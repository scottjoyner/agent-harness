"""Fleet-wide capability probing across every configured backend.

The harness has always been pointed at **one** endpoint per invocation, even though
its endpoint config has held a whole ``endpoints`` map for a long time -- loading it
just picks one and raises if the choice is ambiguous. That is the right default for
a benchmark run and the wrong shape for the question "what can this fleet actually
do", which needs every backend measured the same way before any of them can be
compared.

Three things this module is careful about, each learned the hard way:

* **Reachability is not capability.** A backend that answers ``/v1/models`` may still
  be unable to load the model it advertises. Probing records both separately, so a
  backend that lists 24 models and loads none of them is not mistaken for a working
  one. That is not hypothetical: two nodes on this fleet list models and then abort
  the engine on every auto-load.

* **A backend that did not run is not a backend that failed.** Absent, unreachable
  and exhausted are recorded as themselves and excluded from any comparison, because
  folding "we never asked it" into "it could not do it" is how a fleet report starts
  lying.

* **No composite score.** There is deliberately no single number ranking the fleet.
  The components do not share a scale -- a patch that applies cleanly and a review
  that catches a defect are not commensurable -- so callers read the per-task
  outcomes and the reasons, exactly as ``compare.py`` and ``summarize.py`` do.

Nothing here executes model output. Probing issues a ``GET`` against the models
endpoint and, when asked to attempt tasks, delegates to the ordinary runner so the
per-task evidence and honesty fields are produced by the same code path as a normal
run.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class FleetEndpoint:
    """One backend in the inventory. Never carries a credential."""

    name: str
    base_url: str
    model: str
    label: str = ""
    node: str = ""
    #: How long to wait when asking what the backend has. Kept short on purpose:
    #: a sweep that waits on a dead backend wastes the whole run.
    probe_timeout_s: float = 10.0
    #: How long a *generation* may take. Deliberately not the probe timeout: a
    #: reasoning model legitimately spends over a minute thinking, and conflating
    #: the two made every attempt die at ten seconds with zero model calls, which
    #: read as "these backends cannot do the task".
    timeout_s: float = 180.0
    #: Name of an environment variable holding the credential. The value is read
    #: from the environment and never from the file, so an inventory is safe to
    #: keep next to evidence.
    api_key_env: str = ""
    max_tokens: int = 0
    temperature: float = 0.0
    seed: int = 13

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "base_url": self.base_url,
            "model": self.model,
            "label": self.label,
            "node": self.node,
            "api_key_env": self.api_key_env,
            "max_tokens": self.max_tokens,
            "timeout_s": self.timeout_s,
            "probe_timeout_s": self.probe_timeout_s,
        }


@dataclass
class Probe:
    """What one backend did when asked what it has."""

    name: str
    base_url: str
    model: str
    node: str = ""
    reachable: bool = False
    models: Tuple[str, ...] = ()
    loadable: Optional[bool] = None
    latency_s: float = 0.0
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "base_url": self.base_url,
            "model": self.model,
            "node": self.node,
            "reachable": self.reachable,
            "models": list(self.models),
            "models_advertised": len(self.models),
            # None means "not tested". False means it advertises models and then
            # fails to load one, which is the case a reachability-only check misses.
            "loadable": self.loadable,
            "latency_s": round(self.latency_s, 4),
            "error": self.error,
        }


@dataclass
class FleetReport:
    """A sweep across the inventory, with its own limits stated."""

    probes: List[Probe] = field(default_factory=list)
    attempts: List[Dict[str, Any]] = field(default_factory=list)
    scope_limits: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        reachable = [p for p in self.probes if p.reachable]
        broken = [p for p in self.probes if p.reachable and p.loadable is False]
        unreachable = [p for p in self.probes if not p.reachable]
        return {
            "schema": "realtask.fleet.v1",
            "probes": [p.to_dict() for p in self.probes],
            "attempts": list(self.attempts),
            "summary": {
                "backends": len(self.probes),
                "reachable": len(reachable),
                "unreachable": len(unreachable),
                "reachable_but_failed_to_load": len(broken),
                "tasks_attempted": len(self.attempts),
            },
            "scope_limits": self.scope_limits,
            "composite_score": None,
            "composite_score_note": (
                "intentionally absent. patch application and review quality are "
                "not commensurable, and a fleet that lists models it cannot load "
                "would be ranked by advertisement size otherwise."
            ),
        }

    def write(self, path: Path) -> Path:
        path.write_text(
            json.dumps(self.to_dict(), indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )
        return path


def load_fleet(path: Path) -> List[FleetEndpoint]:
    """Read an endpoint inventory.

    Deliberately the *same* file format ``endpoint_from_config_file`` already
    accepts, rather than a second one: a fleet sweep over an inventory nobody else
    can read is not an inventory. The ``endpoints`` map is used directly, and
    ``default_endpoint`` is ignored because a sweep is the case where picking one is
    exactly wrong.
    """
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    entries: Dict[str, Any]
    if isinstance(payload, dict) and "endpoints" in payload:
        entries = payload["endpoints"]
    elif isinstance(payload, dict):
        entries = {"default": payload}
    else:
        raise ValueError(
            "inventory must be an endpoint config object or {{\"endpoints\": {{...}}}}"
        )
    if not entries:
        raise ValueError("inventory lists no endpoints")

    fleet: List[FleetEndpoint] = []
    for name in sorted(entries):
        entry = entries[name] or {}
        if "base_url" not in entry:
            raise ValueError("endpoint {!r} has no base_url".format(name))
        fleet.append(
            FleetEndpoint(
                name=name,
                base_url=str(entry["base_url"]).rstrip("/"),
                model=str(entry.get("model", "")),
                label=str(entry.get("label", name)),
                node=str(entry.get("node", "")),
                probe_timeout_s=float(entry.get("probe_timeout_s", 10.0)),
                timeout_s=float(entry.get("timeout_s", 180.0)),
                api_key_env=str(entry.get("api_key_env", "")),
                max_tokens=int(entry.get("max_tokens", 0) or 0),
                temperature=float(entry.get("temperature", 0.0)),
                seed=int(entry.get("seed", 13)),
            )
        )
    return fleet


def _normalise(base_url: str) -> str:
    base = base_url.rstrip("/")
    return base + "/models" if base.endswith("/v1") else base + "/v1/models"


def probe_endpoint(
    endpoint: FleetEndpoint,
    opener=urllib.request.urlopen,
    check_loadable: bool = False,
) -> Probe:
    """Ask one backend what it has, and optionally whether it can load a thing.

    ``check_loadable`` issues a one-token completion against the configured model.
    It is off by default because it costs a real generation and, on a backend that
    advertises models but cannot load them, it is the only way to find out.
    """
    probe = Probe(
        name=endpoint.name,
        base_url=endpoint.base_url,
        model=endpoint.model,
        node=endpoint.node,
    )
    started = time.monotonic()
    try:
        with opener(
            _normalise(endpoint.base_url), timeout=endpoint.probe_timeout_s
        ) as response:
            body = json.loads(response.read().decode("utf-8", errors="replace"))
        probe.reachable = True
        probe.models = tuple(
            str(entry.get("id")) for entry in (body.get("data") or []) if entry.get("id")
        )
    except urllib.error.HTTPError as exc:
        probe.error = "HTTP {}: {}".format(exc.code, exc.reason)
    except Exception as exc:  # noqa: BLE001 -- a probe must not raise
        probe.error = "{}: {}".format(type(exc).__name__, exc)
    probe.latency_s = time.monotonic() - started

    if probe.reachable and check_loadable and endpoint.model:
        probe.loadable, probe.error = _check_loadable(endpoint, opener)
    return probe


def _check_loadable(
    endpoint: FleetEndpoint, opener
) -> Tuple[bool, str]:
    """A one-token generation: the only honest test of loadability."""
    payload = json.dumps(
        {
            "model": endpoint.model,
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "hi"}],
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        _normalise(endpoint.base_url).replace("/models", "/chat/completions"),
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with opener(request, timeout=max(endpoint.probe_timeout_s, 60.0)) as response:
            json.loads(response.read().decode("utf-8", errors="replace"))
        return True, ""
    except urllib.error.HTTPError as exc:
        return False, "HTTP {}: {}".format(exc.code, exc.reason)
    except Exception as exc:  # noqa: BLE001
        return False, "{}: {}".format(type(exc).__name__, exc)


def probe_fleet(
    fleet: Sequence[FleetEndpoint],
    opener=urllib.request.urlopen,
    check_loadable: bool = False,
) -> FleetReport:
    """Sweep the inventory and state what the sweep can and cannot support."""
    report = FleetReport(
        probes=[probe_endpoint(e, opener=opener, check_loadable=check_loadable) for e in fleet]
    )
    unreachable = sorted(p.name for p in report.probes if not p.reachable)
    broken = sorted(
        p.name for p in report.probes if p.reachable and p.loadable is False
    )
    untested = sorted(
        p.name
        for p in report.probes
        if p.reachable and p.loadable is None
    )

    limits = [
        "a sweep records what each backend answered when asked. it does not "
        "establish that a backend can attempt any task, and a backend absent "
        "from this report is unknown rather than failed.",
    ]
    if unreachable:
        limits.append(
            "{} backend(s) did not answer and are excluded from any comparison "
            "({}): absence is not a failure.".format(
                len(unreachable), ", ".join(unreachable)
            )
        )
    if broken:
        limits.append(
            "{} backend(s) advertised models and then failed to load one ({}). a "
            "reachability check alone would have counted them as working.".format(
                len(broken), ", ".join(broken)
            )
        )
    if untested and not check_loadable:
        limits.append(
            "loadability was not tested ({} reachable backend(s)); --check-loadable "
            "costs one generation each and is the only way to tell a working "
            "backend from a well-advertising one.".format(len(untested))
        )
    report.scope_limits = limits
    return report

# --------------------------------------------------------------------------
# Driving tasks across the fleet
# --------------------------------------------------------------------------

#: The harness-wide default is 1600 tokens, which every reasoning model observed on
#: this fleet blew through while thinking, producing TRUNCATED results that say
#: nothing about the backend. A fleet sweep exists to compare backends, and a sweep
#: run at the default would compare them on their patience rather than their
#: capability. The value used is recorded in the artifact either way.
FLEET_DEFAULT_MAX_TOKENS = 8192


def eligible_endpoints(
    fleet: Sequence[FleetEndpoint], probes: Sequence[Probe], skip_unloadable: bool = True
) -> Tuple[List[FleetEndpoint], List[str]]:
    """Which backends are worth spending attempts on, and which are not.

    Skipping a backend that cannot load a model is the whole point of having probed
    first. Without it, a sweep spends the majority of its wall clock discovering, one
    backend at a time, what a single ``GET /v1/models`` already said.
    """
    by_name = {p.name: p for p in probes}
    eligible: List[FleetEndpoint] = []
    skipped: List[str] = []
    for endpoint in fleet:
        probe = by_name.get(endpoint.name)
        if probe is None or not probe.reachable:
            skipped.append("{}: did not answer".format(endpoint.name))
            continue
        if skip_unloadable and probe.loadable is False:
            skipped.append(
                "{}: advertises models and cannot load one".format(endpoint.name)
            )
            continue
        if not endpoint.model:
            skipped.append("{}: no model named in the inventory".format(endpoint.name))
            continue
        eligible.append(endpoint)
    return eligible, skipped


def attempt_row(
    endpoint: FleetEndpoint,
    task_id: str,
    outcome: str,
    *,
    calls: int = 0,
    wall_s: float = 0.0,
    tokens: Optional[int] = None,
    budget_bound: Optional[bool] = None,
    targeted_passed: Optional[bool] = None,
    evidence: str = "",
    error: str = "",
) -> Dict[str, Any]:
    """One (backend, task) cell.

    Carries the same honesty fields a normal attempt records -- ``budget_bound``
    above all, so a backend that ran out of room is not counted as a backend that
    could not do the task.
    """
    return {
        "backend": endpoint.name,
        "node": endpoint.node,
        "base_url": endpoint.base_url,
        "model": endpoint.model,
        "task_id": task_id,
        "outcome": outcome,
        "model_calls": calls,
        "total_wall_s": round(wall_s, 3),
        "total_tokens": tokens,
        "budget_bound": budget_bound,
        "targeted_passed": targeted_passed,
        "evidence": evidence,
        "error": error,
    }


def attempt_fleet(
    fleet: Sequence[FleetEndpoint],
    task_ids: Sequence[str],
    executor,
    *,
    stages: Sequence[str] = ("single",),
    skip_unloadable: bool = True,
) -> FleetReport:
    """Drive every eligible backend against every task and record the outcome.

    ``executor`` is ``(endpoint, task_id) -> dict`` returning any of the keys
    :func:`attempt_row` documents. Injecting it keeps this orchestration free of
    model calls and therefore testable without an endpoint, and it guarantees the
    fleet path cannot quietly grow its own idea of how a run works: the caller
    supplies one that delegates to the ordinary runner.
    """
    report = FleetReport()

    for endpoint in fleet:
        for task_id in task_ids:
            try:
                result = executor(endpoint, task_id) or {}
            except Exception as exc:  # noqa: BLE001 -- one backend must not end the sweep
                result = {"outcome": "HARNESS_ERROR", "error": "{}: {}".format(
                    type(exc).__name__, exc)}
            row = attempt_row(
                endpoint,
                task_id,
                str(result.get("outcome") or "UNKNOWN"),
                calls=int(result.get("model_calls") or 0),
                wall_s=float(result.get("total_wall_s") or 0.0),
                tokens=result.get("total_tokens"),
                budget_bound=result.get("budget_bound"),
                targeted_passed=result.get("targeted_passed"),
                evidence=str(result.get("evidence") or ""),
                error=str(result.get("error") or ""),
            )
            report.attempts.append(row)

    return _attempt_limits(report, tuple(stages))


def _attempt_limits(report: FleetReport, stages: Tuple[str, ...]) -> FleetReport:
    limits = [
        "a fleet sweep compares backends only where the same task ran on both. a "
        "cell that was never attempted is absent from the comparison, not "
        "counted as a failure.",
        "budget_bound distinguishes a backend that ran out of completion room "
        "from one that could not do the task. only {} stage(s) were driven ({}), "
        "so nothing here says anything about the other strategies.".format(
            len(stages), ", ".join(stages)
        ),
        "one attempt per (backend, task) is a sample, not a measurement. these "
        "rows carry no replication, and a backend that succeeded once here may "
        "succeed unreliably.",
    ]
    if any(r.get("budget_bound") for r in report.attempts):
        limits.append(
            "{} cell(s) were budget-bound. those are results about the completion "
            "budget, not about the backend's capability.".format(
                sum(1 for r in report.attempts if r.get("budget_bound"))
            )
        )
    report.scope_limits = limits
    return report


#: Notes every attempt carries regardless of what happened. Not diagnostics.
_BOILERPLATE_NOTE_PREFIXES = (
    "evaluation work root moved out of",
)


def _first_real_note(notes: Sequence[str]) -> str:
    for note in notes or ():
        text = str(note).strip()
        if not text:
            continue
        if any(text.startswith(prefix) for prefix in _BOILERPLATE_NOTE_PREFIXES):
            continue
        return text[:400]
    return ""


def make_runner_executor(
    tasks_root: Path,
    out_dir: Path,
    harness_root: Path,
    options,
    *,
    stages: Sequence[str] = ("single",),
    single_attempts: int = 1,
    swarm_attempts: int = 1,
    max_tokens_default: int = FLEET_DEFAULT_MAX_TOKENS,
):
    """Build the executor ``attempt_fleet`` calls, backed by the ordinary runner.

    Every cell goes through ``BenchmarkRunner.run_task`` exactly as a single-backend
    run does, so per-task evidence, the grounding gate, the honesty fields and the
    acceptance commands are produced by the same code path. The fleet layer decides
    only *what to run and where to put it*; it never decides what a result means.

    Each backend gets its own evidence subdirectory, because a fleet that overwrites
    one backend's evidence with another's is worse than no fleet.
    """
    import os
    import sys

    from realtask.adapter import EndpointConfig, OpenAIChatAdapter
    from realtask.evidence import RunDirectory
    from realtask.fixtures import load_task_by_id
    from realtask.runner import BenchmarkRunner

    max_tokens_default = int(max_tokens_default)

    def executor(endpoint: FleetEndpoint, task_id: str) -> Dict[str, Any]:
        task = load_task_by_id(task_id, Path(tasks_root))
        config = EndpointConfig(
            label=endpoint.label or endpoint.name,
            base_url=endpoint.base_url,
            model=endpoint.model,
            api_key=os.environ.get(endpoint.api_key_env) if endpoint.api_key_env else None,
            timeout_s=endpoint.timeout_s,
            temperature=endpoint.temperature,
            seed=endpoint.seed,
            max_tokens=endpoint.max_tokens or max_tokens_default,
            stream=True,
            node=endpoint.node,
        )
        adapter = OpenAIChatAdapter(config)
        run_dir = RunDirectory(
            out_dir, "fleet", prefix="backends/{}/".format(endpoint.name)
        )
        run_dir.copy_fixture(task.task_path, task.source_manifest_path)
        work_root = Path(out_dir) / ("fleet-{}.work".format(endpoint.name))
        runner = BenchmarkRunner(
            adapter, run_dir, options, work_root=work_root, harness_root=harness_root
        )
        try:
            result = runner.run_task(
                task, list(stages),
                single_attempts=single_attempts,
                swarm_attempts=swarm_attempts,
            )
        finally:
            runner.close()
        if not result.attempts:
            return {"outcome": "NO_ATTEMPT", "error": "runner produced no attempt"}
        best = result.attempts[-1]
        metrics = best.metrics
        targeted = metrics.tests.targeted
        return {
            "outcome": metrics.outcome.value,
            "model_calls": metrics.model_calls,
            "total_wall_s": metrics.total_wall_s,
            "total_tokens": metrics.total_tokens,
            "budget_bound": metrics.budget_bound,
            "targeted_passed": (
                bool(targeted) and all(c.passed for c in targeted)
            ) if targeted else None,
            "evidence": str(Path(out_dir) / "fleet" / "backends" / endpoint.name),
            # The first note on every attempt is the work-root relocation notice,
            # so notes were surfacing as boilerplate. Blanking them entirely was
            # worse: it threw away the only diagnostic that matters, which is why
            # the *first non-boilerplate* note is kept instead.
            "error": _first_real_note(metrics.notes),
        }

    return executor

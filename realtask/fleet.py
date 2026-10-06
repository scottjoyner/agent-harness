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
    timeout_s: float = 10.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "base_url": self.base_url,
            "model": self.model,
            "label": self.label,
            "node": self.node,
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
                timeout_s=float(entry.get("probe_timeout_s", 10.0)),
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
        with opener(_normalise(endpoint.base_url), timeout=endpoint.timeout_s) as response:
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
        with opener(request, timeout=max(endpoint.timeout_s, 60.0)) as response:
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
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path

from realtask.fleet import (
    FleetEndpoint, load_fleet, probe_endpoint, probe_fleet, _normalise,
)


class _Resp:
    def __init__(self, payload):
        self._raw = json.dumps(payload).encode()

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fail(code):
    def opener(*a, **k):
        raise urllib.error.HTTPError(a[0], code, "nope", {}, None)
    return opener


def _ok(payloads):
    seq = list(payloads)

    def opener(*a, **k):
        return _Resp(seq.pop(0))
    return opener


class _Tmp(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.tmp = type('T', (), {'name': self._d.name})()
        self.addCleanup(self._d.cleanup)


class InventoryTests(_Tmp):
    def test_it_reads_the_endpoints_map(self):
        p = Path(self.tmp.name) / "f.json"
        p.write_text(json.dumps({"endpoints": {
            "b": {"base_url": "http://b:1234/v1", "model": "mb", "node": "bee"},
            "a": {"base_url": "http://a:1234/v1/", "model": "ma"},
        }}))
        fleet = load_fleet(p)
        self.assertEqual([e.name for e in fleet], ["a", "b"])
        self.assertEqual(fleet[1].node, "bee")

    def test_default_endpoint_is_ignored_because_a_sweep_picks_neither(self):
        p = Path(self.tmp.name) / "f.json"
        p.write_text(json.dumps({
            "default_endpoint": "a",
            "endpoints": {
                "a": {"base_url": "http://a/v1", "model": "m"},
                "b": {"base_url": "http://b/v1", "model": "m"},
            },
        }))
        self.assertEqual(len(load_fleet(p)), 2)

    def test_a_bare_object_is_accepted(self):
        p = Path(self.tmp.name) / "f.json"
        p.write_text(json.dumps({"base_url": "http://a/v1", "model": "m"}))
        self.assertEqual(len(load_fleet(p)), 1)

    def test_an_endpoint_without_a_base_url_is_rejected(self):
        p = Path(self.tmp.name) / "f.json"
        p.write_text(json.dumps({"endpoints": {"a": {"model": "m"}}}))
        with self.assertRaises(ValueError):
            load_fleet(p)

    def test_an_empty_inventory_is_rejected(self):
        p = Path(self.tmp.name) / "f.json"
        p.write_text(json.dumps({"endpoints": {}}))
        with self.assertRaises(ValueError):
            load_fleet(p)

    def test_urls_are_normalised_to_the_models_path(self):
        self.assertEqual(_normalise("http://h:1234/v1"), "http://h:1234/v1/models")
        self.assertEqual(_normalise("http://h:1234"), "http://h:1234/v1/models")


class ProbeTests(_Tmp):
    def test_a_healthy_backend_lists_its_models(self):
        probe = probe_endpoint(
            FleetEndpoint("a", "http://h:1/v1", "m"),
            opener=_ok([{"data": [{"id": "m1"}, {"id": "m2"}]}]),
        )
        self.assertTrue(probe.reachable)
        self.assertEqual(probe.models, ("m1", "m2"))
        self.assertIsNone(probe.loadable)

    def test_an_unreachable_backend_does_not_raise(self):
        probe = probe_endpoint(
            FleetEndpoint("a", "http://h:1/v1", "m"), opener=_fail(500)
        )
        self.assertFalse(probe.reachable)
        self.assertIn("HTTP 500", probe.error)
        self.assertEqual(probe.models, ())

    def test_reachability_alone_would_miss_a_backend_that_cannot_load(self):
        """The case that motivated --check-loadable."""
        probe = probe_endpoint(
            FleetEndpoint("a", "http://h:1/v1", "m"), opener=_fail(503)
        )
        self.assertFalse(probe.reachable)
        # An endpoint that answers /v1/models but aborts the engine on load is
        # exactly what this must catch:
        seq = [{"data": [{"id": "m"}]}]
        calls = []

        def opener(req, timeout=None):
            calls.append(req)
            if len(calls) == 1:
                return _Resp(seq[0])
            raise urllib.error.HTTPError("u", 400, "engine exited SIGABRT", {}, None)

        probe2 = probe_endpoint(
            FleetEndpoint("a", "http://h:1/v1", "m"), opener=opener, check_loadable=True
        )
        self.assertTrue(probe2.reachable, "it advertises models")
        self.assertFalse(probe2.loadable, "but cannot load one")
        self.assertIn("SIGABRT", probe2.error)


class FleetReportTests(_Tmp):
    def test_absent_is_not_recorded_as_failed(self):
        fleet = [
            FleetEndpoint("up", "http://up/v1", "m"),
            FleetEndpoint("down", "http://down/v1", "m"),
        ]
        calls = {"n": 0}

        def opener(*a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                return _Resp({"data": [{"id": "m"}]})
            raise urllib.error.HTTPError("u", 500, "down", {}, None)

        report = probe_fleet(fleet, opener=opener)
        payload = report.to_dict()
        self.assertEqual(payload["summary"]["reachable"], 1)
        self.assertEqual(payload["summary"]["unreachable"], 1)
        joined = " ".join(payload["scope_limits"])
        self.assertIn("absence is not a failure", joined)
        self.assertIn("down", joined)

    def test_it_emits_no_composite_score(self):
        fleet = [FleetEndpoint("a", "http://a/v1", "m")]
        report = probe_fleet(fleet, opener=_ok([{"data": [{"id": "m"}]}]))
        payload = report.to_dict()
        self.assertIsNone(payload["composite_score"])
        blob = json.dumps(payload)
        for banned in ("pass_rate", "overall_score", "fleet_rank", "\"verdict\":"):
            self.assertNotIn(banned, blob, banned)

    def test_it_says_when_loadability_was_not_tested(self):
        fleet = [FleetEndpoint("a", "http://a/v1", "m")]
        joined = " ".join(probe_fleet(fleet, opener=_ok([{"data": []}])).scope_limits)
        self.assertIn("loadability was not tested", joined)

    def test_no_credentials_are_ever_carried(self):
        p = Path(self.tmp.name) / "f.json"
        p.write_text(json.dumps({"endpoints": {
            "a": {"base_url": "http://a/v1", "model": "m", "api_key": "sk-secret-value"},
        }}))
        blob = json.dumps(probe_fleet(load_fleet(p), opener=_fail(500)).to_dict())
        self.assertNotIn("sk-secret-value", blob)


if __name__ == "__main__":
    unittest.main()


class FleetCliTests(_Tmp):
    """The subcommand is the capability; a module nothing invokes is a script."""

    def test_fleet_writes_a_report_and_prints_a_table(self):
        import contextlib
        import io
        import realtime_bench

        inventory = Path(self.tmp.name) / "f.json"
        inventory.write_text(json.dumps({"endpoints": {
            "up": {"base_url": "http://up/v1", "model": "m"},
            "down": {"base_url": "http://down/v1", "model": "m"},
        }}))

        class _Args:
            endpoint_config = str(inventory)
            check_loadable = False
            json = False
            out = self.tmp.name
            tasks_root = str(Path("realtask/tasks"))

        calls = {"n": 0}

        def opener(*a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                return _Resp({"data": [{"id": "m"}]})
            raise urllib.error.HTTPError("u", 500, "down", {}, None)

        import realtask.fleet as fleet_module

        buffer = io.StringIO()
        original = fleet_module.probe_fleet
        fleet_module.probe_fleet = lambda f, **kw: original(f, opener=opener, **kw)
        try:
            with contextlib.redirect_stdout(buffer):
                rc = realtime_bench.main(
                    ["fleet", "--endpoint-config", str(inventory),
                     "--out", self.tmp.name]
                )
        finally:
            fleet_module.probe_fleet = original

        self.assertEqual(rc, 0)
        out = buffer.getvalue()
        self.assertIn("backend", out)
        self.assertIn("reachable", out)
        report = json.loads(
            (Path(self.tmp.name) / "fleet.json").read_text(encoding="utf-8")
        )
        self.assertEqual(report["schema"], "realtask.fleet.v1")
        self.assertEqual(report["summary"]["backends"], 2)
        self.assertEqual(report["summary"]["reachable"], 1)
        self.assertEqual(report["summary"]["unreachable"], 1)


class FleetAttemptTests(_Tmp):
    """What each backend can *do*, as opposed to what it advertises."""

    def _fleet(self):
        return [
            FleetEndpoint("good", "http://good/v1", "m1", node="g"),
            FleetEndpoint("unloadable", "http://bad/v1", "m2", node="b"),
            FleetEndpoint("down", "http://down/v1", "m3", node="d"),
            FleetEndpoint("nomodel", "http://nm/v1", "", node="n"),
        ]

    def _probes(self):
        return probe_fleet(self._fleet(), opener=lambda *a, **k: _Resp(
            {"data": [{"id": "m"}]}
        ), check_loadable=False).probes

    def test_a_backend_that_cannot_load_is_not_worth_attempting(self):
        """The reason the sweep probes first. Without this the sweep spends most
        of its wall clock rediscovering, one backend at a time, what a single
        GET /v1/models already said."""
        from realtask.fleet import eligible_endpoints

        fleet = self._fleet()
        probes = probe_fleet(
            fleet,
            opener=lambda *a, **k: _Resp({"data": [{"id": "m"}]}),
            check_loadable=True,
        ).probes
        # make "unloadable" genuinely fail its load check
        by = {p.name: p for p in probes}
        by["unloadable"].loadable = False
        eligible, skipped = eligible_endpoints(fleet, probes)
        # 'unloadable' is excluded; 'nomodel' has nothing to attempt against.
        self.assertEqual([e.name for e in eligible], ["good", "down"])
        joined = " ".join(skipped)
        self.assertIn("cannot load one", joined)
        self.assertIn("no model named", joined)
        self.assertNotIn("unloadable: did not answer", joined)

    def test_a_backend_with_no_model_named_is_skipped(self):
        from realtask.fleet import eligible_endpoints

        fleet = self._fleet()
        probes = probe_fleet(
            fleet, opener=lambda *a, **k: _Resp({"data": [{"id": "m"}]})
        ).probes
        for p in probes:
            p.loadable = True
        eligible, skipped = eligible_endpoints(fleet, probes)
        names = [e.name for e in eligible]
        self.assertIn("good", names)
        self.assertNotIn("nomodel", names)
        self.assertTrue(any("no model named" in s for s in skipped))

    def test_each_cell_is_recorded_and_carries_the_honesty_fields(self):
        from realtask.fleet import attempt_fleet

        fleet = [FleetEndpoint("b1", "http://b1/v1", "m"),
                 FleetEndpoint("b2", "http://b2/v1", "m")]

        def executor(endpoint, task_id):
            if endpoint.name == "b2":
                return {"outcome": "TRUNCATED", "model_calls": 1,
                        "total_wall_s": 5.0, "total_tokens": 8192,
                        "budget_bound": True, "targeted_passed": False}
            return {"outcome": "SUCCESS", "model_calls": 1, "total_wall_s": 2.0,
                    "total_tokens": 900, "targeted_passed": True,
                    "evidence": "/tmp/b1"}

        report = attempt_fleet(fleet, ["t1", "t2"], executor)
        self.assertEqual(len(report.attempts), 4)
        by_cell = {(r["backend"], r["task_id"]): r for r in report.attempts}
        self.assertEqual(by_cell[("b1", "t1")]["outcome"], "SUCCESS")
        self.assertTrue(by_cell[("b2", "t1")]["budget_bound"])
        self.assertFalse(by_cell[("b2", "t1")]["targeted_passed"])

    def test_one_backend_failing_does_not_end_the_sweep(self):
        from realtask.fleet import attempt_fleet

        def executor(endpoint, task_id):
            if endpoint.name == "boom":
                raise RuntimeError("connection reset")
            return {"outcome": "SUCCESS"}

        report = attempt_fleet(
            [FleetEndpoint("boom", "http://b/v1", "m"),
             FleetEndpoint("ok", "http://o/v1", "m")],
            ["t1"], executor,
        )
        outcomes = {r["backend"]: r["outcome"] for r in report.attempts}
        self.assertEqual(outcomes["boom"], "HARNESS_ERROR")
        self.assertIn("connection reset", [r["error"] for r in report.attempts
                                           if r["backend"] == "boom"][0])
        self.assertEqual(outcomes["ok"], "SUCCESS")

    def test_it_states_that_one_attempt_is_a_sample(self):
        from realtask.fleet import attempt_fleet

        report = attempt_fleet(
            [FleetEndpoint("b", "http://b/v1", "m")], ["t1"],
            lambda e, t: {"outcome": "SUCCESS"},
        )
        joined = " ".join(report.scope_limits)
        self.assertIn("a sample, not a measurement", joined)
        self.assertIn("no replication", joined)

    def test_budget_bound_cells_are_called_out_in_the_limits(self):
        from realtask.fleet import attempt_fleet

        report = attempt_fleet(
            [FleetEndpoint("b", "http://b/v1", "m")], ["t1"],
            lambda e, t: {"outcome": "TRUNCATED", "budget_bound": True},
        )
        joined = " ".join(report.scope_limits)
        self.assertIn("budget-bound", joined)
        self.assertIn("not about the backend's capability", joined)

    def test_the_report_emits_no_composite_score_after_attempting(self):
        from realtask.fleet import attempt_fleet

        payload = attempt_fleet(
            [FleetEndpoint("b", "http://b/v1", "m")], ["t1"],
            lambda e, t: {"outcome": "SUCCESS"},
        ).to_dict()
        self.assertIsNone(payload["composite_score"])
        self.assertEqual(payload["summary"]["tasks_attempted"], 1)
        blob = json.dumps(payload)
        for banned in ("pass_rate", "overall_score", "fleet_rank", "\"verdict\":",
                       "better_than"):
            self.assertNotIn(banned, blob, banned)


class FleetDiagnosticTests(_Tmp):
    """A cell must say why it could not run, in the sweep itself.

    Found on the first real sweep: two remote backends returned
    ``request (8200 tokens) exceeds the available context size``, which is a
    capability finding about the backend. Reporting those cells as bare
    ``PROTOCOL_FAILURE`` would have discarded the only sentence that explains it.
    """

    def test_the_first_non_boilerplate_note_is_kept(self):
        from realtask.fleet import _first_real_note

        notes = [
            "evaluation work root moved out of /tmp/x because it was inside "
            "the read-only tree",
            "single call failed: endpoint rejected the request: HTTP 400: "
            "request (8200 tokens) exceeds the available context size (8192)",
        ]
        kept = _first_real_note(notes)
        self.assertIn("exceeds the available context size", kept)
        self.assertNotIn("read-only tree", kept)

    def test_boilerplate_only_yields_no_error(self):
        from realtask.fleet import _first_real_note

        self.assertEqual(
            _first_real_note(["evaluation work root moved out of /tmp/x"]), ""
        )
        self.assertEqual(_first_real_note([]), "")
        self.assertEqual(_first_real_note(["   "]), "")


class LoadHealthTests(_Tmp):
    """A load failure is not one thing, and reporting it as one thing is wrong.

    This exists because a sweep got it wrong. It reported three healthy backends as
    "advertises models and cannot load one":

      x1-370   a transient ROCm allocator wedge; the same model loaded in 3.97s
               minutes later
      xwing    an active finetune job holding 13.4 GB of VRAM, plus another
               agent's twelve concurrent load attempts, all hung on the same memory
      beelink  a healthy 13 GB CPU-only node that answers fine on the 1.2B model it
               serves -- the 12B the inventory named simply does not fit

    None of those is a broken backend. The fix is to ask a question the probe can
    actually answer -- *can this backend load anything at all right now* -- by naming
    a small ``probe_model``, and to stop claiming a distinction one observation
    cannot make. "now" is in the verdict name deliberately.
    """

    def _advertising(self):
        return {"data": [{"id": "big"}, {"id": "small"}]}

    def _opener(self, healthy):
        def opener(req, timeout=None):
            if not hasattr(req, "data"):
                return _Resp(self._advertising())
            if healthy:
                return _Resp({"choices": [{"message": {"content": "ok"}}]})
            raise urllib.error.HTTPError("u", 400, "SIGABRT", {}, None)
        return opener

    def test_a_healthy_backend_is_ok(self):
        probe = probe_endpoint(
            FleetEndpoint("b", "http://h/v1", "m", probe_model="small"),
            opener=self._opener(True), check_loadable=True,
        )
        self.assertTrue(probe.loadable)
        self.assertEqual(probe.health, "ok")
        self.assertEqual(probe.probed_model, "small")

    def test_the_probe_asks_the_probe_model_not_the_work_model(self):
        """Otherwise a too-big work model masquerades as a broken backend."""
        seen = []

        def opener(req, timeout=None):
            if not hasattr(req, "data"):
                return _Resp(self._advertising())
            seen.append(req.data.decode())
            return _Resp({"choices": [{"message": {"content": "ok"}}]})

        probe_endpoint(
            FleetEndpoint("b", "http://h/v1", "google/gemma-4-12b-qat",
                          probe_model="liquid/lfm2.5-1.2b"),
            opener=opener, check_loadable=True,
        )
        self.assertTrue(any("liquid/lfm2.5-1.2b" in s for s in seen))
        self.assertFalse(any("gemma-4-12b" in s for s in seen))

    def test_a_backend_that_cannot_load_even_a_small_model(self):
        probe = probe_endpoint(
            FleetEndpoint("b", "http://h/v1", "m", probe_model="small"),
            opener=self._opener(False), check_loadable=True,
        )
        self.assertFalse(probe.loadable)
        self.assertEqual(probe.health, "backend_unavailable_now")

    def test_health_is_unknown_when_not_tested(self):
        probe = probe_endpoint(
            FleetEndpoint("b", "http://h/v1", "m"), opener=self._opener(True)
        )
        self.assertEqual(probe.health, "unknown")
        self.assertIsNone(probe.loadable)

    def test_the_limit_refuses_to_call_a_busy_node_broken(self):
        fleet = [FleetEndpoint("b", "http://h/v1", "m", probe_model="small")]
        report = probe_fleet(fleet, opener=self._opener(False), check_loadable=True)
        joined = " ".join(report.scope_limits)
        self.assertIn("single observation", joined)
        self.assertIn("does not separate a broken engine from a busy node", joined)
        self.assertIn("re-probe", joined)

    def test_a_healthy_backend_that_cannot_run_its_work_model_is_called_out(self):
        """The beelink case, at the attempt layer rather than the probe layer."""
        from realtask.fleet import attempt_fleet

        def executor(endpoint, task_id):
            return {"outcome": "PROTOCOL_FAILURE",
                    "error": "Failed to load model SIGABRT"}

        report = attempt_fleet(
            [FleetEndpoint("beelink", "http://b/v1", "google/gemma-4-12b-qat")],
            ["t1"], executor, health_by_backend={"beelink": "ok"},
        )
        cell = report.attempts[0]
        self.assertEqual(cell["backend_health"], "ok")
        joined = " ".join(report.scope_limits)
        self.assertIn("healthy but could not run the model named for them", joined)
        self.assertIn("the capability limit there is the model's", joined)

    def test_an_inventory_can_name_a_probe_model(self):
        p = Path(self.tmp.name) / "f.json"
        p.write_text(json.dumps({"endpoints": {
            "b": {"base_url": "http://h/v1", "model": "big",
                  "probe_model": "small", "max_tokens": 8192, "timeout_s": 240},
        }}))
        endpoint = load_fleet(p)[0]
        self.assertEqual(endpoint.probe_model, "small")
        self.assertEqual(endpoint.max_tokens, 8192)
        self.assertEqual(endpoint.timeout_s, 240)
        self.assertEqual(endpoint.to_dict()["probe_model"], "small")

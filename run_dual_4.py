#!/usr/bin/env python3
"""Dual-harness retry of iter3 fail set: BM-007/008/014/018 via plan->execute->verify."""
import json, sys
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from terminal_bench_harness import TerminalBenchHarness

TASKS = json.loads(Path(__file__).parent.joinpath("benchmarks/baremetal/tasks.json").read_text())["tasks"]
WANT = {"BM-007", "BM-008", "BM-014", "BM-018"}

def file_check(task):
    v = task.get("verification", {})
    p = Path(v.get("path", "")) if v else None
    if not p:
        return None
    if v.get("type") == "file_exists":
        ok = p.exists() and p.stat().st_size >= v.get("min_size_bytes", 0)
        return {"exists": p.exists(), "size": p.stat().st_size if p.exists() else 0, "ok": ok}
    return None

def main():
    h = TerminalBenchHarness(executor_port=1235, reasoner_port=1234)
    out = []
    for t in [x for x in TASKS if x["id"] in WANT]:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {t['id']} start", flush=True)
        r = h.run(t["id"], t["instruction"])
        fc = file_check(t)
        print(f"  verdict={'PASS' if r.success else 'FAIL'} filecheck={fc}", flush=True)
        out.append({"id": t["id"], "verdict": r.success, "tokens": r.total_tokens,
                    "time": round(r.total_time, 1), "filecheck": fc})
    passed = sum(1 for r in out if r["verdict"] and (not r["filecheck"] or r["filecheck"]["ok"]))
    print(f"DUAL RESULT: {passed}/{len(out)}")
    Path(f"dual_4_{datetime.now().strftime('%H%M%S')}.json").write_text(
        json.dumps({"ts": datetime.now().isoformat(), "passed": passed, "results": out}, indent=2))
    print(f"passed={passed}")

if __name__ == "__main__":
    main()

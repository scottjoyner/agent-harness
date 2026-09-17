#!/usr/bin/env python3
"""fixgit-repro-v1 (local, deterministic, ALWAYS emits results.json)

The official terminal-bench-core runner frequently terminates without writing
results.json (iter8/iter9 fix-git runs both died with commands.txt only). This
repro exists so fix-git passes/fails are UNBLOCKED from that harness bug: it
stages the SAME lost-commit scenario, runs the CPM agent through the same
cp_tb2_bench.py bridge (:1235), enforces the same 60s tool budget, and
ALWAYS writes results.json (pass/fail) + probe.jsonl regardless of how the
session ends.

Single assertion that makes the suite valuable: 'recovery branch exists AND
master contains its commit'. Everything in staging here is repeatable/deterministic.

Usage:  python3 fixgit_repro_v1.py   (runs against a fresh tmp repo, cwd=tempdir)
"""
import argparse, json, os, shutil, subprocess, sys, tempfile, time
from pathlib import Path
from contextlib import contextmanager
import math
import signal


def remaining(deadline):
    budget = deadline - time.monotonic()
    if budget <= 0:
        raise TimeoutError("Run deadline exceeded")
    return min(25.0, budget)


@contextmanager
def request_budget(deadline):
    budget = remaining(deadline)
    def expired(signum, frame):
        raise TimeoutError("HTTP request deadline exceeded")
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    started = time.monotonic()
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, budget)
    try:
        yield budget
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0]:
            signal.setitimer(signal.ITIMER_REAL,
                             max(0.000001, previous_timer[0] - (time.monotonic() - started)),
                             previous_timer[1])


def run_process(command, deadline, check=False, **kwargs):
    budget = remaining(deadline)
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, start_new_session=True, **kwargs)
    try:
        stdout, stderr = process.communicate(timeout=budget)
    except BaseException as error:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            stdout, stderr = process.communicate(timeout=1)
        except subprocess.TimeoutExpired:
            process.stdout.close()
            process.stderr.close()
            raise error
        if isinstance(error, subprocess.TimeoutExpired):
            error.output, error.stderr = stdout, stderr
        raise
    result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    if check:
        result.check_returncode()
    return result

def write_trace(path, traces):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".probe-", delete=False) as stream:
            temporary = Path(stream.name)
            for entry in traces:
                stream.write(json.dumps(entry) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def recovery_state(stage_root, lost, deadline, git_env):
    def git(*args):
        return run_process(["git", *args], deadline, cwd=str(stage_root), env=git_env)

    branch_exists = git("show-ref", "--verify", "--quiet",
                        "refs/heads/recovery-branch").returncode == 0
    master_contains = git("merge-base", "--is-ancestor", lost, "master").returncode == 0
    branch_contains = branch_exists and git(
        "merge-base", "--is-ancestor", lost, "refs/heads/recovery-branch"
    ).returncode == 0
    return branch_exists, master_contains, branch_contains


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--endpoint", default="http://localhost:1235/v1/chat/completions")
    ap.add_argument("--timeout-s", type=float, default=60)
    ap.add_argument("--model", default="minicpm5-2b-iter14",
                    help="model id sent in the chat request (server alias)")
    ap.add_argument("--max-steps", type=int, default=14)
    ap.add_argument("--out", default="results.json")
    ap.add_argument("--root", default=None, help="override repo root (default fresh tmp repo)")
    ap.add_argument("--tool-mode", choices=["xml", "native"], default="xml",
                    help="xml: MiniCPM-style <function> text contract; "
                         "native: OpenAI tools array (server parses tool_calls)")
    ap.add_argument("--disable-thinking", action="store_true",
                    help="send chat_template_kwargs.enable_thinking=false")
    a = ap.parse_args()
    if not math.isfinite(a.timeout_s) or a.timeout_s <= 0:
        ap.error("--timeout-s must be positive and finite")
    started = time.monotonic()
    deadline = started + a.timeout_s

    out_p = Path(a.out)
    os.makedirs(out_p.parent if out_p.parent != Path(".") else ".", exist_ok=True)
    stage_root = Path(a.root) if a.root else Path(tempfile.mkdtemp("fixgit-stage"))
    try:
        passed = run_scenario(a, stage_root, out_p, deadline)
    except Exception as e:
        passed = False
        result = {
            "version": "fixgit-repro-v1",
            "task": "fix-git-lost-commit",
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "model": a.model,
            "endpoint": a.endpoint,
            "passed": False,
            "elapsed_s": round(time.monotonic() - started, 1),
            "deadline_exceeded": time.monotonic() >= deadline,
            "steps": 0,
            "emit": "always",
            "harness_error": f"{type(e).__name__}: {e}",
            "signal": {},
            "probe_jsonl": "probe.jsonl",
        }
        out_p.write_text(json.dumps(result, indent=2) + "\n")
        (out_p.parent / "probe.jsonl").write_text("")
        print(json.dumps(result, indent=2))
    finally:
        if not a.root:
            shutil.rmtree(stage_root, ignore_errors=True)
    return 0 if passed else 1


def run_scenario(a, stage_root, out_p, deadline):
    git_env = dict(os.environ, GIT_AUTHOR_NAME="Harness",
                   GIT_AUTHOR_EMAIL="harness@localhost", GIT_COMMITTER_NAME="Harness",
                   GIT_COMMITTER_EMAIL="harness@localhost")
    git = lambda *x, cwd=str(stage_root): run_process(
        ["git", *x], deadline, check=True, env=git_env, cwd=cwd)
    git("init", "-b", "master")
    (stage_root / "index.html").write_text("<h1>old</h1>\n")
    git("add", "index.html")
    git("-c", "user.email=h@l", "-c", "user.name=h", "commit", "-m", "Initial")
    git("checkout", "--detach", "HEAD")
    (stage_root / "index.html").write_text("<h1>new work</h1>\n")
    git("-c", "user.email=h@l", "-c", "user.name=h", "commit", "-am", "Feature work")  # SHALL become the lost commit
    lost = git("rev-parse", "HEAD").stdout.strip()
    git("checkout", "master")

    task = (
        "I was working on a personal site in a detached HEAD. I made my changes and "
        "committed them, then ran `git checkout master`. Now git status and git diff show "
        "nothing and I can't find my work. Help me recover the lost commit and merge it "
        "onto master. Tooling contract: every response must be exactly one "
        '<function name="bash"><param name="command">...</param></function> tool call.'
    )

    if a.tool_mode == "native":
        task = task.split("Tooling contract:")[0] + (
            "Create a branch named recovery-branch at the lost commit, then merge "
            "it onto master. Use the bash tool, one call per turn."
        )

    # ── 2. RUN the agent through the same bridge contract ──────────────────
    msgs = [{"role": "user", "content": task}]
    calls = []
    traces = []
    harness_error = None
    tool_calls = 0
    tool_timeouts = 0
    branch_exists = master_contains = branch_contains = False
    t0 = deadline - a.timeout_s
    try:
        import urllib.request
        step = 0
        while time.monotonic() < deadline and step < a.max_steps:
            body = {"model": a.model, "messages": msgs, "max_tokens": 512}
            if a.tool_mode == "native":
                body["tools"] = [{
                    "type": "function",
                    "function": {
                        "name": "bash",
                        "description": "Run one bash command in the repo and "
                                       "return stdout/stderr plus exit code.",
                        "parameters": {
                            "type": "object",
                            "properties": {"command": {"type": "string"}},
                            "required": ["command"],
                        },
                    },
                }]
            if a.disable_thinking:
                body["chat_template_kwargs"] = {"enable_thinking": False}
            request_snapshot = json.loads(json.dumps(body))
            out = None
            cmd = None
            request_error = None
            try:
                with request_budget(deadline):
                    req = urllib.request.Request(a.endpoint, data=json.dumps(body).encode(),
                                                 headers={"Content-Type": "application/json"})
                    with urllib.request.urlopen(req) as r:
                        out = json.loads(r.read())
                msg = out["choices"][0]["message"]
                cont = msg.get("content", "") or ""
                ntc = msg.get("tool_calls") or []
                cmd = None
                if a.tool_mode == "native" and ntc:
                    if len(ntc) != 1:
                        raise ValueError("Expected exactly one native tool call")
                    tc = ntc[0]
                    fn = tc.get("function", {})
                    if fn.get("name") != "bash" or not tc.get("id"):
                        raise ValueError("Expected bash tool call with an id")
                    args = fn.get("arguments")
                    args = json.loads(args) if isinstance(args, str) else args
                    if not isinstance(args, dict):
                        raise ValueError("Tool arguments must be an object")
                    cmd = args.get("command")
                    if not isinstance(cmd, str) or not cmd.strip():
                        raise ValueError("Tool command must be a nonempty string")
                    cont = json.dumps(msg)
            except Exception as e:
                request_error = f"{type(e).__name__}: {e}"
                cont = f"//ExitEmpty//{e}"
            calls.append(cont)
            traces.append({"cont": cont, "request": request_snapshot, "response": out,
                           "command": cmd, "error": request_error})
            write_trace(out_p.parent / "probe.jsonl", traces)
            if not cont or cont.startswith("//ExitEmpty//"):
                msgs.append({"role": "assistant", "content": cont})
                msgs.append({"role": "user", "content": "Tool result rc=0 (no output). Not done. Next single bash tool call that finds the lost commit and merges it:"})
                step += 1
                continue
            # Parse ONE bash command from the emitted function block.
            import re
            if a.tool_mode == "xml":
                cm = re.search(r'<param name="command">([^<]+)</param>', cont)
                cmd = cm.group(1) if cm else None
            if cmd is None:
                step += 1
                msgs.append({"role": "assistant", "content": cont})
                msgs.append({"role": "user", "content": "Use one bash tool call to continue recovery."})
                continue
            # strip the harness wait-marker if appended (official harness bug source)
            cmd = cmd.replace("; tmux wait -S done", "").replace("&& tmux wait -S done", "")
            try:
                r = run_process(["bash", "-lc", cmd], deadline,
                                cwd=str(stage_root), env=git_env)
                tool_calls += 1
            except subprocess.TimeoutExpired as e:
                r = subprocess.CompletedProcess(cmd, 124, stdout=e.output or "",
                                                stderr=(e.stderr or "") + "\n[tool timeout]")
                tool_timeouts += 1
            traces[-1]["command"] = cmd
            traces[-1]["tool_result"] = {
                "returncode": r.returncode, "stdout": r.stdout, "stderr": r.stderr,
            }
            write_trace(out_p.parent / "probe.jsonl", traces)
            tool_out = f"stdout:\n{r.stdout[-600:]}\n[stderr] {r.stderr[-400:]}"
            feedback = f"Tool result rc={r.returncode}: {tool_out}"
            if a.tool_mode == "native":
                msgs.append({"role": "assistant", "content": msg.get("content") or "",
                             "tool_calls": ntc})
                msgs.append({"role": "tool", "tool_call_id": ntc[0]["id"],
                             "content": feedback})
            else:
                msgs += [{"role": "assistant", "content": cont},
                         {"role": "user", "content": feedback +
                          "\nContinue until recovery-branch exists and master contains the lost commit."}]
            step += 1
            branch_exists, master_contains, branch_contains = recovery_state(
                stage_root, lost, deadline, git_env
            )
            traces[-1]["verification"] = {
                "recovery_branch_exists": branch_exists,
                "master_contains_lost": master_contains,
                "recovery_branch_contains_lost": branch_contains,
            }
            write_trace(out_p.parent / "probe.jsonl", traces)
            if branch_exists and master_contains and branch_contains:
                break
    except Exception as e:
        harness_error = f"{type(e).__name__}: {e}"
    elapsed = time.monotonic() - t0

    # ── 3. SINGLE ASSERTION: recovery branch present + its commit on master ─
    distinct = [c for c in calls if c and not c.startswith("//ExitEmpty//")]
    recover_cmd_used = any("recovery-branch" in c or "reflog" in c or "logs/HEAD" in c for c in distinct)
    deadline_exceeded = time.monotonic() >= deadline
    if deadline_exceeded:
        harness_error = harness_error or "TimeoutError: Run deadline exceeded"
    elapsed = time.monotonic() - t0
    passed = branch_exists and master_contains and branch_contains and not deadline_exceeded

    # ── 4. ALWAYS WRITE results.json ────────────────────────────────────────
    result = {
        "version": "fixgit-repro-v1",
        "task": "fix-git-lost-commit",
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "model": a.model,
        "tool_mode": a.tool_mode,
        "disable_thinking": a.disable_thinking,
        "endpoint": a.endpoint,
        "passed": passed,
        "elapsed_s": round(elapsed, 1),
        "deadline_exceeded": deadline_exceeded,
        "steps": step,
        "emit": "always",
        "harness_error": harness_error,
        "signal": {
            "recovery_branch_exists": branch_exists,
            "recovery_branch_contains_lost": branch_contains,
            "master_contains_lost": master_contains,
            "recovery_verb_used": recover_cmd_used,
            "total_tool_calls": tool_calls,
            "tool_timeouts": tool_timeouts,
            "empty_continuations": len([c for c in calls if not c]),
        },
        "probe_jsonl": "probe.jsonl",
    }
    out_p.write_text(json.dumps(result, indent=2) + "\n")
    write_trace(out_p.parent / "probe.jsonl", traces)
    print(json.dumps(result, indent=2))
    return passed

if __name__ == "__main__":
    sys.exit(main())

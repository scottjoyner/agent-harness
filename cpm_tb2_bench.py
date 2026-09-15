#!/usr/bin/env python3
"""CPM TB2 bench v2 - multi-step loop for MiniCPM5 on :1235.
v2: cwd persistence across steps, bash -n precheck on scripts,
don't-repeat failed commands, per-difficulty tool budgets, content flags."""

import json, time, re, os, subprocess, urllib.request
from datetime import datetime
from pathlib import Path

URL = "http://localhost:1235/v1/chat/completions"
BASH_SYS = """You are a tool-calling assistant. Execute tasks using bash.
RULES:
1. Output ONLY the function call
2. Format: <function name="bash"><param name="command">COMMAND</param></function>
3. Never explain, just execute
4. Chain with &&, redirect report with > /tmp/file.txt
5. Working directory persists between steps - plain cd works
Example:
<function name="bash"><param name="command">echo hello > /tmp/out.txt</param></function>"""
WRITE_SYS = """You are a tool-calling assistant. Output ONLY the function call, no thinking.
<function name="write_file"><param name="path">/path/to/file</param><param name="content">file content here</param></function>"""
def pick_sys(instr):
    t = instr.lower()
    if "write a python" in t or "create a bash script" in t or "write a bash" in t or "create a backup script" in t or "write a script" in t:
        return WRITE_SYS
    return BASH_SYS
SYS = BASH_SYS

def call_model(msgs, max_tokens=400, timeout=60):
    body = {"model": "test", "messages": msgs, "max_tokens": max_tokens, "temperature": 0.1}
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    t = time.time()
    try:
        r = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
        m = r["choices"][0]["message"]
        content = m.get("content", "") or ""
        reason = m.get("reasoning_content", "") or ""
        text = content if "<function" in content else (reason if "<function" in reason else content + "\n" + reason)
        return {"text": text, "tokens": r["usage"]["completion_tokens"], "time": time.time()-t}
    except Exception as e:
        return {"text": f"Error: {e}", "tokens": 0, "time": time.time()-t}

def extract_tc(text):
    if "<function" not in text:
        return None, None
    nm = re.search(r'<function\s+name="([^"]+)"', text)
    if not nm:
        return None, None
    name = nm.group(1)
    params = {}
    for m in re.finditer(r'<param\s+name="([^"]+)">(.*?)</param>', text, re.DOTALL):
        v = m.group(2).strip()
        if v.startswith("<![CDATA["):
            v = v[9:]
        if v.endswith("]]>"):
            v = v[:-3]
        params[m.group(1)] = v.strip()
    return name, params

_CWD = os.path.expanduser("~")

def _norm(cmd):
    return re.sub(r"\s+", " ", cmd.strip())

def _track_cwd(cmd):
    """Track cd across steps so context persists (v2)."""
    global _CWD
    for m in re.finditer(r"(?:^|&&|;)\s*cd\s+([^&;]+)", cmd):
        d = m.group(1).strip().strip("'\"")
        if d.startswith("~"):
            d = os.path.expanduser(d)
        elif not d.startswith("/"):
            d = os.path.join(_CWD, d)
        d = os.path.normpath(d)
        if os.path.isdir(d):
            _CWD = d

def run_tool(name, args, timeout=60):
    global _CWD
    try:
        if name == "bash":
            cmd = args.get("command", "")
            _track_cwd(cmd)
            if not re.match(r"\s*(cd\s|/)", cmd):
                cmd = f"cd {_CWD} && {cmd}"
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout, executable="/bin/bash")
            return r.stdout + r.stderr, r.returncode
        elif name == "write_file":
            p, c = args.get("path", ""), args.get("content", "")
            Path(p).parent.mkdir(parents=True, exist_ok=True)
            Path(p).write_text(c)
            if p.endswith(".py") or p.endswith(".sh"):
                Path(p).chmod(0o755)
            extra = ""
            if p.endswith(".sh"):
                chk = subprocess.run(["bash", "-n", p], capture_output=True, text=True, timeout=10)
                extra = f"\n[SYNTAX-CHECK] {'OK' if chk.returncode == 0 else 'FAIL: ' + (chk.stderr.strip()[:200])}"
                if chk.returncode != 0:
                    return f"Wrote {len(c)} bytes to {p}" + extra, 2
            return f"Wrote {len(c)} bytes to {p}" + extra, 0
        elif name == "read_file":
            return Path(args.get("path", "")).read_text()[:2000], 0
        return f"Unknown tool {name}", -1
    except subprocess.TimeoutExpired:
        return "TIMEOUT", -1
    except Exception as e:
        return str(e), -1

def _suspicious(task, out):
    """Conservative content flag (informational only, never flips pass)."""
    v = task.get("verification", {})
    p = Path(v.get("path", "")) if v else None
    if not p or not p.exists() or not p.is_file():
        return ""
    try:
        txt = p.read_text()[:600]
    except Exception:
        return ""
    if not txt.strip():
        return "empty-output"
    low = txt.lower()
    if ("syntax error" in low or "command not found" in low) and len(txt) < 400:
        return "likely-error-output"
    return ""

def verify(task):
    v = task.get("verification", {})
    p = Path(v.get("path", ""))
    if v.get("type") == "file_exists":
        if not p.exists():
            return False, "missing file"
        if p.stat().st_size < v.get("min_size_bytes", 0):
            return False, f"too small {p.stat().st_size}"
        return True, "PASS"
    if v.get("type") == "directory_exists":
        if not p.exists():
            return False, "missing dir"
        if len(list(p.iterdir())) < v.get("min_files", 0):
            return False, "too few files"
        return True, "PASS"
    return True, "no check"

def run_task(task, max_steps=4):
    global _CWD
    _CWD = os.path.expanduser("~")
    budget = 45 if task.get("difficulty") == "hard" else 25
    p = task.get("verification", {}).get("path")
    if p:
        pp = Path(p)
        if pp.exists():
            if pp.is_file():
                pp.unlink()
            else:
                import shutil
                shutil.rmtree(pp)
    base = [{"role": "system", "content": pick_sys(task["instruction"])}, {"role": "user", "content": task["instruction"]}]
    trace = []
    tot_tok, tot_time = 0, 0
    last_fb = ""
    failed_cmds = set()
    for step in range(max_steps):
        msgs = base + ([{"role": "user", "content": last_fb}] if last_fb else [])
        r = call_model(msgs, max_tokens=600, timeout=120)
        tot_tok += r["tokens"]
        tot_time += r["time"]
        name, args = extract_tc(r["text"])
        if not name:
            last_fb = "No tool call found. Output ONE <function> tool call now."
            trace.append({"step": step+1, "raw": r["text"][:300], "error": "no toolcall"})
            continue
        norm = _norm(args.get("command", args.get("path", "")))
        repeat = name == "bash" and norm in failed_cmds
        out, rc = run_tool(name, args, min(task.get("timeout_sec", 60), budget))
        if name == "write_file" and args.get("path", "").endswith((".py", ".sh")):
            if rc != 2:  # syntax precheck passed (or n/a)
                rc2_out, rc2 = run_tool("bash", {"command": f"python3 {args['path']}" if args['path'].endswith(".py") else f"bash {args['path']}"}, budget)
                out = out + "\n[AUTO-RUN] " + rc2_out[:300]
                rc = rc2 if rc2 != 0 else rc
        if rc != 0 and norm:
            failed_cmds.add(norm)
        flag = _suspicious(task, out)
        trace.append({"step": step+1, "tool": name, "args_keys": list(args.keys()), "cmd": args.get("command", args.get("path", ""))[:150], "rc": rc, "out": out[:300], "repeat": repeat, "flag": flag})
        ok, msg = verify(task)
        if ok:
            return {"id": task["id"], "success": True, "steps": step+1, "tokens": tot_tok, "time": tot_time, "trace": trace}
        last_fb = f"Result rc={rc}: {out[:300]}\nNot done ({msg})."
        if repeat:
            last_fb += " You already tried this exact command and it failed - try a DIFFERENT approach."
        last_fb += " Next tool call:"
    ok, msg = verify(task)
    return {"id": task["id"], "success": ok, "steps": max_steps, "tokens": tot_tok, "time": tot_time, "trace": trace, "note": msg}

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=0)
    ap.add_argument("--tasks", nargs="*")
    a = ap.parse_args()
    tasks = json.loads(Path(__file__).parent.joinpath("benchmarks/baremetal/tasks.json").read_text())["tasks"]
    if a.tasks:
        tasks = [t for t in tasks if t["id"] in a.tasks]
    if a.max:
        tasks = tasks[:a.max]
    print(f"CPM TB2 bench v2 | tasks={len(tasks)} | {datetime.now().isoformat()}", flush=True)
    results = []
    for t in tasks:
        print(f"[{len(results)+1}/{len(tasks)}] {t['id']} start {datetime.now().strftime('%H:%M:%S')}", flush=True)
        r = run_task(t)
        results.append(r)
        print(f"  {'PASS' if r['success'] else 'FAIL'} {r['id']} steps={r['steps']} tok={r['tokens']} t={r['time']:.0f}s", flush=True)
    passed = sum(1 for r in results if r["success"])
    print(f"PASSED {passed}/{len(results)} ({100*passed/len(results):.1f}%)")
    for r in results:
        print(f"  {'PASS' if r['success'] else 'FAIL'} {r['id']} steps={r['steps']} tok={r['tokens']} t={r['time']:.1f}s")
    fn = f"cpm_tb2_{datetime.now().strftime('%H%M%S')}.json"
    Path(fn).write_text(json.dumps({"ts": datetime.now().isoformat(), "passed": passed, "total": len(results), "results": results}, indent=2))
    print(f"Saved {fn}")

if __name__ == "__main__":
    main()

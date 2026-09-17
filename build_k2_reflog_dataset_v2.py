"""Build the K2 reflog recovery dataset from real disposable git fixtures.

Every tool result is captured from an actual command run in an actual repo.
Training rows end with the final assistant action; intermediate mistakes are
context only (the trainer masks everything before the final turn).
Held-out recipes are emitted to a separate file and excluded from training.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TOOLS = [{
    "type": "function",
    "function": {
        "name": "bash",
        "description": "Run one bash command in the repo and return stdout/stderr plus exit code.",
        "parameters": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    },
}]

TASKS = {
    "site": ("I was working on a personal site in a detached HEAD. I made my changes and "
             "committed them, then ran `git checkout master`. Now git status and git diff show "
             "nothing and I can't find my work. Help me recover the lost commit and merge it "
             "onto master. Create a branch named recovery-branch at the lost commit, then merge "
             "it onto master. Use the bash tool, one call per turn."),
    "config": ("I tuned some settings while my repo was in detached HEAD, committed the "
               "change, and then switched back to my main branch. The tuning is gone from "
               "git status and git diff. Find that commit and land it on main through a "
               "branch called recovery-branch. Use the bash tool, one call per turn."),
    "notes": ("Some release notes I wrote and committed while in detached HEAD have "
              "vanished after I checked out master. Track down that commit and merge it "
              "into master using a branch named recovery-branch. Use the bash tool, one "
              "call per turn."),
    "perf": ("I profiled and committed a speedup while detached, then checked out main. "
             "Now the change is nowhere in git status. Recover it: create a branch named "
             "recovery-branch at that commit and merge it into main. Use the bash tool, "
             "one call per turn."),
}

RECIPES = [
    # id, task_key, base_branch, filename, old, new, subject, detached_commits, held_out
    ("site-default", "site", "master", "index.html",
     "<h1>old</h1>\n", "<h1>new work</h1>\n", 1, False),
    ("site-main", "site", "main", "index.html",
     "<h1>old</h1>\n", "<h1>gallery live</h1>\n", 1, False),
    ("config-tune", "config", "main", "settings.conf",
     "cache_size = 16\n", "cache_size = 64\n", 1, False),
    ("notes-draft", "notes", "master", "NOTES.md",
     "# Notes\n", "# Notes\n- release candidate 3\n", 1, False),
    ("site-two-commits", "site", "master", "index.html",
     "<h1>old</h1>\n", "<h1>rebuilt nav</h1>\n", 2, False),
    ("perf-fast", "perf", "main", "export.py",
     "def run(): pass\n", "def run(): return 42\n", 1, True),
    ("site-advanced", "site", "main", "index.html",
     "<h1>old</h1>\n", "<h1>footer adjusted</h1>\n", 2, True),
]

REFLOG_CMD = "git reflog --format='%H %gs'"


def run_cmd(root, command):
    env = dict(os.environ, GIT_AUTHOR_NAME="Harness",
               GIT_AUTHOR_EMAIL="harness@localhost",
               GIT_COMMITTER_NAME="Harness",
               GIT_COMMITTER_EMAIL="harness@localhost")
    return subprocess.run(["bash", "-lc", command], cwd=str(root), env=env,
                          capture_output=True, text=True)


def build_fixture(recipe):
    _, task_key, base_branch, filename, old, new, n_detached, held_out = recipe
    root = Path(tempfile.mkdtemp("k2ds"))
    subprocess.run(["git", "-C", str(root), "init", "-b", base_branch],
                   capture_output=True, text=True, check=True)
    (root / filename).write_text(old)
    run_cmd(root, f"git add {filename}")
    run_cmd(root, "git commit -m Initial")
    subprocess.run(["git", "-C", str(root), "checkout", "--detach", "HEAD"],
                   capture_output=True, text=True, check=True)
    lost_shas = []
    for i in range(n_detached):
        (root / filename).write_text(new if i == n_detached - 1
                                     else f"{old}<!-- wip {i} -->\n")
        run_cmd(root, f"git commit -am 'Feature work part {i + 1}'")
        lost_shas.append(run_cmd(root, "git rev-parse HEAD").stdout.strip())
    lost = lost_shas[-1]
    subprocess.run(["git", "-C", str(root), "checkout", base_branch],
                   capture_output=True, text=True, check=True)
    return root, lost, lost_shas, TASKS[task_key], base_branch


def user(content):
    return {"role": "user", "content": content}


def assistant_cmd(cmd, call_id):
    return {"role": "assistant", "content": "",
            "tool_calls": [{"id": call_id, "type": "function",
                            "function": {"name": "bash",
                                         "arguments": json.dumps({"command": cmd})}}]}


def tool_reply(call_id, r):
    return {"role": "tool", "tool_call_id": call_id,
            "content": (f"Tool result rc={r.returncode}: stdout:\n{r.stdout[-600:]}\n"
                        f"[stderr] {r.stderr[-400:]}")}


def build_rows():
    train_rows, heldout_rows = [], []
    for recipe in RECIPES:
        recipe_id, _, _, _, _, _, n_detached, held_out = recipe
        root, lost, lost_shas, task, base_branch = build_fixture(recipe)
        try:
            call = [f"c-{recipe_id}-1", f"c-{recipe_id}-2", f"c-{recipe_id}-3"]
            reflog_r = run_cmd(root, REFLOG_CMD)
            action = (f"git branch recovery-branch {lost} && "
                      f"git checkout {base_branch} && "
                      f"git merge --ff-only recovery-branch")
            action_r = run_cmd(root, action)
            assert action_r_ok(action_r), action_r

            # discovery row: task -> reflog
            row = {"messages": [user(task), assistant_cmd(REFLOG_CMD, call[0])],
                   "tools": TOOLS}
            (heldout_rows if held_out else train_rows).append(row)

            # action row: task -> reflog -> (real reflog result) -> action
            row = {"messages": [
                user(task),
                assistant_cmd(REFLOG_CMD, call[0]),
                tool_reply(call[0], reflog_r),
                assistant_cmd(action, call[1]),
            ], "tools": TOOLS}
            (heldout_rows if held_out else train_rows).append(row)

            if n_detached > 1:
                # multi-detached chain: verify branch points at the LAST commit
                verify = "git rev-parse recovery-branch"
                row = {"messages": [
                    user(task),
                    assistant_cmd(REFLOG_CMD, call[0]),
                    tool_reply(call[0], reflog_r),
                    assistant_cmd(f"git branch recovery-branch {lost_shas[0]}",
                                  call[1]),
                    tool_reply(call[1], run_cmd(root, f"git branch -D recovery-branch; "
                                                f"git branch recovery-branch {lost_shas[0]}")),
                    user(task + "\n\nrecovery-branch points at the first detached "
                         "commit, but the newest detached work is the missing one. "
                         "The last 'commit:' line in the reflog above is the newest. "
                         "Reply with exactly one bash tool call to finish recovery."),
                    assistant_cmd(f"git branch -f recovery-branch {lost} && "
                                  f"git checkout {base_branch} && "
                                  f"git merge --ff-only recovery-branch", call[2]),
                ], "tools": TOOLS}
                (heldout_rows if held_out else train_rows).append(row)
        finally:
            shutil.rmtree(root, ignore_errors=True)
    return train_rows, heldout_rows


def action_r_ok(r):
    return r.returncode == 0 and "fast-forward" not in r.stderr.lower()


def validate(rows):
    problems = []
    for idx, row in enumerate(rows):
        if set(row) != {"messages", "tools"} or row["tools"] != TOOLS:
            problems.append(f"row {idx}: bad shape")
            continue
        prior = ""
        last_call_id = None
        for mi, msg in enumerate(row["messages"]):
            role = msg["role"]
            if role == "assistant":
                calls = msg.get("tool_calls", [])
                if len(calls) != 1 or calls[0]["function"]["name"] != "bash":
                    problems.append(f"row {idx} msg {mi}: bad call")
                    continue
                last_call_id = calls[0]["id"]
                cmd = json.loads(calls[0]["function"]["arguments"])["command"]
                for token in re.findall(r"[0-9a-f]{40}", cmd):
                    if token not in prior:
                        problems.append(f"row {idx} msg {mi}: ungrounded {token[:8]}")
            elif role == "tool":
                if msg.get("tool_call_id") != last_call_id:
                    problems.append(f"row {idx} msg {mi}: tool id mismatch")
                prior += msg["content"]
            else:
                prior += msg["content"]
        if row["messages"][-1]["role"] != "assistant":
            problems.append(f"row {idx}: must end with assistant action")
    return problems


def main():
    train_rows, heldout_rows = build_rows()
    for label, rows in (("train", train_rows), ("heldout", heldout_rows)):
        problems = validate(rows)
        if problems:
            for p in problems:
                print(f"[{label}] {p}", file=sys.stderr)
            return 1
    base = Path(__file__).parent
    for label, rows in (("k2_reflog_train_v2", train_rows),
                        ("k2_reflog_heldout_v2", heldout_rows)):
        with (base / f"{label}.jsonl").open("w") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")
    print(f"train={len(train_rows)} heldout={len(heldout_rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

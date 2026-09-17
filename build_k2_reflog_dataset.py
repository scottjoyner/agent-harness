import json
import re
import sys
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

TASK = (
    "I was working on a personal site in a detached HEAD. I made my changes and "
    "committed them, then ran `git checkout master`. Now git status and git diff show "
    "nothing and I can't find my work. Help me recover the lost commit and merge it "
    "onto master. Create a branch named recovery-branch at the lost commit, then merge "
    "it onto master. Use the bash tool, one call per turn."
)

TASK_VARIANTS = [
    TASK,
    "I committed work while in detached HEAD, then ran `git checkout master`. Now my "
    "changes are gone from status and diff. Find the lost commit and get it onto "
    "master via a branch named recovery-branch. Use the bash tool, one call per turn.",
    "My last commit is missing after I left detached HEAD. Recover it: create "
    "recovery-branch at the lost commit and merge it into master. Use the bash tool, "
    "one call per turn.",
]

CONT = "\nReply with exactly one bash tool call to continue recovery."

REAL_LOST = "4906178ddd0c2db6a5a7ae4927cf283fc12ac8ec"
REAL_MASTER = "140faa67afd122913534cf9f6f7e01da188c3647"

LOST_TAIL = "3f7a1c9e5b2d84f06a3c17e9d5b8f24a6c0e91d"
MASTER_TAIL = "b8e2d5f09a4c7163e8b0d2f5a9c6147e3d8b0f2"


def sha(i, tail):
    return f"{i:1x}{tail}"


def reflog_fb(lost, master):
    return (
        "Tool result rc=0: stdout:\n"
        f"{master} checkout: moving from {lost} to master\n"
        f"{lost} commit: Feature work\n"
        f"{master} checkout: moving from master to HEAD\n"
        f"{master} commit (initial): Initial\n\n[stderr] "
    )


def user(content):
    return {"role": "user", "content": content}


def assistant_cmd(cmd, n):
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{
            "id": f"call-{n}",
            "type": "function",
            "function": {"name": "bash", "arguments": json.dumps({"command": cmd})},
        }],
    }


REFLOG_CMD = "git reflog --format='%H %gs'"


def action_cmd(lost):
    return f"git branch recovery-branch {lost} && git merge --ff-only recovery-branch"


def build_rows():
    rows = []

    for variant in TASK_VARIANTS:
        rows.append({
            "messages": [user(variant), assistant_cmd(REFLOG_CMD, len(rows) + 1)],
            "tools": TOOLS,
        })
    rows.append({
        "messages": [user(TASK), assistant_cmd("git reflog -8", len(rows) + 1)],
        "tools": TOOLS,
    })

    pairs = [(REAL_LOST, REAL_MASTER)] + [
        (sha(i, LOST_TAIL), sha(i, MASTER_TAIL)) for i in range(1, 10)
    ]
    for lost, master in pairs:
        prompt = TASK + "\n\n" + reflog_fb(lost, master) + CONT
        rows.append({
            "messages": [user(prompt), assistant_cmd(action_cmd(lost), len(rows) + 1)],
            "tools": TOOLS,
        })

    for lost, master in pairs[:4]:
        rows.append({
            "messages": [
                user(TASK),
                assistant_cmd(REFLOG_CMD, len(rows) + 1),
                user(TASK + "\n\n" + reflog_fb(lost, master) + CONT),
                assistant_cmd(action_cmd(lost), len(rows) + 1),
            ],
            "tools": TOOLS,
        })

    for i in range(10, 12):
        lost, master = sha(i, LOST_TAIL), sha(i, MASTER_TAIL)
        rows.append({
            "messages": [
                user(TASK),
                assistant_cmd(REFLOG_CMD, len(rows) + 1),
                user(TASK + "\n\n" + reflog_fb(lost, master) + CONT),
                assistant_cmd(f"git branch recovery-branch {lost}", len(rows) + 1),
                user(TASK + "\n\nTool result rc=0: stdout:\nSwitched to a new branch "
                     "'recovery-branch'\n\n[stderr] \n" + CONT),
                assistant_cmd("git checkout master && git merge --ff-only recovery-branch",
                              len(rows) + 1),
            ],
            "tools": TOOLS,
        })

    invented = "deadbeef" * 5
    for lost, master in pairs[5:7]:
        rows.append({
            "messages": [
                user(TASK),
                assistant_cmd(REFLOG_CMD, len(rows) + 1),
                user(TASK + "\n\n" + reflog_fb(lost, master) + CONT),
                assistant_cmd(f"git branch recovery-branch {lost}", len(rows) + 1),
                user(TASK + f"\n\nTool result rc=128: fatal: bad object {invented}\n"
                     "The branch command referenced an unlisted sha; the only valid "
                     "shas are in the reflog output above.\n" + CONT),
                assistant_cmd(action_cmd(lost), len(rows) + 1),
            ],
            "tools": TOOLS,
        })

    # line-selection: model branches at the checkout-to-master entry (master's own
    # sha, the top reflog line) instead of the 'commit: Feature work' line; the
    # merge then no-ops. Feedback names the mistake; target re-selects correctly.
    for i in range(12, 15):
        lost, master = sha(i, LOST_TAIL), sha(i, MASTER_TAIL)
        rows.append({
            "messages": [
                user(TASK),
                assistant_cmd(REFLOG_CMD, len(rows) + 1),
                user(TASK + "\n\n" + reflog_fb(lost, master) + CONT),
                assistant_cmd(f"git branch recovery-branch {master} && "
                              "git merge --ff-only recovery-branch", len(rows) + 1),
                user(TASK + f"\n\nTool result rc=0: stdout:\nAlready up to date.\n\n"
                     f"[stderr] \nrecovery-branch points at {master}, the checkout "
                     "entry — that is master itself, not the lost commit. The lost "
                     f"commit is the line reading 'commit: Feature work' ({lost}). "
                     "Reset the branch and merge again.\n" + CONT),
                assistant_cmd(f"git branch -f recovery-branch {lost} && "
                              "git checkout master && "
                              "git merge --ff-only recovery-branch", len(rows) + 1),
            ],
            "tools": TOOLS,
        })

    return rows


def validate(rows):
    seen = set()
    problems = []
    for idx, row in enumerate(rows):
        if set(row) != {"messages", "tools"} or row["tools"] != TOOLS:
            problems.append(f"row {idx}: bad shape")
            continue
        key = json.dumps(row["messages"], sort_keys=True)
        if key in seen:
            problems.append(f"row {idx}: duplicate")
        seen.add(key)
        prior_text = ""
        for mi, msg in enumerate(row["messages"]):
            role = msg["role"]
            if role == "assistant":
                if msg["content"] != "":
                    problems.append(f"row {idx} msg {mi}: assistant content not empty")
                calls = msg.get("tool_calls", [])
                if len(calls) != 1 or calls[0]["function"]["name"] != "bash":
                    problems.append(f"row {idx} msg {mi}: bad tool call")
                    continue
                args = json.loads(calls[0]["function"]["arguments"])
                cmd = args["command"]
                if "<function" in cmd or "<ifm" in cmd:
                    problems.append(f"row {idx} msg {mi}: xml leakage")
                for token in re.findall(r"[0-9a-f]{40}", cmd):
                    if token not in prior_text:
                        problems.append(f"row {idx} msg {mi}: ungrounded sha {token[:8]}")
            else:
                prior_text += msg["content"]
    return problems


def main():
    rows = build_rows()
    problems = validate(rows)
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1
    out = Path(__file__).parent / "k2_reflog_train.jsonl"
    with out.open("w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    kinds = {"discovery": 0, "action": 0}
    for row in rows:
        last = row["messages"][-1]["tool_calls"][0]["function"]["arguments"]
        if "reflog" in last:
            kinds["discovery"] += 1
        else:
            kinds["action"] += 1
    print(f"wrote {len(rows)} rows to {out.name}: {kinds}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

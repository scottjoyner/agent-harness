"""Build the Stage 2 K2 reflog dataset with additional phrasing variants.

The base fixture and tool-reply machinery lives in build_k2_reflog_dataset_v2.
This module adds fresh disposable recipes with alternate site/perf wording while
keeping the held-out split separate from training.
"""
import json
import sys
from pathlib import Path

import build_k2_reflog_dataset_v2 as base

TASKS = dict(base.TASKS)
TASKS["site-correction-last"] = (
    "Detached HEAD had two commits, then master was checked out. "
    "The newest detached commit is lost. Read the reflog and finish recovery. "
    "Use bash, one command per turn.")

RECIPES = list(base.RECIPES) + [
    ("site-alt", "site-alt", "master", "about.html",
     "<h1>old</h1>\n", "<h1>about page live</h1>\n", 1, False, False),
    ("site-alt-two", "site-alt", "main", "about.html",
     "<h1>old</h1>\n", "<h1>about page revised</h1>\n", 2, False, False),
    ("perf-alt", "perf-alt", "main", "speed.py",
     "def run(): pass\n", "def run(): return 99\n", 1, False, False),
    ("site-correction-short", "site-correction-short", "master", "index.html",
     "<h1>old</h1>\n", "<h1>recovery target</h1>\n", 2, False, False),
    ("site-correction-last", "site-correction-last", "master", "index.html",
     "<h1>old</h1>\n", "<h1>last-line target</h1>\n", 2, False, False),
    ("site-correction-last-main", "site-correction-last", "main", "index.html",
     "<h1>old</h1>\n", "<h1>last-line main target</h1>\n", 2, False, False),
]


def build_rows():
    correction_phrases = {
        "site-correction-last": (
            "recovery-branch points at the first detached commit, but the newest "
            "detached work is the missing one. Force recovery-branch to the newest "
            "detached commit; the newest 'commit:' entry appears first in the "
            "reflog above. "),
    }
    train_rows, _ = base.build_rows(
        recipes=RECIPES, task_overrides=TASKS,
        correction_phrases=correction_phrases)
    heldout_path = Path(__file__).parent / "k2_reflog_heldout_v2.jsonl"
    if heldout_path.exists():
        heldout_rows = [json.loads(line) for line in heldout_path.read_text().splitlines()]
    else:
        _, heldout_rows = base.build_rows(
            recipes=RECIPES, task_overrides=TASKS,
            correction_phrases={"site-correction-last": (
                "recovery-branch points at the first detached commit, but the newest "
                "detached work is the missing one. Force recovery-branch to the newest "
                "detached commit; the newest 'commit:' entry appears first in the "
                "reflog above. ")})
    return train_rows, heldout_rows


def main():
    train_rows, heldout_rows = build_rows()
    problems = base.validate(train_rows + heldout_rows)
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1

    base_path = Path(__file__).parent
    train_path = base_path / "k2_reflog_train_v3.jsonl"
    with train_path.open("w") as output:
        for row in train_rows:
            output.write(json.dumps(row) + "\n")

    heldout_path = base_path / "k2_reflog_heldout_v2.jsonl"
    if not heldout_path.exists():
        print("held-out split is missing", file=sys.stderr)
        return 1

    print(f"train={len(train_rows)} heldout={len(heldout_rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

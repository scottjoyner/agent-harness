#!/usr/bin/env python3
"""Bounded answer check for the ``contract_reasoning`` fixture.

The candidate is asked to reason about the driver-lifetime contract in
``auto_ingest/shorts/cli.py``. This script asserts the specific findings the
task is about, against the exact answer text the harness captured.

It reads one file (the answer path given as ``argv[1]``), prints a finding-by-
finding report, and exits non-zero when a required finding is absent. No
network, no database, no credentials.
"""
from __future__ import annotations

import sys
from pathlib import Path

#: (finding name, all-of substrings that must appear, any-of substrings)
#: ``any_of`` empty means the ``all_of`` list alone decides the finding.
REQUIRED_FINDINGS = (
    (
        "names the planning entry point",
        ("plan_shorts",),
        (),
    ),
    (
        "names the driver's close call",
        ("close",),
        (),
    ),
    (
        "identifies the ordering defect (plan_shorts runs after driver.close())",
        ("plan_shorts",),
        (
            "after driver.close",
            "after close",
            "after the driver is closed",
            "after the driver",
            "already closed",
            "closed driver",
            "post-close",
            "outside the try",
            "after the finally",
            "before driver.close",
            "while the driver is still",
            "while the driver is live",
            "still open",
        ),
    ),
    (
        "locates the defect in the CLI module",
        ("cli.py",),
        (),
    ),
)

FORBIDDEN = (
    (
        "does not propose dropping the driver argument (S-G10 intent must survive)",
        (
            "remove the driver",
            "drop the driver",
            "without a driver",
            "no driver=",
            "driver=None",
        ),
    ),
)


def main(argv) -> int:
    if len(argv) < 2:
        print("usage: check_answer.py <answer-file>", file=sys.stderr)
        return 2
    answer_path = Path(argv[1])
    if not answer_path.is_file():
        print("answer file not found: {}".format(answer_path), file=sys.stderr)
        return 2
    answer = answer_path.read_text(encoding="utf-8", errors="replace")
    lowered = answer.lower()

    failures = 0
    print("=== required findings ===")
    for name, all_of, any_of in REQUIRED_FINDINGS:
        has_all = all(needle.lower() in lowered for needle in all_of)
        has_any = (not any_of) or any(needle.lower() in lowered for needle in any_of)
        ok = has_all and has_any
        print("  [{}] {}".format("PASS" if ok else "FAIL", name))
        if not ok:
            failures += 1
            if not has_all:
                print("         missing: {}".format(", ".join(all_of)))
            if not has_any:
                print("         expected one of: {}".format(", ".join(any_of)))

    print("=== forbidden ===")
    for name, needles in FORBIDDEN:
        hit = [n for n in needles if n.lower() in lowered]
        ok = not hit
        print("  [{}] {}".format("PASS" if ok else "FAIL", name))
        if not ok:
            failures += 1
            print("         matched: {}".format(", ".join(hit)))

    print("=== summary ===")
    print("  answer bytes: {}".format(len(answer)))
    print("  findings failed: {}".format(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

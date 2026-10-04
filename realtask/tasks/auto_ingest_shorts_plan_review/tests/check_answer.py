#!/usr/bin/env python3
"""Bounded answer check for the ``code_review`` fixture.

The candidate is asked to review ``auto_ingest/shorts/cli.py``. This script
asserts that the review surfaced the driver-lifetime defect and the concrete
downstream risk, against the exact answer text the harness captured.

It reads one file (the answer path given as ``argv[1]``), prints a
finding-by-finding report, and exits non-zero when a required finding is
absent. No network, no database, no credentials.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

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
        "flags the ordering defect (plan_shorts runs after driver.close())",
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
        ),
    ),
    (
        "flags the silent-degradation consequence",
        ("",),
        (
            "templated",
            "silently",
            "silent",
            "fallback",
            "swallow",
            "except",
            "swallowed",
            "exit 0",
            "exit code 0",
            "loses",
            "lost",
            "degrade",
            "hidden",
        ),
    ),
    (
        "flags a cleanup hazard in the repair (driver leak or discussion-mode regression)",
        ("",),
        (
            "leak",
            "not closed",
            "never closed",
            "double close",
            "twice",
            "--discusses",
            "discusses",
            "discussion",
            "finally",
        ),
    ),
    (
        "locates the finding in the CLI module",
        ("cli.py",),
        (),
    ),
)

#: A finding must be carried by a single proposition. Splitting on sentence and
#: clause punctuation -- and deliberately *not* on commas, so a comma-separated
#: word dump stays one blob -- stops a candidate from scattering trigger words
#: across a whole document until each one happens to appear somewhere.
#: A period only ends a sentence when whitespace follows it, so the dotted
#: identifiers this corpus is full of -- ``cli.py``, ``planner.plan_shorts``,
#: ``auto_ingest_config.get_x`` -- survive intact. Splitting on every period
#: shreds exactly the file references a finding has to cite.
CLAUSE_SPLIT = re.compile(r"[;:!?\n]|\.(?=\s|$)")

#: Below this length a fragment carries no claim worth crediting.
MIN_CLAUSE_CHARS = 24

#: A clause that is mostly trigger words is a keyword dump, not a finding. On the
#: reference answer the densest clause scores ~0.19; a pure trigger-word salad
#: scores above 0.8, so this threshold has a wide margin in both directions.
MAX_CLAUSE_KEYWORD_DENSITY = 0.55

#: A clause asserting a finding must also say something that is *not* a trigger.
#: Without this, a clause made of trigger words still counts as a finding however
#: little of it is prose. The bar is 3 rather than higher because a bare file
#: path is a legitimate locator and carries only two non-trigger words
#: ("auto_ingest", "shorts"); a real sentence carries ten times that, and an
#: echo of a needle carries none.
MIN_SUBSTANTIVE_TOKENS = 3


#: A review or a contract argument made of fewer propositions than this has not
#: argued anything.
MIN_CLAUSES = 2


def clauses(answer: str):
    """Split an answer into candidate propositions."""
    out = []
    for raw in CLAUSE_SPLIT.split(answer):
        piece = " ".join(raw.split())
        if len(piece) >= MIN_CLAUSE_CHARS:
            out.append(piece)
    return out


def _mask(low: str, needles) -> bytearray:
    """Mark every character of ``low`` covered by any needle in ``needles``."""
    mask = bytearray(len(low))
    for needle in needles:
        if not needle:
            continue
        start = 0
        while True:
            i = low.find(needle.lower(), start)
            if i < 0:
                break
            for j in range(i, i + len(needle)):
                mask[j] = 1
            start = i + len(needle)
    return mask


def trigger_vocabulary(groups) -> set:
    """Every word any needle is keyed on, however the needle is punctuated.

    A candidate that pads an answer with these words has learned the grader, not
    the codebase, so they do not count as having said anything.
    """
    words = set()
    for group in groups:
        for needle in group:
            if not needle:
                continue
            for token in re.findall(r"[a-z_][a-z0-9_]*", needle.lower()):
                words.add(token)
    return words


def clause_carries(clause: str, all_of, any_of, vocabulary) -> bool:
    """Does this one clause assert the finding, rather than merely echo it?"""
    low = clause.lower()
    required = [n for n in all_of if n]
    if any(n.lower() not in low for n in required):
        return False
    alternatives = [n for n in any_of if n]
    if alternatives and not any(n.lower() in low for n in alternatives):
        return False
    covered = _mask(low, required + alternatives)
    if sum(covered) / max(len(low), 1) > MAX_CLAUSE_KEYWORD_DENSITY:
        return False
    tokens = re.findall(r"[a-z_][a-z0-9_]*", low)
    substantive = [tok for tok in tokens if tok not in vocabulary]
    return len(substantive) >= MIN_SUBSTANTIVE_TOKENS




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

    parts = clauses(answer)
    groups = [list(a) + list(o) for _n, a, o in REQUIRED_FINDINGS]
    vocabulary = trigger_vocabulary(groups)
    failures = 0
    print("=== required findings ===")
    for name, all_of, any_of in REQUIRED_FINDINGS:
        hit = next(
            (c for c in parts if clause_carries(c, all_of, any_of, vocabulary)), None
        )
        print("  [{}] {}".format("PASS" if hit else "FAIL", name))
        if hit is None:
            failures += 1
            required = [n for n in all_of if n]
            alternatives = [n for n in any_of if n]
            if not any(
                all(n.lower() in lowered for n in required) for _ in (0,)
            ):
                print("         missing: {}".format(", ".join(required)))
            if alternatives and not any(n.lower() in lowered for n in alternatives):
                print("         expected one of: {}".format(", ".join(alternatives)))
            print("         no single clause asserts this; the terms appear only "
                  "scattered across the answer, or only as keyword padding")

    print("=== answer quality ===")
    print("  propositions found: {}".format(len(parts)))
    if len(parts) < MIN_CLAUSES:
        failures += 1
        print("  [FAIL] fewer than {} propositions; nothing was argued".format(MIN_CLAUSES))
    else:
        print("  [PASS] the answer is broken into at least {} propositions".format(MIN_CLAUSES))

    print("=== summary ===")
    print("  answer bytes: {}".format(len(answer)))
    print("  findings failed: {}".format(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

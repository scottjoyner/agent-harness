#!/usr/bin/env python3
"""Bounded answer check for the ``assistx_allocation_llm_capability_gate`` fixture.

The candidate is asked to review ``assistx/allocation_engine.py``. The module
ranks task/node/model placements and is supposed to refuse a node that cannot
serve a task. At the frozen revision it does not, for one capability, and the
refusal path is arranged so that nothing reveals it.

This script asserts that the review surfaced that specific hole and its shape,
against the exact answer text the harness captured.

It reads one file (the answer path given as ``argv[1]``), prints a finding-by-
finding report, and exits non-zero when a required finding is absent. No network,
no database, no credentials.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import List

#: (finding name, all-of substrings, any-of substrings).
#: ``any_of`` empty means the ``all_of`` list alone decides the finding.
REQUIRED_FINDINGS = (
    (
        "locates the capability gate in the allocation engine",
        ("required_capabilities",),
        (),
    ),
    (
        "identifies that llm is exempted from the capability requirement",
        ("llm",),
        (
            "exempt",
            "excluded",
            "special-cased",
            "special cased",
            "unconditionally",
            "waived",
            "bypass",
            "short-circuit",
            "short circuit",
            "hardcoded",
            "hard-coded",
            "implicitly granted",
            "auto-satisfied",
            "always satisfied",
            "added to the node's capabilities",
        ),
    ),
    (
        "points at the union with the literal set (capabilities | {llm})",
        ("capabilities",),
        (
            "union",
            "|",
            "issubset",
            "subset",
            "or llm",
            "plus llm",
            "merged",
            "superset",
        ),
    ),
    (
        "states the consequence: a node lacking llm is admitted, not rejected",
        ("llm",),
        (
            "admitted",
            "accepted",
            "eligible",
            "not rejected",
            "never rejected",
            "passes the gate",
            "passes the check",
            "can be recommended",
            "may be recommended",
            "becomes a candidate",
            "considered",
        ),
    ),
    (
        "notes the diagnostic is suppressed too, so rejected stays empty",
        ("rejected",),
        (
            "missing_capabilities",
            "capability_mismatch",
            "diagnostic",
            "reason",
            "no reason",
            "silent",
            "no record",
            "never reported",
            "not reported",
            "suppressed",
            "hidden",
        ),
    ),
    (
        "identifies the silent consequence: the wrong node receives the work",
        (),
        (
            # Added after a real 30B answer was failed for saying it in its own
            # words: "this allows a node that does not advertise 'llm' to be
            # admitted for tasks requiring it". That is the wrong node receiving
            # the work. The compounds below are deliberate rather than bare
            # "admitted", because "a node lacking llm is admitted, not
            # rejected" is the *gate-level* finding and the two must stay
            # distinguishable.
            "admitted for tasks requiring",
            "assigned the task",
            "assigned a task",
            "receives the work",
            "runs the work",
            "picks up the work",
            "wrong node",
            "node that does not advertise",
            "cannot serve",
            "dispatch",
            "executed on",
            "runs the task",
            "receives the task",
            "receives the work",
            "capable of serving",
            "cannot serve",
            "misconfigured",
            "unconfigured",
            "silently",
            "no warning",
            "no signal",
            "no error",
            "without any",
        ),
    ),
)

#: Answers that would satisfy the findings without having read the module.
FORBIDDEN = (
    (
        "does not merely restate the task statement",
        (
            "the task is to review",
            "as stated in the problem",
            "per the problem statement",
        ),
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
            if required and not all(n.lower() in lowered for n in required):
                print("         missing: {}".format(", ".join(required)))
            if alternatives and not any(n.lower() in lowered for n in alternatives):
                print("         expected one of: {}".format(", ".join(alternatives)))
            print("         no single clause asserts this; the terms appear only "
                  "scattered across the answer, or only as keyword padding")

    print("=== forbidden ===")
    for name, needles in FORBIDDEN:
        hit = [n for n in needles if n.lower() in lowered]
        ok = not hit
        print("  [{}] {}".format("PASS" if ok else "FAIL", name))
        if not ok:
            failures += 1
            print("         matched: {}".format(", ".join(hit)))

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

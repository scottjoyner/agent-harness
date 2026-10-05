#!/usr/bin/env python3
"""Bounded answer check for the ``auto_router_contract_shim_single_source`` fixture.

The candidate is asked to reason about the contract ``auto_router/contracts_shim.py``
advertises: that every repo emits the single source-of-truth envelope. This script
asserts the review reached the three places that promise is not kept, against the
exact answer text the harness captured.

It reads one file (the answer path given as ``argv[1]``), prints a
finding-by-finding report, and exits non-zero when a required finding is absent. No
network, no database, no credentials.
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
        "states the contract the module advertises: one source of truth for the envelope",
        ("source",),
        (
            "single source of truth",
            "single-source-of-truth",
            "one source of truth",
            "canonical",
            "re-export",
            "one definition",
            "same envelope everywhere",
            "uniformly",
        ),
    ),
    (
        "identifies the silent ImportError fallback as a second, independent definition",
        # The construct anchor is the *fallback branch itself*, not the literal
        # token "ImportError". Requiring that exact identifier rejected a correct
        # answer that said "falling back to local mirrors ... a divergent local
        # implementation", which is the same fact in the model's own words.
        ("fallback",),
        (
            "fallback",
            "silently",
            "silent",
            "mirror",
            "local",
            "second definition",
            "diverg",
            "when the canonical",
            "when assistx",
            "not importable",
            "unavailable",
        ),
    ),
    (
        "notices TraceEvent and TraceGroup are empty placeholders in that fallback",
        ("traceevent",),
        (
            "pass",
            "placeholder",
            "empty",
            "stub",
            "no-op",
            "no attributes",
            "cannot hold",
            "holds no",
            "not a type",
            "trivial",
        ),
    ),
    (
        "notes the failure surfaces at use rather than at import",
        (),
        (
            "at use",
            "when constructed",
            "when constructed, or when a consumer",
            "later",
            "far from the import",
            "downstream",
            "only when",
            "deferred",
            # Added after a real 30B answer was rejected for saying it better
            # than the reference did: "cause late runtime failures for consumers
            # expecting real models, and the divergence is not observable at
            # import time". That is the finding. "later" was already in this
            # list, but the clause carried "late runtime failures" instead.
            "late runtime",
            "runtime failure",
            "not observable at import",
            "invisible at import",
            "not detectable at import",
            "no error at import",
            "at instantiation",
        ),
    ),
    (
        "notices SCHEMA_VERSION becomes a hardcoded literal that can drift from canonical",
        ("schema_version",),
        (
            "hardcoded",
            "hard-coded",
            "literal",
            "drift",
            "frozen",
            "copy",
            "duplicat",
            "out of step",
            "out of sync",
            "stale",
            "no one compares",
            "not compared",
        ),
    ),
    (
        "notes that _USING_CANONICAL is exported but read by nobody",
        ("_using_canonical",),
        (
            "nothing reads",
            "never read",
            "no one reads",
            "unused",
            "unconsumed",
            "not consulted",
            "never consulted",
            "no consumer",
            "ignored",
            "informational only",
            "signals nothing",
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

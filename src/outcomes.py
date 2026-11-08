# =============================================================================
# src/outcomes.py: the typed outcomes and their fixed sentences
# =============================================================================
"""
An outcome is what a question gets in place of figures: a code and one fixed
sentence, never free text and never a model's words (the reviewer's Phase 2
brief, sections 2 and 5.3). A sentence here names no member and holds no
placeholder, so no outcome can echo a name the role may not see.

Part 2c holds one outcome, nothing_promoted: what a session answers before
anyone has promoted a pair in it. Part 2d adds the others (an unknown metric
or member, a dimension, filter or comparison not allowed, an invalid period,
limit or sort, an entity not permitted, no data), each with its sentence here.

This module imports neither the database engine nor the query parser
(tests/test_boundaries.py).
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["NOTHING_PROMOTED", "SENTENCES", "Outcome", "outcome"]

SENTENCES = {
    "nothing_promoted": "Nothing has been promoted yet. Review a batch first.",
}


@dataclass(frozen=True)
class Outcome:
    """A typed outcome: its code and its fixed sentence."""
    code: str
    sentence: str


def outcome(code: str) -> Outcome:
    """The outcome of a code; a code with no sentence here is a KeyError, never free text."""
    return Outcome(code, SENTENCES[code])


NOTHING_PROMOTED = outcome("nothing_promoted")

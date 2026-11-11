# =============================================================================
# src/outcomes.py: the typed outcomes and their fixed sentences
# =============================================================================
"""
An outcome is what a question gets in place of figures: a code and one fixed
sentence, never free text and never a model's words (the reviewer's Phase 2
brief, sections 2 and 6.1). A sentence names no member, so no outcome can
echo a name the role may not see, or a name it was given.

The sentences are templates that could be filled only with values on an
allow-list; the allow-list (PLACEHOLDERS) is empty, so every sentence is
fixed, and outcome() takes a code and nothing else. A test fails if a
sentence holds a placeholder that is not on the allow-list.

The codes, in the order validation checks them (src/spec.py), then the
outcomes of running a question:
- nothing_promoted: no pair is promoted in the session (part 2c);
- specification_invalid: the text is not a specification of exactly the
  eight keys with values of the right JSON types, or a Specification built
  by hand has a field of the wrong type by the same rules (3a, F24);
- unknown_metric, unknown_member, dimension_not_allowed, filter_not_allowed,
  comparison_not_allowed, prior_year_not_available, period_invalid,
  limit_invalid, sort_invalid, entity_not_permitted: a refusal by
  validation;
- no_data: the window holds no month of the version (with its coverage and
  gaps, in an Answer);
- engine_error: the engine stopped the query; the detail is kept for the
  audit of Phase 3, never shown, and no SQL is echoed.

This module imports neither the database engine nor the query parser
(tests/test_boundaries.py).
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["ENGINE_ERROR", "NOTHING_PROMOTED", "NO_DATA", "PLACEHOLDERS", "SENTENCES",
           "Outcome", "outcome"]

SENTENCES = {
    "nothing_promoted": "Nothing has been promoted yet. Review a batch first.",
    "specification_invalid": "The question could not be read: it is not a specification of "
                             "the expected form.",
    "unknown_metric": "That metric is not in the registry.",
    "unknown_member": "A name in the question is not a member of the plan.",
    "dimension_not_allowed": "That metric cannot be grouped that way.",
    "filter_not_allowed": "That metric cannot be filtered that way.",
    "comparison_not_allowed": "That comparison is not available for that metric and version.",
    "prior_year_not_available": "Prior-year comparisons are not available: the plan holds one "
                                "fiscal year.",
    "period_invalid": "That period is not valid: ask for a month, a quarter, the year to date "
                      "or the fiscal year, within FY2026.",
    "limit_invalid": "The number of rows must be between 1 and 100.",
    "sort_invalid": "The rows can be sorted only by the value or by a dimension they are "
                    "grouped by.",
    "entity_not_permitted": "That entity is not available to your role.",
    "no_data": "The version holds no figures for the period asked for.",
    "engine_error": "The figures could not be computed: the engine stopped the query.",
}

# The values a sentence may be filled with, by placeholder name: none.
PLACEHOLDERS: dict = {}


@dataclass(frozen=True)
class Outcome:
    """A typed outcome: its code and its fixed sentence."""
    code: str
    sentence: str


def outcome(code: str) -> Outcome:
    """The outcome of a code; a code with no sentence here is a KeyError, never free text."""
    return Outcome(code, SENTENCES[code])


NOTHING_PROMOTED = outcome("nothing_promoted")
NO_DATA = outcome("no_data")
ENGINE_ERROR = outcome("engine_error")

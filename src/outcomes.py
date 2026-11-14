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
  audit of Phase 3, never shown, and no SQL is echoed (session.answer hands
  the engine's message to a caller who asks for it, and the outcome stays
  this one fixed object).

The question path of Phase 3 (part 3b, src/ask.py) adds seven, in the order
it meets them:
- question_invalid: the question fails the checks before anything is sent
  (1 to 500 characters after trimming, plain text);
- out_of_scope, needs_clarification, change_not_possible and
  currency_not_supported: the model refused the question with that reason;
- session_ended: the session was closed (SessionError) before the answer;
- audit_unavailable: the record could not be written, so the answer is not
  shown.

This module imports neither the database engine nor the query parser
(tests/test_boundaries.py).
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["AUDIT_UNAVAILABLE", "ENGINE_ERROR", "NOTHING_PROMOTED", "NO_DATA", "PLACEHOLDERS",
           "QUESTION_INVALID", "SENTENCES", "SESSION_ENDED", "Outcome", "outcome"]

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
    # Part 3b: the question path (the reviewer's Phase 3 brief, 4.5).
    "question_invalid": "Ask a question of between 1 and 500 characters, in plain text.",
    "out_of_scope": "That is not a question this pipeline can answer: it answers questions "
                    "about the plan's figures.",
    "needs_clarification": "The question is ambiguous. Say which metric, version, entity and "
                           "period you mean.",
    "change_not_possible": "This pipeline answers questions. It does not change data, "
                           "definitions or access.",
    "currency_not_supported": "All figures are in euros. Conversion to another currency is not "
                              "available.",
    "session_ended": "Your session ended. Review the batch again.",
    "audit_unavailable": "The answer was not shown because the record of it could not be "
                         "written.",
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
QUESTION_INVALID = outcome("question_invalid")
SESSION_ENDED = outcome("session_ended")
AUDIT_UNAVAILABLE = outcome("audit_unavailable")

# =============================================================================
# src/checks.py: the numeric and direction checks, flags for the reviewer
# =============================================================================
"""
Two checks over any prose and the figures Python computed for it, so a
template's sentences and the model's sentences pass through the same code.
A flag is for the reviewer: a check never rewrites a sentence.

The numeric check. Every number in the prose is read with its formatting
(thousands separators, k, m and bn in any case, per cent, points, brackets
for a negative) and must match a computed figure at the precision it is
written in: "£2.48m" and "£2.48M" match 2,477,550, "£2,477,560" does not. A
lone b is billions only after a currency sign or code ("£2.4B"), and a
number is always read whole, never as its first digits ("£2.48M" is never
"£2"). A number is read against the figure whose name the clause gives
("the group margin was 30%" is flagged when 30% is another unit's margin),
and against every figure of its kind when no name is given. An amount in
another currency than the run's is flagged, whether written with a sign or
a code (US$, EUR, 2.48m EUR). A bare year (1900 to 2099, no unit) is a date,
not a figure (item 56), and so is a day (1 to 31, no unit) beside a month
name, full or three letters, before or after it: "15 June", "Jun 15"
(Phase 0 verdict, finding 3). A month name is capitalised, so "15 may" is
still a figure.

The direction check (item 43). A word that gives a figure a direction must
agree with what Python computed for it:
- up or down (above, below, rose, fell, climbed, slipped, went up...) with
  the sign of the figure's variance against the comparator named (budget,
  last year, forecast), or the nearest one named before it in the sentence;
- favourable or adverse (beat, improved, missed, deteriorated...) with the
  variance's polarity, so a cost below budget is favourable and "beat" it;
  "stronger" and "weaker" are left out, being ambiguous for a cost (Phase 0
  verdict, finding 2);
- a profit or a loss with the sign of the figure itself;
- offset language only where the sentence names one favourable and one
  adverse movement, so a cost overspend can never "offset" a shortfall.
A word is read against the nearest figure name before it in its clause,
then the nearest after it, then the nearest earlier in the sentence. When
the figures cannot settle a word (no variance, or a comparator that leaves
both directions possible), no flag is raised: the checks flag what is
wrong, not what is merely unproven.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable, Optional, Sequence

MONEY, PERCENT, POINTS, COUNT = "money", "percent", "points", "count"
NUMERIC, DIRECTION = "numeric", "direction"


@dataclass(frozen=True)
class Figure:
    """One number Python computed, under the name the prose uses for it."""
    name: str
    value: Decimal                         # signed; money in whole units, 32 for 32%
    unit: str                              # MONEY, PERCENT, POINTS or COUNT
    against: Optional[str] = None          # a variance against "budget", "last year" or "forecast"
    higher_is_better: Optional[bool] = None   # the line's polarity, for a variance
    basis: str = "actual"                  # for a figure itself: "actual", "budget", "last year"

    @property
    def polarity(self) -> Optional[str]:
        if self.against is None or self.higher_is_better is None or self.value == 0:
            return None
        return "favourable" if (self.value > 0) == self.higher_is_better else "adverse"


@dataclass(frozen=True)
class Flag:
    """What a check found, for the reviewer: the words, the figure they were read against, why."""
    kind: str                  # NUMERIC or DIRECTION
    text: str
    figure: Optional[str]
    reason: str


# -- Reading the prose ------------------------------------------------------------

_CURRENCY_OF = {"£": "GBP", "€": "EUR", "$": "USD", "US$": "USD", "¥": "JPY",
                "GBP": "GBP", "EUR": "EUR", "USD": "USD", "JPY": "JPY", "CHF": "CHF"}
_CODES = "GBP|EUR|USD|JPY|CHF"
# The number is an atomic group, so a token is never its first digits: when
# what follows a number is not read, the number is not read either.
_TOKEN = re.compile(
    r"(?<![\w.])"
    rf"(?:(?P<cur>US\$|£|€|\$|¥|(?:{_CODES})\s?))?"
    r"(?P<num>(?>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?))"
    r"(?:(?P<scale>(?i:bn|k|m)|(?(cur)(?i:b)|(?!)))"
    r"|\s(?P<word>billion|million|thousand)"
    r"|\s?(?P<pct>%)"
    r"|\s(?P<pctword>per\s?cent|percent)"
    r"|\s?(?P<pts>percentage\s+points|points|pts|pt))?"
    rf"(?:\s?(?P<code>{_CODES}))?"
    r"(?![A-Za-z0-9])"
)
_EXPONENT = {"k": 3, "thousand": 3, "m": 6, "million": 6, "b": 9, "bn": 9, "billion": 9}
# A day beside a month name, before or after it: "15 June", "June 15", "15 Jun".
_MONTHS = ("January|February|March|April|May|June|July|August|September|October|November|"
           "December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept|Sep|Oct|Nov|Dec")
_MONTH_AFTER = re.compile(rf"\s+(?:{_MONTHS})(?![A-Za-z])")
_MONTH_BEFORE = re.compile(rf"(?<![A-Za-z])(?:{_MONTHS})\.?\s+\Z")

_COMPARATORS = {
    "budget": ("budget", "budgeted", "plan", "planned"),
    "last year": ("last year", "prior year", "the previous year", "a year earlier", "a year ago"),
    "forecast": ("forecast",),
}
_UP = ("higher than", "higher", "above", "ahead of", "increased", "increases", "increase",
       "rose", "risen", "rises", "rise", "grew", "grown", "grows", "growth", "exceeded",
       "exceeds", "over budget", "overspend", "overspent",
       "climbed", "surged", "jumped", "gained", "went up")
_DOWN = ("lower than", "lower", "below", "behind", "decreased", "decreases", "decrease",
         "fell", "fallen", "falls", "fall", "dropped", "drop", "declined", "declines",
         "decline", "shortfall", "short of", "under budget", "underspend", "underspent",
         "slipped", "dipped", "plunged", "sank", "went down")
# Read by polarity, so a cost that "beat" budget came in below it. "Stronger"
# and "weaker" are left out: for a cost, either could mean either.
_FAVOURABLE = ("favourable", "favourably", "favorable", "better than",
               "beat", "beaten", "beating", "outperformed", "outperforming", "improved",
               "improvement")
_ADVERSE = ("adverse", "adversely", "unfavourable", "unfavorable", "worse than",
            "missed", "miss", "underperformed", "underperforming", "deteriorated",
            "deterioration", "worsened")
_PROFIT = ("profitable", "profit")
_LOSS = ("loss-making", "loss")
_OFFSET = ("partially offset", "partly offset", "offsetting", "offsets", "offset",
           "compensated", "cushioned", "mitigated")
_CLAIMS = ([(w, "up") for w in _UP] + [(w, "down") for w in _DOWN]
           + [(w, "favourable") for w in _FAVOURABLE] + [(w, "adverse") for w in _ADVERSE]
           + [(w, "profit") for w in _PROFIT] + [(w, "loss") for w in _LOSS]
           + [(w, "offset") for w in _OFFSET])


def _phrase(words: str) -> str:
    return r"\s+".join(re.escape(w) for w in words.split())


def _alternation(phrases: Iterable[str]) -> str:
    return "|".join(_phrase(p) for p in sorted(phrases, key=len, reverse=True))


_CLAIM_OF = {w: claim for w, claim in _CLAIMS}
# Phrases whose words would read as a claim but are not one.
_NOT_CLAIMS = re.compile(r"(?<![\w-])(?:profit\s+(?:and|or|&)\s+loss|profit-and-loss|"
                         r"broken\s+down)(?![\w-])", re.IGNORECASE)
_DIRECTION = re.compile(
    rf"(?<![\w-])(?:{_alternation(_CLAIM_OF)}|(?P<updown>up|down)(?=\s+(?:on|by|from|against|"
    rf"versus|vs|compared|[£€$\d])))(?![\w-])", re.IGNORECASE)
_COMPARATOR = re.compile(
    rf"(?<![\w-])(?:{_alternation(a for aliases in _COMPARATORS.values() for a in aliases)})"
    rf"(?![\w-])", re.IGNORECASE)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_CLAUSE = re.compile(r"[,;:]\s+|\s+(?:while|whereas|but)\s+", re.IGNORECASE)


def _comparator_of(alias: str) -> str:
    alias = " ".join(alias.lower().split())
    return next(key for key, aliases in _COMPARATORS.items() if alias in aliases)


@dataclass(frozen=True)
class _Span:
    start: int
    end: int
    text: str


class _Prose:
    """The prose split into sentences and clauses, with the figure names found in it."""

    def __init__(self, text: str, figures: Sequence[Figure], labels: Sequence[str] = ()):
        self.text = text
        self.sentences = self._spans(_SENTENCE, 0, len(text))
        self.clauses = [c for s in self.sentences for c in self._spans(_CLAUSE, s.start, s.end)]
        self.names: list[_Span] = []
        taken: list[tuple[int, int]] = []
        for name in sorted({f.name for f in figures} | set(labels), key=len, reverse=True):
            pattern = re.compile(rf"(?<![\w-]){_phrase(name)}(?![\w-])", re.IGNORECASE)
            for m in pattern.finditer(text):
                if not any(m.start() < e and s < m.end() for s, e in taken):
                    taken.append((m.start(), m.end()))
                    if name in {f.name for f in figures}:
                        self.names.append(_Span(m.start(), m.end(), name))
        self.names.sort(key=lambda n: n.start)
        # The prose with every name, label and non-claim phrase blanked, so
        # their words and digits are never read as a figure or a direction.
        taken += [(m.start(), m.end()) for m in _NOT_CLAIMS.finditer(text)]
        masked = list(text)
        for s, e in taken:
            masked[s:e] = " " * (e - s)
        self.masked = "".join(masked)

    def _spans(self, separator: re.Pattern, start: int, end: int) -> list[_Span]:
        spans, at = [], start
        for m in separator.finditer(self.text, start, end):
            spans.append(_Span(at, m.start(), self.text[at:m.start()]))
            at = m.end()
        spans.append(_Span(at, end, self.text[at:end]))
        return spans

    def _within(self, spans: list[_Span], position: int) -> _Span:
        return next(s for s in spans if s.start <= position <= s.end)

    def bound_name(self, position: int) -> Optional[str]:
        """The figure a word at position speaks of: clause before, clause after, sentence before."""
        clause = self._within(self.clauses, position)
        sentence = self._within(self.sentences, position)
        before = [n for n in self.names if clause.start <= n.start and n.end <= position]
        if before:
            return before[-1].text
        after = [n for n in self.names if position <= n.start and n.end <= clause.end]
        if after:
            return after[0].text
        earlier = [n for n in self.names if sentence.start <= n.start and n.end <= position]
        return earlier[-1].text if earlier else None

    def comparators(self, position: int) -> set[str]:
        """The comparators the clause names; else the nearest named before it in the sentence."""
        clause = self._within(self.clauses, position)
        found = {_comparator_of(m.group(0))
                 for m in _COMPARATOR.finditer(self.masked, clause.start, clause.end)}
        if found:
            return found
        sentence = self._within(self.sentences, position)
        earlier = list(_COMPARATOR.finditer(self.masked, sentence.start, position))
        return {_comparator_of(earlier[-1].group(0))} if earlier else set()

    def basis_before(self, position: int) -> Optional[str]:
        """A comparator in the three words before position: 'a budget profit'."""
        window = self.masked[max(0, position - 40):position].split()[-3:]
        found = _COMPARATOR.search(" ".join(window))
        return _comparator_of(found.group(0)) if found else None


# -- The numeric check ----------------------------------------------------------------

def _matches(value: Decimal, places: int, exponent: int, figure: Decimal) -> bool:
    step = Decimal(1).scaleb(-places)
    return abs(figure).scaleb(-exponent).quantize(step, rounding=ROUND_HALF_UP) == value


def _unit_of(m: re.Match) -> tuple[Optional[str], int]:
    scale = (m.group("scale") or m.group("word") or "").lower()
    if m.group("cur") or m.group("code") or scale:
        return MONEY, _EXPONENT.get(scale, 0)
    if m.group("pct") or m.group("pctword"):
        return PERCENT, 0
    if m.group("pts"):
        return POINTS, 0
    return None, 0


def _bare(m: re.Match) -> bool:
    """A number written with no currency, scale or unit."""
    return not any(m.group(g) for g in ("cur", "code", "scale", "word", "pct", "pctword", "pts"))


def _is_year(m: re.Match) -> bool:
    raw = m.group("num")
    return len(raw) == 4 and raw.isdigit() and 1900 <= int(raw) <= 2099 and _bare(m)


def _is_day(m: re.Match, text: str) -> bool:
    """A bare 1 to 31 with a month name just after it ("15 June") or just before it ("June 15")."""
    raw = m.group("num")
    if not (raw.isdigit() and len(raw) <= 2 and 1 <= int(raw) <= 31 and _bare(m)):
        return False
    return bool(_MONTH_AFTER.match(text, m.end()) or _MONTH_BEFORE.search(text[:m.start()]))


def numeric_flags(text: str, figures: Sequence[Figure], *, currency: str,
                  labels: Sequence[str] = ()) -> list[Flag]:
    """Every number in text that no computed figure supports, once each, in order."""
    prose = _Prose(text, figures, labels)
    flags: list[Flag] = []
    for m in _TOKEN.finditer(prose.masked):
        if _is_year(m) or _is_day(m, prose.masked):
            continue
        token = text[m.start():m.end()].strip()
        if token in [f.text for f in flags]:
            continue
        written = (m.group("cur") or m.group("code") or "").strip()
        if written and _CURRENCY_OF[written] != currency:
            flags.append(Flag(NUMERIC, token, None,
                              f"'{token}' is in another currency than the run's {currency}."))
            continue
        raw = m.group("num").replace(",", "")
        value = Decimal(raw)
        places = len(raw.split(".")[1]) if "." in raw else 0
        unit, exponent = _unit_of(m)
        name = prose.bound_name(m.start())
        of_kind = [f for f in figures if unit is None or f.unit == unit]
        named = [f for f in of_kind if f.name == name]
        candidates = named or of_kind
        if not any(_matches(value, places, exponent, f.value) for f in candidates):
            where = f"for {name}" if named else "Python computed"
            flags.append(Flag(NUMERIC, token, name if named else None,
                              f"'{token}' matches no figure {where}."))
    return flags


# -- The direction check ----------------------------------------------------------

def _described(figure: Figure) -> str:
    against = f" against {figure.against}" if figure.against else ""
    polarity = f" ({figure.polarity})" if figure.polarity else ""
    return f"{figure.value:,}{against}{polarity}"


def _word_flag(prose: _Prose, m: re.Match, figures: Sequence[Figure]) -> Optional[Flag]:
    word = " ".join(prose.text[m.start():m.end()].split())
    claim = "up" if (m.group("updown") or "").lower() == "up" else (
        "down" if m.group("updown") else _CLAIM_OF[" ".join(word.lower().split())])
    name = prose.bound_name(m.start())
    if name is None:
        return None
    own = [f for f in figures if f.name == name]
    if claim in ("profit", "loss"):
        basis = prose.basis_before(m.start()) or "actual"
        levels = [f for f in own if f.against is None and f.basis == basis and f.value != 0]
        agree = [f for f in levels if (f.value > 0) == (claim == "profit")]
        if levels and not agree:
            return Flag(DIRECTION, word, name,
                        f"'{word}' does not agree with {name}: Python computed "
                        f"{', '.join(_described(f) for f in levels)}.")
        return None
    wanted = prose.comparators(m.start())
    variances = [f for f in own if f.against is not None and f.value != 0
                 and (not wanted or f.against in wanted)]
    if claim in ("up", "down"):
        agree = [f for f in variances if (f.value > 0) == (claim == "up")]
        judged = variances
    else:
        judged = [f for f in variances if f.polarity]
        agree = [f for f in judged if f.polarity == claim]
    if judged and not agree:
        return Flag(DIRECTION, word, name,
                    f"'{word}' does not agree with {name}: Python computed "
                    f"{', '.join(_described(f) for f in judged)}.")
    return None


def _offset_flag(prose: _Prose, m: re.Match, figures: Sequence[Figure]) -> Optional[Flag]:
    word = " ".join(prose.text[m.start():m.end()].split()).lower()
    sentence = prose._within(prose.sentences, m.start())
    names = list(dict.fromkeys(n.text for n in prose.names
                               if sentence.start <= n.start and n.end <= sentence.end))
    wanted = {_comparator_of(c.group(0))
              for c in _COMPARATOR.finditer(prose.masked, sentence.start, sentence.end)}
    moves = {f.polarity for f in figures if f.name in names and f.polarity
             and (not wanted or f.against in wanted)}
    if {"favourable", "adverse"} <= moves:
        return None
    if not names:
        said = "no line is named beside it"
    elif not moves:
        said = f"Python computed no favourable or adverse movement for {' and '.join(names)}"
    else:
        said = f"{' and '.join(names)} all moved {moves.pop()}ly"
    return Flag(DIRECTION, word, None,
                f"'{word}' needs one favourable and one adverse movement, but {said}.")


def direction_flags(text: str, figures: Sequence[Figure]) -> list[Flag]:
    """Every direction word in text that the computed figures contradict, in order."""
    prose = _Prose(text, figures)
    flags: list[Flag] = []
    for m in _DIRECTION.finditer(prose.masked):
        word = " ".join(m.group(0).lower().split())
        if not m.group("updown") and _CLAIM_OF.get(word) == "offset":
            flag = _offset_flag(prose, m, figures)
        else:
            flag = _word_flag(prose, m, figures)
        if flag and (flag.text, flag.figure) not in [(f.text, f.figure) for f in flags]:
            flags.append(flag)
    return flags


def check_text(text: str, figures: Sequence[Figure], *, currency: str,
               labels: Sequence[str] = ()) -> list[Flag]:
    """Both checks: the numeric flags, then the direction flags. The text is never changed."""
    return (numeric_flags(text, figures, currency=currency, labels=labels)
            + direction_flags(text, figures))

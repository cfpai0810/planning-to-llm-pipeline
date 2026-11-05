# =============================================================================
# src/names.py: the member-name allow-list
# =============================================================================
"""
Which member names may ever enter a prompt (brief 5.3). A name passes when it
is made of Unicode letters, digits, spaces and the characters & . , - / ( ) %
only, is at most 50 characters and six words long, and has no leading or
trailing space.

- A letter is a character of a Unicode letter category (Lu, Ll, Lt, Lm, Lo),
  so a precomposed accented letter passes and a separate combining accent
  does not. A digit is a decimal digit (Nd). A space is the plain space,
  U+0020: tabs, line breaks, the no-break space and zero-width characters fail.
- A character is one Unicode code point. A word is a run of characters
  between spaces, so "R & D" is three words.

check() returns the reasons a name fails, as fixed codes in a fixed order,
and never changes the name: the extractor lands the member raw, records the
reasons, and part 1d keeps a failing member out of the star schema and out of
every prompt. docs/controls.md states the limit: a short plain-English
sentence passes any character list, so the allow-list narrows the route into
a prompt and does not close it.
"""

from __future__ import annotations

import unicodedata

MAX_CHARACTERS = 50
MAX_WORDS = 6
SYMBOLS = "&.,-/()%"
SPACE = " "
LETTER_CATEGORIES = frozenset({"Lu", "Ll", "Lt", "Lm", "Lo"})
DIGIT_CATEGORY = "Nd"

# The fixed wording of each reason, in the order check() reports them.
REASONS = {
    "empty": "the name is empty",
    "characters": "it holds a character other than letters, digits, spaces and & . , - / ( ) %",
    "length": f"it is longer than {MAX_CHARACTERS} characters",
    "words": f"it has more than {MAX_WORDS} words",
    "space": "it starts or ends with a space",
}


def _allowed_character(character: str) -> bool:
    if character == SPACE or character in SYMBOLS:
        return True
    category = unicodedata.category(character)
    return category in LETTER_CATEGORIES or category == DIGIT_CATEGORY


def check(name: str) -> tuple[str, ...]:
    """The reasons a name fails the allow-list, in a fixed order; () when it passes."""
    if not isinstance(name, str):
        raise TypeError(f"a member name is text, not {type(name).__name__}")
    if name == "":
        return ("empty",)
    found = []
    if not all(_allowed_character(c) for c in name):
        found.append("characters")
    if len(name) > MAX_CHARACTERS:
        found.append("length")
    if len([word for word in name.split(SPACE) if word]) > MAX_WORDS:
        found.append("words")
    if name[0] == SPACE or name[-1] == SPACE:
        found.append("space")
    return tuple(found)


def allowed(name: str) -> bool:
    """True when the name may enter a prompt."""
    return check(name) == ()

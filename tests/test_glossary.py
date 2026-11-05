"""One language: docs/glossary.md and the variants it retires (brief 6.5).

docs/glossary.md lists the agreed terms, each with the variants it retires.
This test reads the text a reader meets (the README and docs, the command
line, the code's comments, docstrings and strings, the SQL files, and the
comments and docstrings of the tests) and fails if a retired variant comes
back. It follows the P6 glossary test: a string that is a single identifier
(a dictionary key, a column name) is not prose and is not read.
"""

import io
import re
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GLOSSARY = ROOT / "docs" / "glossary.md"

# The agreed terms, as docs/glossary.md writes them.
TERMS = ("batch", "batch record", "break", "control", "reconcile", "acknowledge",
         "acknowledgement", "pair", "promote", "promotion", "plan", "Actual", "Budget",
         "Forecast", "version", "entity", "account", "line item", "period", "ledger",
         "ledger sign", "presentation sign", "star schema")

# The retired variants: (the term to say instead, the variants as the glossary
# lists them, a pattern that finds them in prose).
RETIRED = (
    ("batch", ("dataset", "data set", "data load"),
     r"(?i)\bdata ?sets?\b|\bdata loads?\b"),
    ("break", ("discrepancy", "mismatch", "reconciling item", "exception item"),
     r"(?i)\bdiscrepanc(y|ies)\b|\bmismatch(es)?\b|\breconciling items?\b|\bexception items?\b"),
    ("acknowledge", ("sign off", "sign-off", "approve a break"),
     r"(?i)\bsign(ed|s)?[ -]?off\b|\bsignoffs?\b|\bapprov(e|es|ed|ing) (a|the|each|every) break"),
    ("promote", ("go live", "make live", "publish a batch", "release a batch"),
     r"(?i)\b(go|goes|went|make|made) live\b|\b(publish|release)(es|ed|ing|s)? (a|the) batch"),
    ("plan", ("plan file", "planning file"), r"(?i)\bplan(ning)? files?\b"),
    ("ledger", ("ERP actuals", "ERP extract", "ERP file", "general ledger", "trial balance"),
     r"\bERP (actuals|extract|file)s?\b|(?i:\bgeneral ledger\b|\btrial balances?\b)"),
    ("Budget", ("AOP", "annual operating plan"), r"\bAOP\b|(?i:\bannual operating plan\b)"),
    ("Forecast", ("outlook", "latest estimate"), r"(?i)\boutlooks?\b|\blatest estimates?\b"),
    ("entity", ("business unit", "subsidiary", "legal entity", "company code"),
     r"(?i)\bbusiness units?\b|\bsubsidiar(y|ies)\b|\blegal entit(y|ies)\b|\bcompany codes?\b"),
    ("account", ("GL account", "GL code", "nominal code", "nominal account"),
     r"\bGL (accounts?|codes?)\b|(?i:\bnominal (codes?|accounts?)\b)"),
    ("period", ("time period", "fiscal period", "posting period"),
     r"(?i)\b(time|fiscal|posting) periods?\b"),
    ("presentation sign", ("reporting sign", "display sign", "natural sign"),
     r"(?i)\b(reporting|display|natural) signs?\b"),
    ("star schema", ("data mart",), r"(?i)\bdata marts?\b"),
)


def _reader_text(path: Path, *, docs_only: bool = False) -> list:
    """(line, text) a reader sees: all of a Markdown or SQL file; in Python, the comments,
    docstrings and strings, but not a string that is a single identifier. docs_only keeps
    the comments and docstrings alone, for a test file, whose other strings are test data."""
    text = path.read_text(encoding="utf-8")
    if path.suffix in (".md", ".sql"):
        return list(enumerate(text.splitlines(), start=1))
    kinds = {tokenize.COMMENT, tokenize.STRING, getattr(tokenize, "FSTRING_MIDDLE", -1)}
    pieces = []
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type not in kinds or re.fullmatch(r"[rbfRBF]*['\"]*[\w.{}:-]*['\"]*", tok.string):
            continue
        if docs_only and not (tok.type == tokenize.COMMENT or tok.string.startswith('"""')):
            continue
        pieces += [(tok.start[0] + i, line) for i, line in enumerate(tok.string.splitlines())]
    return pieces


def _reader_files() -> list:
    patterns = ("README.md", "docs/*.md", "main.py", "config.py", "src/*.py",
                "streamlit_app/*.py", "streamlit_app/*/*.py", "scripts/*.py", "sql/*.sql")
    files = [p for pattern in patterns for p in sorted(ROOT.glob(pattern))]
    return [p for p in files if p != GLOSSARY]      # the glossary lists them on purpose


def _retired(text: str) -> list:
    return [f"{', '.join(variants)} (say {term}): {m.group(0)!r}"
            for term, variants, pattern in RETIRED for m in re.finditer(pattern, text)]


def _rows() -> dict:
    """The glossary's table rows: {term: the text of its Retired column}."""
    text = GLOSSARY.read_text(encoding="utf-8")
    return {m[1]: m[2] for m in re.finditer(r"^\| \*\*(.+?)\*\* \|.*\|(.*)\|$", text,
                                            flags=re.MULTILINE)}


class TestGlossary:
    def test_the_glossary_names_every_agreed_term(self):
        rows = _rows()
        assert tuple(rows) == TERMS
        for seed in ("batch", "break", "acknowledge", "promote", "plan", "Actual", "Budget",
                     "Forecast", "entity", "account", "period", "ledger"):
            assert seed in rows, seed

    def test_each_term_lists_the_variants_it_retires(self):
        rows = _rows()
        for term, variants, _ in RETIRED:
            listed = [v.strip() for v in rows[term].split(",") if v.strip()]
            assert listed == list(variants), term
        assert {t for t, row in rows.items() if row.strip()} == {t for t, _, _ in RETIRED}

    def test_no_retired_variant_in_text_a_reader_sees(self):
        found = [f"{path.relative_to(ROOT).as_posix()}:{line}: {hit}"
                 for path in _reader_files() for line, text in _reader_text(path)
                 for hit in _retired(text)]
        found += [f"{path.relative_to(ROOT).as_posix()}:{line}: {hit}"
                  for path in sorted(ROOT.glob("tests/*.py")) if path.name != Path(__file__).name
                  for line, text in _reader_text(path, docs_only=True)
                  for hit in _retired(text)]
        assert found == []

    def test_each_pattern_fires_on_its_variant_and_not_on_the_term(self):
        for text in ("a new dataset", "the ERP extract", "a GL account", "the AOP",
                     "the latest estimate", "a business unit", "the fiscal period",
                     "the reporting sign", "a discrepancy", "she signed off the break",
                     "the batch goes live", "publish the batch", "the plan file", "a data mart",
                     "a trial balance"):
            assert _retired(text), text
        for text in ("a batch", "the ledger extract", "an account", "the Budget",
                     "the Forecast", "an entity", "the period", "the presentation sign",
                     "a break", "she acknowledged the break", "the pair is promoted",
                     "the planning workbook", "the star schema", "the published figures",
                     "the ledger sign", "file metadata", "a release of the app"):
            assert not _retired(text), text

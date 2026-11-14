"""The numeric and direction checks (src/checks.py): flags for the reviewer, never a rewrite.

Both checks are generic over text and figures, so a template's prose and the
model's prose pass through the same code. The figures below are a small,
self-consistent stand-in month (every total adds up), and the margin example
of the brief: Unit A earns a 50% margin on 10% of the revenue and Unit B 30%
on 90%, so the group margin is 32%, not the 40% a plain average gives.

Item 56 (the numeric check, as P6 left it): amounts in US$ or EUR were not
read as money in another currency, and a bare year was flagged as a figure.
Item 43: a flipped direction ("above" written for below, a loss written as a
profit) raised no flag; P6 checked only offset language.

Phase 0 verdict, finding 2: the direction words missed beat, missed,
improved, deteriorated, outperformed and their kin. Each new word has a
sentence it must flag and one it must pass, on the figures below.
Finding 3: "On 15 June" flagged 15, and "£2.48M" flagged £2. A day beside a
month name is a date, and scale letters are read in any case.

The reviewer's ruling of 14 November on 3c, which allowed this file to change once:
F-a, a written sign ("-", "+" or the minus sign U+2212, right before the number, before its
currency or between the two, touching what follows and not after a letter or a digit) is
compared with the signed figure, and an unsigned number with its size; brackets are not a
sign; "pp" is a points unit word. F-b, strict_names=True flags a number or a direction word
whose own clause names no figure; the default changes nothing. The rulings on the questions
(option A): direction_flags takes labels, and check_text hands its labels to both checks, so a
word inside a label is never read as a direction word; with no labels the direction check gives,
on every sentence this file held at eeed857, exactly the flags eeed857's own check gave.
"""

from decimal import Decimal as D

import pytest

from src.checks import (COUNT, MONEY, PERCENT, POINTS, Figure, Flag, check_text,
                        direction_flags, numeric_flags)


def _variance(name, value, unit, against, higher_is_better):
    return Figure(name, D(value), unit, against=against, higher_is_better=higher_is_better)


# Revenue 2,477,550 against a budget of 2,590,000 and 2,349,280 last year;
# COGS 1,611,180 (budget 1,577,000); opex 875,000 (budget 893,000); so EBIT
# is a loss of 8,630 against a budget profit of 120,000.
MONTH = [
    Figure("Revenue", D("2477550"), MONEY),
    Figure("Revenue", D("2590000"), MONEY, basis="budget"),
    Figure("Revenue", D("2349280"), MONEY, basis="last year"),
    _variance("Revenue", "-112450", MONEY, "budget", True),
    _variance("Revenue", "-4.3", PERCENT, "budget", True),
    _variance("Revenue", "128270", MONEY, "last year", True),
    _variance("Revenue", "5.5", PERCENT, "last year", True),
    Figure("COGS", D("1611180"), MONEY),
    Figure("COGS", D("1577000"), MONEY, basis="budget"),
    _variance("COGS", "34180", MONEY, "budget", False),
    Figure("Opex", D("875000"), MONEY),
    Figure("Opex", D("893000"), MONEY, basis="budget"),
    _variance("Opex", "-18000", MONEY, "budget", False),
    Figure("EBIT", D("-8630"), MONEY),
    Figure("EBIT", D("120000"), MONEY, basis="budget"),
    _variance("EBIT", "-128630", MONEY, "budget", True),
    Figure("Gross margin", D("34.97"), PERCENT),
    Figure("Gross margin", D("39.11"), PERCENT, basis="budget"),
    _variance("Gross margin", "-4.14", POINTS, "budget", True),
    Figure("Headcount", D("212"), COUNT),
    Figure("Region 2 revenue", D("310000"), MONEY),
]
MARGINS = [
    Figure("Unit A margin", D("50"), PERCENT),
    Figure("Unit B margin", D("30"), PERCENT),
    Figure("Group margin", D("32"), PERCENT),
    Figure("Unit A revenue", D("100000"), MONEY),
    Figure("Unit B revenue", D("900000"), MONEY),
    Figure("Group revenue", D("1000000"), MONEY),
]
GOOD_PARAGRAPH = (
    "In June 2026, revenue was £2.48m, £112k (4.3%) below budget but £128k (5.5%) above "
    "last year. COGS was £34k over budget, partly offset by an £18k underspend on opex. "
    "EBIT was a loss of £8.6k against a budget profit of £120k. The gross margin fell "
    "4.1 pts to 35.0%. Headcount was 212, and Region 2 revenue was £310k.")


def _texts(flags):
    return [f.text for f in flags]


class TestNumericCheck:
    def test_a_correct_paragraph_raises_no_flag(self):
        assert check_text(GOOD_PARAGRAPH, MONTH, currency="GBP") == []

    def test_a_number_off_by_one_digit_is_flagged(self):
        flags = numeric_flags("Revenue was £2,477,560.", MONTH, currency="GBP")
        assert _texts(flags) == ["£2,477,560"] and flags[0].kind == "numeric"
        assert flags[0].figure == "Revenue"

    def test_the_formatting_rules_are_read(self):
        # Thousands separators, k and m, per cent, points. Changed on purpose by the ruling of
        # 14 November (F-a): this comment said "brackets for negatives", which was never so;
        # brackets are not a sign, so the bracketed EBIT is an unsigned number, read by its size.
        text = ("Revenue was £2,477,550 (£2.48m), or 2.48 million pounds. EBIT was (£8,630), "
                "(£8.6k). Revenue was 4.3 per cent below budget; the gross margin was 4.1 "
                "points lower.")
        assert numeric_flags(text, MONTH, currency="GBP") == []

    @pytest.mark.parametrize("text", ["Revenue was US$2.48m.", "Revenue was $2.48m.",
                                      "Revenue was EUR 2.48m.", "Revenue was 2.48m EUR.",
                                      "Revenue was €2.48m.", "Revenue was USD 2,477,550."])
    def test_item56_an_amount_in_another_currency_is_flagged(self, text):
        flags = numeric_flags(text, MONTH, currency="GBP")
        assert len(flags) == 1 and "another currency" in flags[0].reason

    @pytest.mark.parametrize("currency, text", [
        ("USD", "Revenue was US$2.48m."), ("USD", "Revenue was $2.48m."),
        ("USD", "Revenue was USD 2,477,550."), ("EUR", "Revenue was EUR 2.48m."),
        ("EUR", "Revenue was 2.48m EUR."), ("EUR", "Revenue was €2.48m.")])
    def test_item56_an_amount_in_the_run_currency_is_read(self, currency, text):
        assert numeric_flags(text, MONTH, currency=currency) == []

    @pytest.mark.parametrize("text", ["In June 2026, revenue was £2.48m.",
                                      "Revenue in 2026 was £2.48m.",
                                      "FY2026 revenue was £2.48m.",
                                      "Revenue for Q1 2026 was £2.48m."])
    def test_item56_a_bare_year_is_not_a_figure(self, text):
        assert numeric_flags(text, MONTH, currency="GBP") == []

    def test_a_name_with_a_digit_is_not_a_figure(self):
        assert numeric_flags("Region 2 revenue was £310k.", MONTH, currency="GBP") == []

    def test_a_label_given_by_the_caller_is_not_a_figure(self):
        assert numeric_flags("For Store 17, revenue was £2.48m.", MONTH, currency="GBP",
                             labels=["Store 17"]) == []

    def test_the_margin_example_a_plain_average_is_flagged(self):
        flags = numeric_flags("The group margin was 40%.", MARGINS, currency="GBP")
        assert _texts(flags) == ["40%"] and flags[0].figure == "Group margin"

    def test_the_margin_example_another_units_figure_is_flagged(self):
        # 30% is a computed figure, but it is Unit B's margin, not the group's.
        flags = numeric_flags("The group margin was 30%.", MARGINS, currency="GBP")
        assert _texts(flags) == ["30%"] and flags[0].figure == "Group margin"

    def test_the_margin_example_stated_correctly_passes(self):
        text = ("The group margin was 32%, with Unit A margin at 50% and Unit B margin at 30%. "
                "Group revenue was £1.0m: Unit A revenue £100k and Unit B revenue £900k.")
        assert numeric_flags(text, MARGINS, currency="GBP") == []

    def test_an_invented_figure_is_flagged_once(self):
        text = "Revenue was £3.1m. Revenue was £3.1m again."
        assert _texts(numeric_flags(text, MONTH, currency="GBP")) == ["£3.1m"]


class TestDirectionCheck:
    @pytest.mark.parametrize("text", [
        "Revenue was £112k below budget.",
        "Revenue fell £112k short of budget.",
        "Revenue grew by £128k on last year.",
        "Revenue was below budget but above last year.",
        "COGS was £34k over budget, an adverse variance.",
        "Opex was £18k under budget, a favourable variance.",
        "EBIT was a loss of £8.6k.",
        "The gross margin fell 4.1 pts against budget.",
        "COGS was £34k over budget, partly offset by an £18k underspend on opex.",
    ])
    def test_a_direction_that_agrees_raises_no_flag(self, text):
        assert direction_flags(text, MONTH) == []

    def test_item43_above_written_for_below_is_flagged(self):
        flags = direction_flags("Revenue was £112k above budget.", MONTH)
        assert [(f.kind, f.text, f.figure) for f in flags] == [("direction", "above", "Revenue")]

    def test_item43_a_rise_written_for_a_fall_on_last_year_is_flagged(self):
        flags = direction_flags("Revenue fell by £128k on last year.", MONTH)
        assert [(f.text, f.figure) for f in flags] == [("fell", "Revenue")]

    def test_item43_a_loss_written_as_a_profit_is_flagged(self):
        flags = direction_flags("EBIT was a profit of £8.6k.", MONTH)
        assert [(f.text, f.figure) for f in flags] == [("profit", "EBIT")]

    def test_a_revenue_shortfall_called_favourable_is_flagged(self):
        flags = direction_flags("Revenue was £112k below budget, a favourable result.", MONTH)
        assert [(f.text, f.figure) for f in flags] == [("favourable", "Revenue")]

    def test_a_cost_saving_called_adverse_is_flagged(self):
        flags = direction_flags("Opex was £18k under budget, an adverse variance.", MONTH)
        assert [(f.text, f.figure) for f in flags] == [("adverse", "Opex")]

    def test_p6s_case_a_cost_overspend_called_an_offset_is_flagged(self):
        # P6's model did this in two of three live runs: both lines moved adversely.
        flags = direction_flags(
            "Revenue was £112k below budget, partly offset by a £34k overspend on COGS.", MONTH)
        assert [(f.text, f.figure) for f in flags] == [("partly offset", None)]
        assert "Revenue" in flags[0].reason and "COGS" in flags[0].reason

    def test_an_offset_with_nothing_to_offset_is_flagged(self):
        flags = direction_flags("Revenue was £112k below budget, partly offset elsewhere.",
                                MONTH)
        assert _texts(flags) == ["partly offset"]

    @pytest.mark.parametrize("text", [
        "COGS, broken down by site, was £1.61m.",
        "The profit and loss account shows revenue of £2.48m.",
        "An increase in COGS of £34k took EBIT to a loss.",
    ])
    def test_ordinary_phrasing_is_not_misread(self, text):
        # "broken down" gives no direction, "profit and loss" names a statement,
        # and each word is read against the nearest name in its own clause.
        assert check_text(text, MONTH, currency="GBP") == []

    def test_a_name_inside_another_name_is_not_read_as_a_direction(self):
        # "profit" inside "Gross profit" is the line's name, not a claim.
        figures = MONTH + [Figure("Gross profit", D("866370"), MONEY)]
        assert direction_flags("Gross profit was £866k.", figures) == []


class TestFinding3DatesAndCapitalScales:
    @pytest.mark.parametrize("text", [
        "On 15 June, revenue was £2.48m.",
        "On June 15, revenue was £2.48m.",
        "On 15th June, revenue was £2.48m.",
        "On June 15th, revenue was £2.48m.",
        "On 15 Jun, revenue was £2.48m.",
        "On Jun 15, revenue was £2.48m.",
        "Revenue on 15 June was £2.48m.",
        "On 1 September 2026, revenue was £2.48m.",
        "On Sept 30, revenue was £2.48m.",
        "Between 3 May and 30 Jun, revenue was £2.48m.",
    ], ids=["15 June", "June 15", "15th June", "June 15th", "15 Jun", "Jun 15", "on 15 June",
            "1 September 2026", "Sept 30", "3 May and 30 Jun"])
    def test_a_day_beside_a_month_name_is_a_date_not_a_figure(self, text):
        assert numeric_flags(text, MONTH, currency="GBP") == []

    @pytest.mark.parametrize("text, flagged", [
        ("In June, 15 stores opened.", "15"),            # a comma between: not beside the month
        ("Revenue rose 15% June on June.", "15%"),        # a figure with a unit is never a day
        ("Revenue rose 2.5 June on June.", "2.5"),        # nor is a decimal
        ("On 32 June, revenue was £2.48m.", "32"),        # no month has a 32nd day
        ("A headcount of 15 may follow.", "15"),          # "may", not "May": not a month name
        ("In June 2026, revenue was £3.1m.", "£3.1m"),    # a year, then an amount still read
    ], ids=["after a comma", "with a unit", "a decimal", "32", "lower-case may", "after a year"])
    def test_a_figure_near_a_month_name_that_is_not_a_day_still_counts(self, text, flagged):
        assert _texts(numeric_flags(text, MONTH, currency="GBP")) == [flagged]

    @pytest.mark.parametrize("text", [
        "Revenue was £2.48M.", "Revenue was £2,478K.", "Revenue was 2.48M.",
        "Revenue was GBP 2.48M.", "EBIT was a loss of £8.6K.", "EBIT was a loss of 8.6K.",
        "Revenue was £0.0025BN.", "Revenue was £0.0025Bn.", "Revenue was £0.0025bn.",
        "Revenue was £0.0025B.", "Revenue was GBP 0.0025B.",
    ], ids=["sign 2.48M", "sign 2,478K", "no sign 2.48M", "code 2.48M", "sign 8.6K", "no sign 8.6K",
            "sign 0.0025BN", "sign 0.0025Bn", "sign 0.0025bn", "sign 0.0025B", "code 0.0025B"])
    def test_scale_letters_in_any_case_after_a_money_amount(self, text):
        # K, M and Bn in any case; a lone B only after a currency sign or code.
        assert numeric_flags(text, MONTH, currency="GBP") == []

    def test_a_wrong_amount_in_capitals_is_flagged_whole_never_as_its_first_digits(self):
        flags = numeric_flags("Revenue was £3.1M.", MONTH, currency="GBP")
        assert _texts(flags) == ["£3.1M"] and flags[0].figure == "Revenue"

    def test_another_currency_in_capitals_is_flagged_whole(self):
        flags = numeric_flags("Revenue was US$2.48M.", MONTH, currency="GBP")
        assert _texts(flags) == ["US$2.48M"] and "another currency" in flags[0].reason

    def test_a_lone_b_is_billions_only_after_a_currency(self):
        # "£2.48B" is £2.48 billion, and wrong. "2.48B" with no sign or code is
        # not read as billions (it would be flagged then), and its 2 is never a figure.
        assert _texts(numeric_flags("Revenue was £2.48B.", MONTH, currency="GBP")) == ["£2.48B"]
        assert numeric_flags("Revenue was 2.48B.", MONTH, currency="GBP") == []


# Finding 2: each new word with a sentence the figures contradict (flagged,
# against the figure named) and one they support (no flag from either check).
# Favourable or adverse: the variance's polarity decides, so a cost below
# budget "beat" it. Up or down: the sign of the variance.
VOCABULARY = [
    # word, wrong, the figure it is read against, right, unsettled
    ("beat", "Revenue beat budget.", "Revenue", "Opex beat budget by £18k.", "Revenue beat."),
    ("beaten", "Revenue had beaten budget.", "Revenue", "Opex had beaten budget by £18k.",
     "Revenue had beaten."),
    ("beating", "COGS is beating budget.", "COGS", "Revenue is beating last year by £128k.",
     "Revenue is beating."),
    ("outperformed", "Revenue outperformed budget by £112k.", "Revenue",
     "Revenue outperformed last year by £128k.", "Revenue outperformed."),
    ("outperforming", "COGS is outperforming budget.", "COGS", "Opex is outperforming budget.",
     "Revenue is outperforming."),
    ("improved", "EBIT improved against budget.", "EBIT", "Revenue improved on last year.",
     "Revenue improved."),
    ("improvement", "The gross margin showed an improvement against budget.", "Gross margin",
     "Opex showed an £18k improvement against budget.", "Revenue showed an improvement."),
    ("missed", "Opex missed budget.", "Opex", "Revenue missed budget by £112k.",
     "Revenue missed."),
    ("miss", "Opex was a miss against budget.", "Opex",
     "Revenue was a £112k miss against budget.", "Revenue was a miss."),
    ("underperformed", "Revenue underperformed last year.", "Revenue",
     "Revenue underperformed budget by £112k.", "Revenue underperformed."),
    ("underperforming", "Opex is underperforming budget.", "Opex",
     "EBIT is underperforming budget.", "Revenue is underperforming."),
    ("deteriorated", "Opex deteriorated against budget.", "Opex",
     "The gross margin deteriorated against budget.", "Revenue deteriorated."),
    ("deterioration", "Revenue showed a deterioration on last year.", "Revenue",
     "EBIT showed a £129k deterioration against budget.", "Revenue showed a deterioration."),
    ("worsened", "Revenue worsened on last year.", "Revenue", "EBIT worsened against budget.",
     "Revenue worsened."),
    ("climbed", "Revenue climbed against budget.", "Revenue",
     "Revenue climbed £128k on last year.", "Revenue climbed."),
    ("surged", "Opex surged against budget.", "Opex", "COGS surged against budget.",
     "Revenue surged."),
    ("jumped", "EBIT jumped against budget.", "EBIT", "Revenue jumped £128k on last year.",
     "Revenue jumped."),
    ("gained", "The gross margin gained against budget.", "Gross margin",
     "Revenue gained £128k on last year.", "Revenue gained."),
    ("went up", "Revenue went up against budget.", "Revenue",
     "COGS went up £34k against budget.", "Revenue went up."),
    ("slipped", "COGS slipped against budget.", "COGS", "Revenue slipped £112k against budget.",
     "Revenue slipped."),
    ("dipped", "Revenue dipped on last year.", "Revenue", "Opex dipped £18k against budget.",
     "Revenue dipped."),
    ("plunged", "Revenue plunged on last year.", "Revenue", "EBIT plunged against budget.",
     "Revenue plunged."),
    ("sank", "COGS sank against budget.", "COGS",
     "The gross margin sank 4.1 pts against budget.", "Revenue sank."),
    ("went down", "COGS went down against budget.", "COGS",
     "Opex went down £18k against budget.", "Revenue went down."),
]
WORDS = [v[0] for v in VOCABULARY]


class TestFinding2DirectionVocabulary:
    @pytest.mark.parametrize("word, wrong, figure", [v[:3] for v in VOCABULARY], ids=WORDS)
    def test_flagged_when_the_figures_say_the_opposite(self, word, wrong, figure):
        flags = direction_flags(wrong, MONTH)
        assert [(f.kind, f.text, f.figure) for f in flags] == [("direction", word, figure)]

    @pytest.mark.parametrize("right", [v[3] for v in VOCABULARY], ids=WORDS)
    def test_unflagged_when_the_figures_agree(self, right):
        assert check_text(right, MONTH, currency="GBP") == []

    @pytest.mark.parametrize("unsettled", [v[4] for v in VOCABULARY], ids=WORDS)
    def test_a_word_the_figures_cannot_settle_raises_no_flag(self, unsettled):
        # No comparator is named, and revenue is below budget but above last
        # year, so either reading is possible: no flag.
        assert direction_flags(unsettled, MONTH) == []

    def test_polarity_decides_so_a_cost_that_beat_budget_is_favourable(self):
        assert direction_flags("Opex beat budget.", MONTH) == []
        flags = direction_flags("COGS beat budget.", MONTH)
        assert [(f.text, f.figure) for f in flags] == [("beat", "COGS")]
        assert "adverse" in flags[0].reason

    def test_a_figure_with_no_variance_settles_nothing(self):
        for text in ("Headcount climbed to 212.", "Region 2 revenue improved to £310k."):
            assert check_text(text, MONTH, currency="GBP") == [], text

    @pytest.mark.parametrize("text", ["COGS was stronger than budget.",
                                      "Revenue was weaker than last year."])
    def test_stronger_and_weaker_are_left_out(self, text):
        # Ambiguous for a cost, so they are not direction words (the brief).
        assert direction_flags(text, MONTH) == []


class TestFlagsAreForTheReviewer:
    def test_the_checks_return_flags_and_never_text(self):
        text = "Revenue was £112k above budget at £2,477,560."
        flags = check_text(text, MONTH, currency="GBP")
        assert flags and all(isinstance(f, Flag) for f in flags)
        assert {f.kind for f in flags} == {"numeric", "direction"}
        assert text == "Revenue was £112k above budget at £2,477,560."

    def test_every_flag_has_a_plain_reason(self):
        text = "Revenue was £112k above budget at £2,477,560, US$1m."
        for flag in check_text(text, MONTH, currency="GBP"):
            assert flag.reason.endswith(".") and "Error" not in flag.reason


# -- The reviewer's ruling of 14 November on 3c ------------------------------------------------------

# The minus sign U+2212, written as an escape so that a reader never takes it for a hyphen.
MINUS = "\u2212"
# The coordinator's probe: Austria's revenue and its variance against Budget, Spain's revenue,
# and the group's EBIT, a loss.
PROBE = [
    Figure("Actual revenue for Calder Foods Austria", D("3983040"), MONEY),
    Figure("Actual revenue for Calder Foods Spain", D("9865590"), MONEY),
    _variance("the variance of revenue for Calder Foods Austria", "118992", MONEY, "budget",
              True),
    _variance("the variance in per cent of revenue for Calder Foods Austria", "3.1", PERCENT,
              "budget", True),
    Figure("Actual EBIT for the group", D("-500000"), MONEY),
]
MEMBERS = ["Calder Foods Austria", "Calder Foods Spain"]
AUSTRIA = "Actual revenue for Calder Foods Austria was {}."
EBIT = "Actual EBIT for the group was {}."


def _flat(text: str) -> str:
    return " ".join(text.split())


class TestFaTheSign:
    @pytest.mark.parametrize("text, flagged", [
        ("The variance of revenue for Calder Foods Austria was -118,992 EUR.", "-118,992 EUR"),
        ("The variance in per cent of revenue for Calder Foods Austria was -3.1%.", "-3.1%"),
        ("Actual revenue for Calder Foods Austria was -3,983,040 EUR.", "-3,983,040 EUR")],
        ids=["variance", "variance in per cent", "value"])
    def test_the_probes_wrong_signs_are_flagged(self, text, flagged):
        flags = check_text(text, PROBE, currency="EUR")
        assert [(f.kind, f.text) for f in flags] == [("numeric", flagged)]

    def test_the_probes_unsigned_negative_is_not_flagged_and_the_docstring_says_so(self):
        # A known limit: a negative level written with no sign and no word such as "loss".
        import src.checks as checks
        assert check_text("Actual EBIT for the group was 500,000 EUR.", PROBE,
                          currency="EUR") == []
        assert check_text("Actual EBIT for the group was a profit of 500,000 EUR.", PROBE,
                          currency="EUR")
        doc = _flat(checks.__doc__)
        assert "Two known limits follow." in doc
        assert ('so is a negative level written with no sign: "EBIT was 500,000 EUR" passes '
                'for a loss of 500,000') in doc

    def test_brackets_are_not_a_sign(self):
        # The probe's second sentence: "(118,992)" is an unsigned number, read by its size.
        import src.checks as checks
        text = "The variance of revenue for Calder Foods Austria was (118,992) EUR."
        assert check_text(text, PROBE, currency="EUR") == []
        assert check_text(EBIT.format("(500,000) EUR"), PROBE, currency="EUR") == []
        doc = _flat(checks.__doc__)
        assert "Brackets are not a sign." in doc and "brackets for a negative" not in doc
        assert ('A number in brackets is read by its size, unsigned: "(118,992)" passes for '
                'a figure of 118,992 or of -118,992.') in doc

    @pytest.mark.parametrize("written", [
        "-500,000 EUR", f"{MINUS}500,000 EUR", "-\u20ac500,000", f"{MINUS}\u20ac500,000",
        "\u20ac-500,000", f"\u20ac{MINUS}500,000", "EUR -500,000", "-EUR 500,000",
        "-500k EUR", "500,000 EUR"],
        ids=["minus", "minus sign", "before the euro", "minus sign before the euro",
             "after the euro", "minus sign after the euro", "after the code and a space",
             "before the code", "with a scale", "unsigned"])
    def test_a_sign_where_the_ruling_puts_it_agrees_with_a_negative_figure(self, written):
        assert check_text(EBIT.format(written), PROBE, currency="EUR") == []

    @pytest.mark.parametrize("written", [
        "-3,983,040 EUR", f"{MINUS}3,983,040 EUR", "-\u20ac3,983,040", "\u20ac-3,983,040",
        "EUR -3,983,040", "-EUR 3,983,040", "-3.98m EUR"],
        ids=["minus", "minus sign", "before the euro", "after the euro",
             "after the code and a space", "before the code", "with a scale"])
    def test_the_same_sign_on_a_positive_figure_is_flagged(self, written):
        flags = check_text(AUSTRIA.format(written), PROBE, currency="EUR")
        assert [(f.kind, f.text) for f in flags] == [("numeric", written)]

    def test_a_plus_is_a_sign_too(self):
        assert check_text(AUSTRIA.format("+3,983,040 EUR"), PROBE, currency="EUR") == []
        flags = check_text(EBIT.format("+500,000 EUR"), PROBE, currency="EUR")
        assert [(f.kind, f.text) for f in flags] == [("numeric", "+500,000 EUR")]

    @pytest.mark.parametrize("written", ["- 3,983,040 EUR", "EUR-3,983,040"],
                             ids=["a space after the minus", "a minus after the code"])
    def test_the_two_open_cases_hold_no_sign(self, written):
        # Decided for the ruling's open cases (BRIEF_3C_FIX, Trap 3): the sign must touch what
        # follows it, and must not follow a letter, so neither minus is a sign; the number is
        # read unsigned, by its size, and Austria's positive revenue is not flagged.
        assert check_text(AUSTRIA.format(written), PROBE, currency="EUR") == []
        assert check_text(EBIT.format(written.replace("3,983,040", "500,000")), PROBE,
                          currency="EUR") == []

    @pytest.mark.parametrize("text", [
        "In Q2-2026, the variance of revenue for Calder Foods Austria was 118,992 EUR.",
        "Year-on-year, the variance of revenue for Calder Foods Austria was 118,992 EUR.",
        "In FY2025-2026, the variance of revenue for Calder Foods Austria was 118,992 EUR."],
        ids=["Q2-2026", "year-on-year", "FY2025-2026"])
    def test_a_hyphen_after_a_letter_or_a_digit_is_not_a_sign(self, text):
        assert check_text(text, PROBE, currency="EUR") == []

    def test_a_range_is_two_unsigned_numbers(self):
        # "6-12": the hyphen follows a digit, so 12 is read as 12, never as -12.
        figures = [Figure("months held", D("6"), COUNT),
                   Figure("months asked for", D("12"), COUNT)]
        assert numeric_flags("Rows 6-12 were read.", figures, currency="EUR") == []
        assert _texts(numeric_flags("Rows 6-13 were read.", figures, currency="EUR")) == [
            "13"]

    def test_a_signed_number_is_compared_at_the_precision_written(self):
        assert check_text("Revenue was -4.3% against budget.", MONTH, currency="GBP") == []
        assert check_text("Revenue was 4.3% below budget.", MONTH, currency="GBP") == []
        assert _texts(check_text("Revenue was +4.3% against budget.", MONTH,
                                 currency="GBP")) == ["+4.3%"]
        assert check_text("Revenue was -\u00a3112k against budget.", MONTH,
                          currency="GBP") == []
        assert _texts(check_text("Revenue was -\u00a3113k against budget.", MONTH,
                                 currency="GBP")) == ["-\u00a3113k"]

    def test_rounding_is_the_same_on_both_sides_of_zero(self):
        small = [_variance("The margin's variance", "-0.04", PERCENT, "budget", True),
                 _variance("The rate's variance", "0.05", PERCENT, "budget", True)]
        for text in ("The margin's variance was -0.0%.", "The margin's variance was 0.0%.",
                     "The rate's variance was 0.1%.", "The rate's variance was +0.1%."):
            assert check_text(text, small, currency="EUR") == [], text
        assert _texts(check_text("The rate's variance was -0.1%.", small,
                                 currency="EUR")) == ["-0.1%"]

    def test_a_signed_year_is_a_figure(self):
        # A date has no sign: "-2026" is read as a number.
        assert _texts(numeric_flags("The count was -2026.", PROBE, currency="EUR")) == ["-2026"]
        assert numeric_flags("In 2026 the count was nil.", PROBE, currency="EUR") == []


class TestPpIsAPointsUnitWord:
    POINTS_FIGURE = [_variance("The margin's variance", "-1.5", POINTS, "budget", True)]
    MONEY_FIGURE = [_variance("The margin's variance", "-1.5", MONEY, "budget", True)]

    @pytest.mark.parametrize("written", ["-1.5 pp", "-1.5pp", "-1.5 pts", "-1.5 pt",
                                         "-1.5 points", "-1.5 percentage points"])
    def test_pp_and_the_other_points_words_read_a_points_figure(self, written):
        text = f"The margin's variance was {written}."
        assert check_text(text, self.POINTS_FIGURE, currency="EUR") == []

    def test_pp_against_a_money_figure_is_flagged(self):
        flags = check_text("The margin's variance was -1.5 pp.", self.MONEY_FIGURE,
                           currency="EUR")
        assert [(f.kind, f.text) for f in flags] == [("numeric", "-1.5 pp")]

    def test_pp_is_read_whole_and_never_as_pt(self):
        flags = check_text("The margin's variance was -1.6 pp.", self.POINTS_FIGURE,
                           currency="EUR")
        assert _texts(flags) == ["-1.6 pp"]
        # "ppm" is not the points word: the number before it is read with no unit.
        flags = check_text("The margin's variance was -1.6 ppm.", self.POINTS_FIGURE,
                           currency="EUR")
        assert _texts(flags) == ["-1.6"]


FB_SENTENCES = [
    ("Spain's revenue was 3,983,040 EUR, and Austria's was 9,865,590 EUR.",
     [("numeric", "3,983,040 EUR"), ("numeric", "9,865,590 EUR")]),
    ("Calder Foods Spain had revenue of 3,983,040 EUR.", [("numeric", "3,983,040 EUR")]),
    ("Revenue for Calder Foods Austria was 118,992 EUR below Budget.",
     [("numeric", "118,992 EUR"), ("direction", "below")]),
]


class TestFbStrictNames:
    @pytest.mark.parametrize("text, flagged", FB_SENTENCES,
                             ids=["swapped", "full name", "below"])
    def test_the_three_sentences_are_flagged_with_strict_names_and_not_by_default(
            self, text, flagged):
        assert check_text(text, PROBE, currency="EUR", labels=MEMBERS) == []
        flags = check_text(text, PROBE, currency="EUR", labels=MEMBERS, strict_names=True)
        assert [(f.kind, f.text) for f in flags] == flagged
        assert all(f.figure is None and f.reason == f"'{f.text}' has no figure name in its "
                   "clause." for f in flags)

    @pytest.mark.parametrize("text", [
        "Actual revenue for Calder Foods Austria was 3,983,040 EUR; the variance of revenue "
        "for Calder Foods Austria was 118,992 EUR (favourable).",
        "3,983,040 EUR was Actual revenue for Calder Foods Austria.",
        "In June 2026, Actual revenue for Calder Foods Austria was 3,983,040 EUR.",
        "On 15 June, Actual revenue for Calder Foods Austria was 3,983,040 EUR.",
        "For Calder Foods Austria, Actual revenue for Calder Foods Austria was 3,983,040 EUR."],
        ids=["each in its clause", "the name after the number", "a year", "a day",
             "a member name"])
    def test_a_name_in_the_clause_before_or_after_passes(self, text):
        assert check_text(text, PROBE, currency="EUR", labels=MEMBERS,
                          strict_names=True) == []

    def test_a_name_earlier_in_the_sentence_does_not_count(self):
        text = "Actual revenue for Calder Foods Austria was high, at 3,983,040 EUR."
        assert check_text(text, PROBE, currency="EUR", labels=MEMBERS) == []
        flags = check_text(text, PROBE, currency="EUR", labels=MEMBERS, strict_names=True)
        assert _texts(flags) == ["3,983,040 EUR"]
        text = ("The variance of revenue for Calder Foods Austria was 118,992 EUR, which was "
                "above Budget.")
        flags = check_text(text, PROBE, currency="EUR", labels=MEMBERS, strict_names=True)
        assert [(f.kind, f.text) for f in flags] == [("direction", "above")]

    def test_strict_names_asks_more_than_the_default(self):
        assert check_text(GOOD_PARAGRAPH, MONTH, currency="GBP") == []
        flags = check_text(GOOD_PARAGRAPH, MONTH, currency="GBP", strict_names=True)
        assert flags and all("has no figure name in its clause" in f.reason for f in flags)

    def test_a_wrong_figure_in_a_named_clause_is_still_its_own_flag(self):
        flags = check_text(AUSTRIA.format("3,983,041 EUR"), PROBE, currency="EUR",
                           labels=MEMBERS, strict_names=True)
        assert [(f.text, f.figure) for f in flags] == [
            ("3,983,041 EUR", "Actual revenue for Calder Foods Austria")]

    def test_the_option_is_keyword_only_and_off_by_default(self):
        import inspect
        for function in (check_text, numeric_flags, direction_flags):
            option = inspect.signature(function).parameters["strict_names"]
            assert (option.kind, option.default) == (inspect.Parameter.KEYWORD_ONLY, False)

    def test_the_docstring_states_strict_naming_and_the_vocabulary(self):
        import src.checks as checks
        doc = _flat(checks.__doc__)
        assert ("check_text(..., strict_names=True) also flags a number read as a figure, and "
                "a direction word, whose own clause holds no figure name") in doc
        assert "any other wording is not checked" in doc


class TestLabelsReachTheDirectionCheck:
    """The reviewer's rulings on 3c (provisional: option A). A label the caller gives is masked
    for the direction check as for the numeric one, so its words are never read as a direction
    word; a direction word outside every label is still read."""

    GROSS = [Figure("Gross profit for the group", D("866370"), MONEY)]

    def test_a_metric_label_is_not_read_as_a_direction_word(self):
        text = "Gross profit for the group: Budget, FY2026."
        assert check_text(text, [], currency="EUR", labels=["Gross profit"],
                          strict_names=True) == []
        flags = check_text(text, [], currency="EUR", strict_names=True)
        assert [(f.kind, f.text) for f in flags] == [("direction", "profit")]

    def test_a_direction_word_outside_every_label_is_still_read(self):
        text = "Gross profit for the group was a loss."
        for strict in (False, True):
            flags = check_text(text, self.GROSS, currency="EUR", labels=["Gross profit"],
                               strict_names=strict)
            assert [(f.kind, f.text, f.figure) for f in flags] == [
                ("direction", "loss", "Gross profit for the group")], strict

    def test_a_member_name_holding_a_direction_word_is_not_read_either(self):
        figures = [_variance("The variance of revenue for Upper Region", "-5000", MONEY,
                             "budget", True)]
        text = "The variance of revenue for Upper Region was -5,000 EUR (adverse)."
        assert direction_flags(text, figures, labels=["Upper Region"]) == []
        flipped = text.replace("adverse", "favourable")
        assert _texts(direction_flags(flipped, figures, labels=["Upper Region"])) == [
            "favourable"]

    def test_with_no_labels_the_direction_check_reads_as_before(self):
        for text in ("Revenue was \u00a3112k above budget.", "EBIT was a profit of \u00a38.6k."):
            assert direction_flags(text, MONTH) == direction_flags(text, MONTH, labels=())
            assert direction_flags(text, MONTH)

    def test_check_text_hands_its_labels_to_both_checks(self):
        import inspect
        assert "labels" in inspect.signature(direction_flags).parameters
        assert check_text("Gross profit for the group: Budget.", [], currency="EUR",
                          labels=["Gross profit"], strict_names=True) == []
        import src.checks as checks
        assert ("A label the caller gives, a member's name or a metric's label, is masked "
                "before either check reads the prose") in _flat(checks.__doc__)


# -- With no labels, the direction check reads as it did at eeed857 (the reviewer's ruling, A) ------

# The parts of this file that held at eeed857, the commit before the 3c fix: their sentences are
# the corpus, each read against each figure set the file held then.
EEED857_PARTS = ("GOOD_PARAGRAPH", "VOCABULARY", "TestNumericCheck", "TestDirectionCheck",
                 "TestFinding3DatesAndCapitalScales", "TestFinding2DirectionVocabulary",
                 "TestFlagsAreForTheReviewer")
# What eeed857's own direction_flags gives on the corpus below: the number of readings, the
# number of flags, and the SHA-256 of the canonical JSON of every reading's flags (kind, text,
# figure, reason). Computed outside the repository by eeed857's src/checks.py, imported under
# another name; the computation is kept with the 3c fix's evidence.
EEED857_SENTENCES = (450, 72, "01496b66b623675d37be33342d08c28a20422ac0327aadee1580bbe8e6110e78")


def direction_corpus() -> list:
    """(sentence, figure set name, figures) for every sentence the parts EEED857_PARTS hold
    (each string of more than one word that ends a sentence), with each figure set: MONTH,
    MARGINS, and MONTH with Gross profit, as the direction tests used them."""
    import ast
    from pathlib import Path
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    sentences: list = []
    for node in tree.body:
        name = (node.name if isinstance(node, ast.ClassDef)
                else node.targets[0].id if isinstance(node, ast.Assign)
                and isinstance(node.targets[0], ast.Name) else None)
        if name not in EEED857_PARTS:
            continue
        for found in ast.walk(node):
            if (isinstance(found, ast.Constant) and isinstance(found.value, str)
                    and " " in found.value and found.value.rstrip().endswith(".")
                    and found.value not in sentences):
                sentences.append(found.value)
    sets = (("MONTH", MONTH), ("MARGINS", MARGINS),
            ("MONTH and Gross profit", MONTH + [Figure("Gross profit", D("866370"), MONEY)]))
    return [(s, set_name, figures) for s in sentences for set_name, figures in sets]


def readings(flags_per_reading: list) -> tuple:
    """(readings, flags, SHA-256 of the canonical JSON of every reading's flags)."""
    import hashlib
    import json
    data = [[[f.kind, f.text, f.figure, f.reason] for f in flags] for flags in flags_per_reading]
    text = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return (len(data), sum(len(d) for d in data),
            hashlib.sha256(text.encode("ascii")).hexdigest())


class TestNoLabelsReadsAsEeed857:
    def test_the_corpus_is_every_sentence_of_the_parts_held_at_eeed857(self):
        corpus = direction_corpus()
        sentences = {s for s, _, _ in corpus}
        assert len(corpus) == 3 * len(sentences) == EEED857_SENTENCES[0] and len(sentences) == 150
        for v in VOCABULARY:
            assert set(v[1:2] + v[3:]) <= sentences, v[0]
        assert GOOD_PARAGRAPH in sentences

    def test_direction_flags_with_no_labels_gives_eeed857s_flags_on_the_corpus(self):
        found = readings([direction_flags(s, figures) for s, _, figures in direction_corpus()])
        assert found == EEED857_SENTENCES
        assert found[1] > 40

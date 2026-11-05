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
        # Thousands separators, k and m, per cent, points, brackets for negatives.
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

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

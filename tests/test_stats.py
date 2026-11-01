"""wilson_interval (src/stats.py): a rate is never shown without its 95% interval and n.

The four intervals below were verified by the reviewer (pilot verdict,
section 5), over 40 golden questions.
"""

import pytest

from src.stats import percent, wilson_interval

VERIFIED = [
    (40, 40, "91.2", "100.0"),
    (38, 40, "83.5", "98.6"),
    (36, 40, "76.9", "96.0"),
    (32, 40, "65.2", "89.5"),
]


class TestWilsonInterval:
    @pytest.mark.parametrize("k, n, low, high", VERIFIED, ids=[f"{k}-of-{n}" for k, n, *_ in VERIFIED])
    def test_the_verified_intervals(self, k, n, low, high):
        lower, upper = wilson_interval(k, n)
        assert (percent(lower), percent(upper)) == (low, high)

    def test_the_interval_holds_the_rate(self):
        for k in range(0, 41):
            lower, upper = wilson_interval(k, 40)
            assert 0 <= lower <= k / 40 <= upper <= 1

    def test_none_of_n_starts_at_zero(self):
        assert percent(wilson_interval(0, 40)[0]) == "0.0"

    @pytest.mark.parametrize("k, n", [(41, 40), (-1, 40), (0, 0), (1, -3)])
    def test_impossible_counts_are_refused(self, k, n):
        with pytest.raises(ValueError):
            wilson_interval(k, n)

    @pytest.mark.parametrize("k, n", [(38.0, 40), (38, 40.0), (True, 40)])
    def test_counts_are_whole_numbers(self, k, n):
        with pytest.raises(TypeError):
            wilson_interval(k, n)

    def test_percent_rounds_half_up_to_one_place(self):
        # 12.25 rounds to 12.3, not to the even 12.2.
        assert percent(0.1225) == "12.3" and percent(0.1224) == "12.2"
        assert percent(1.0) == "100.0" and percent(0) == "0.0"

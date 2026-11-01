# =============================================================================
# src/stats.py: the Wilson interval, so a rate never travels without it
# =============================================================================
"""
An evaluation rate over a small set (about 40 golden questions) is uncertain,
so it is shown with its 95% Wilson score interval and its n. The Wilson
interval stays inside 0 to 100% and behaves at 0 of n and n of n, where the
plain normal interval does not. Counts are whole numbers; a percentage is
rounded half up to one place, so 12.25 shows as 12.3.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from math import sqrt
from statistics import NormalDist

Z95 = NormalDist().inv_cdf(0.975)        # 1.959963984540054


def wilson_interval(k: int, n: int, z: float = Z95) -> tuple[float, float]:
    """The Wilson score interval for k successes in n, as fractions from 0 to 1."""
    for count in (k, n):
        if isinstance(count, bool) or not isinstance(count, int):
            raise TypeError("counts are whole numbers")
    if n <= 0 or not 0 <= k <= n:
        raise ValueError(f"{k} of {n} is not a possible count")
    p = k / n
    z2 = z * z
    centre = (p + z2 / (2 * n)) / (1 + z2 / n)
    half = z * sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / (1 + z2 / n)
    # At 0 of n and n of n the bound is exactly 0 or 1; floating point would
    # leave a trace either side.
    lower = 0.0 if k == 0 else max(0.0, centre - half)
    upper = 1.0 if k == n else min(1.0, centre + half)
    return lower, upper


def percent(value: float) -> str:
    """A fraction as a percentage with one decimal place, rounded half up."""
    return str((Decimal(repr(float(value))) * 100).quantize(Decimal("0.1"),
                                                           rounding=ROUND_HALF_UP))

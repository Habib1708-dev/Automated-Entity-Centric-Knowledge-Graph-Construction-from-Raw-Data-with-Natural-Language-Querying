"""A proportion with its 95 % Wilson score interval, so that every rate a sample gives comes with its n
and the range another sample of the same size could land in.

Role in the pipeline: scoring helpers of `validation/` use it for rates estimated from a sample (the
coverage estimate of R68, coverage.py); the reports carry it as data and MLflow gets its bounds.
Design: a small pydantic model built by `Proportion.of(k, n)`; pure arithmetic, no I/O.
Not here: what is counted (the scorers decide k and n).
"""

import math

from pydantic import BaseModel

# The normal quantile of a two-sided 95 % interval: the one level every interval in the thesis uses.
Z95 = 1.96


class Proportion(BaseModel):
    """k of n, the rate k / n and its 95 % Wilson score interval.

    Wilson rather than the textbook normal interval (rate ± 1.96·√(rate·(1 - rate) / n)): the normal one
    has zero width at a rate of 0 or 1 and leaves [0, 1] for small n, which is where estimates from a
    sample of 40 sentences sit. An empty sample (n = 0) has no rate: `rate`, `low` and `high` are None,
    and MLflow then logs nothing for them instead of a made-up 0 or 1.
    """

    k: int
    n: int
    rate: float | None
    low: float | None
    high: float | None

    @classmethod
    def of(cls, k: int, n: int) -> "Proportion":
        """Raises `ValueError` unless 0 <= k <= n: a count above its total is a bug in the caller."""
        if not 0 <= k <= n:
            raise ValueError(f"a proportion needs 0 <= k <= n, got {k} of {n}")
        if n == 0:
            return cls(k=0, n=0, rate=None, low=None, high=None)
        rate = k / n
        z2 = Z95 * Z95
        centre = (rate + z2 / (2 * n)) / (1 + z2 / n)
        half = Z95 * math.sqrt(rate * (1 - rate) / n + z2 / (4 * n * n)) / (1 + z2 / n)
        # the clamp only removes floating-point dust: the Wilson bounds lie in [0, 1] by construction
        return cls(k=k, n=n, rate=rate, low=max(0.0, centre - half), high=min(1.0, centre + half))

import logging
from typing import Dict, Tuple

from config import N0, POWER_SCALE_DIVISOR, R0

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def clamp(value: float, min_val: float, max_val: float) -> float:
    """Clamp value between min and max."""
    return max(min_val, min(max_val, value))


def shrink_toward_mean(
    rating: float, n_matches: int, n0: int = N0, baseline: float = R0
) -> float:
    """
    Small-sample regression: shrink toward the league mean.
    After n_matches, keep n/(n+n0) of the deviation from baseline.
    """
    return baseline + (rating - baseline) * n_matches / (n_matches + n0)


def normalize_to_power_score(
    rating: float,
    n_matches: int,
    scale_divisor: float = POWER_SCALE_DIVISOR,
    n0: int = N0,
    baseline: float = R0,
) -> float:
    """
    Convert Elo rating to 0-100 power score.
    1. Shrink toward mean (small-sample regression)
    2. Map to 0-100 with fixed scale (not min-max)
    """
    adjusted = shrink_toward_mean(rating, n_matches, n0, baseline)
    power = 50 + (adjusted - baseline) / scale_divisor
    return clamp(power, 0, 100)

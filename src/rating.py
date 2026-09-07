import logging
import math
from typing import Dict, List, Tuple
from collections import defaultdict
from datetime import datetime

from config import HFA, K_BASE, MARGIN_MULTIPLIER_FLOOR, R0, SCALE

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class EloRating:
    def __init__(
        self,
        k_base: float = K_BASE,
        hfa: float = HFA,
        scale: float = SCALE,
        margin_floor: float = MARGIN_MULTIPLIER_FLOOR,
        use_margin: bool = True,
    ):
        self.k_base = k_base
        self.hfa = hfa
        self.scale = scale
        self.margin_floor = margin_floor
        # False gives plain Elo, the backtest baseline that tells us whether the
        # goal-difference term earns its complexity.
        self.use_margin = use_margin
        self.ratings = {}
        self.rating_history = defaultdict(list)

    def initialize_teams(self, teams: List[str], starting_rating: float = R0) -> None:
        """Initialize all teams with starting rating."""
        for team in teams:
            self.ratings[team] = starting_rating
            self.rating_history[team].append((0, starting_rating))

    def update_from_match(
        self,
        home_team: str,
        away_team: str,
        home_goals: int,
        away_goals: int,
        matchday: int,
    ) -> None:
        """
        Update ratings based on a single match result.
        Formula: standard Elo with margin multiplier and autocorrelation dampening.
        """
        if home_team not in self.ratings or away_team not in self.ratings:
            logger.warning(
                f"Skipping match: unknown team(s) {home_team} vs {away_team}"
            )
            return

        r_home = self.ratings[home_team]
        r_away = self.ratings[away_team]

        # Expected score
        r_diff = (r_home + self.hfa) - r_away
        e_home = 1.0 / (1.0 + 10 ** (-r_diff / self.scale))
        e_away = 1.0 - e_home

        # Actual score
        goal_diff = home_goals - away_goals
        if goal_diff > 0:
            s_home, s_away = 1.0, 0.0
        elif goal_diff < 0:
            s_home, s_away = 0.0, 1.0
        else:
            s_home, s_away = 0.5, 0.5

        # Margin multiplier
        if self.use_margin:
            margin_mult = max(math.log(abs(goal_diff) + 1), self.margin_floor)
        else:
            margin_mult = 1.0

        # Autocorrelation dampening (stop favorites farming weak sides)
        d_winner = r_diff if goal_diff > 0 else (-r_diff if goal_diff < 0 else 0)
        autocorr = 2.2 / (d_winner * 0.001 + 2.2)

        k = self.k_base * margin_mult * autocorr

        # Update
        self.ratings[home_team] += k * (s_home - e_home)
        self.ratings[away_team] += k * (s_away - e_away)

        # Record history
        self.rating_history[home_team].append((matchday, self.ratings[home_team]))
        self.rating_history[away_team].append((matchday, self.ratings[away_team]))

    def get_ratings(self) -> Dict[str, float]:
        """Return current ratings."""
        return self.ratings.copy()

    def get_rating_history(self) -> Dict[str, List[Tuple[int, float]]]:
        """Return rating history per team."""
        return dict(self.rating_history)

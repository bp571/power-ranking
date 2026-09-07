"""Rating math: the properties the Elo engine must never violate.

The backtest tunes K_BASE and HFA *through* this engine, so a sign error here
would be baked into config.py and never noticed again.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from config import N0, R0  # noqa: E402
from rating import EloRating  # noqa: E402
from score import normalize_to_power_score, shrink_toward_mean  # noqa: E402

TEAMS = ["A", "B", "C", "D"]


def engine(**kwargs):
    elo = EloRating(**kwargs)
    elo.initialize_teams(TEAMS)
    return elo


def test_every_team_starts_at_r0():
    assert set(engine().get_ratings().values()) == {R0}


def test_update_is_zero_sum():
    """The two updates are equal and opposite, so the league mean never moves."""
    elo = engine()
    for home, away, hg, ag in [("A", "B", 3, 0), ("C", "D", 1, 1), ("B", "C", 0, 2)]:
        elo.update_from_match(home, away, hg, ag, 1)
        assert sum(elo.get_ratings().values()) == pytest.approx(R0 * len(TEAMS))


def test_winner_gains_and_loser_loses():
    elo = engine()
    elo.update_from_match("A", "B", 2, 0, 1)
    ratings = elo.get_ratings()
    assert ratings["A"] > R0 > ratings["B"]


def test_draw_between_equals_is_neutral_apart_from_home_advantage():
    """With HFA the home side is favoured, so a draw must cost it rating."""
    elo = engine()
    elo.update_from_match("A", "B", 1, 1, 1)
    ratings = elo.get_ratings()
    assert ratings["A"] < R0 < ratings["B"]

    no_hfa = engine(hfa=0)
    no_hfa.update_from_match("A", "B", 1, 1, 1)
    assert no_hfa.get_ratings()["A"] == pytest.approx(R0)


def test_home_advantage_makes_an_away_win_worth_more():
    home_win = engine()
    home_win.update_from_match("A", "B", 2, 0, 1)

    away_win = engine()
    away_win.update_from_match("B", "A", 0, 2, 1)

    assert away_win.get_ratings()["A"] > home_win.get_ratings()["A"]


def gain_for_margin(goals: int) -> float:
    elo = engine()
    elo.update_from_match("A", "B", goals, 0, 1)
    return elo.get_ratings()["A"] - R0


def test_bigger_margin_moves_the_rating_further():
    gains = [gain_for_margin(g) for g in range(1, 8)]
    assert gains == sorted(gains)
    assert gains[0] < gains[-1]


def test_each_extra_goal_adds_less_than_the_one_before():
    """Log damping. Measured from 2:0 up, where the multiplier floor no longer
    binds - at 1:0 the floor lifts ln(2)=0.693 to 0.7 and distorts the step."""
    gains = [gain_for_margin(g) for g in range(2, 9)]
    steps = [b - a for a, b in zip(gains, gains[1:])]
    assert steps == sorted(steps, reverse=True)
    assert all(step > 0 for step in steps)


def test_the_margin_floor_binds_at_a_one_goal_win():
    """Without the floor a 1:0 would score ln(2); the floor keeps narrow wins
    and draws informative."""
    floored = gain_for_margin(1)
    unfloored = EloRating(margin_floor=0.0)
    unfloored.initialize_teams(TEAMS)
    unfloored.update_from_match("A", "B", 1, 0, 1)
    assert floored > unfloored.get_ratings()["A"] - R0


def test_beating_a_stronger_opponent_pays_more():
    strong = engine()
    strong.ratings["B"] = 1900
    strong.update_from_match("A", "B", 1, 0, 1)

    weak = engine()
    weak.ratings["B"] = 1100
    weak.update_from_match("A", "B", 1, 0, 1)

    assert strong.get_ratings()["A"] - R0 > weak.get_ratings()["A"] - R0


def test_a_team_that_wins_everything_ends_on_top():
    elo = engine()
    for _ in range(3):
        for opponent in ("B", "C", "D"):
            elo.update_from_match("A", opponent, 2, 0, 1)

    ratings = elo.get_ratings()
    assert ratings["A"] == max(ratings.values())
    assert all(ratings["A"] > ratings[t] for t in ("B", "C", "D"))


def test_replaying_the_same_matches_is_deterministic():
    matches = [("A", "B", 2, 1), ("C", "D", 0, 0), ("B", "D", 3, 2), ("A", "C", 1, 4)]
    runs = []
    for _ in range(2):
        elo = engine()
        for home, away, hg, ag in matches:
            elo.update_from_match(home, away, hg, ag, 1)
        runs.append(elo.get_ratings())

    assert runs[0] == runs[1]


def test_unknown_team_is_skipped_without_changing_ratings():
    elo = engine()
    elo.update_from_match("A", "Unknown FC", 5, 0, 1)
    assert set(elo.get_ratings().values()) == {R0}


def test_shrinkage_pulls_toward_the_baseline_and_relaxes_with_sample_size():
    """n0 passed explicitly: this tests the formula, not the configured value."""
    assert shrink_toward_mean(1700, 0, n0=5) == pytest.approx(R0)
    assert shrink_toward_mean(1700, 5, n0=5) == pytest.approx(1600)  # half the deviation
    assert shrink_toward_mean(1700, 20, n0=5) == pytest.approx(1660)  # 80 percent
    assert shrink_toward_mean(1300, 5, n0=5) == pytest.approx(1400)


def test_shrinkage_uses_the_configured_n0_by_default():
    assert shrink_toward_mean(1700, N0) == pytest.approx(1600)  # n == n0 -> half
    assert shrink_toward_mean(1700, 1) < shrink_toward_mean(1700, 26)


def test_power_score_is_50_at_the_baseline_and_clamped():
    assert normalize_to_power_score(R0, 26) == pytest.approx(50)
    assert 0 <= normalize_to_power_score(5000, 26) <= 100
    assert 0 <= normalize_to_power_score(-5000, 26) <= 100

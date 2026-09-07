"""Walk-forward backtest: does the model beat the baselines, and at which parameters?

Every match is predicted using only matches strictly before it. The rating gap is
turned into home/draw/away probabilities by an ordered logistic with two
thresholds, fitted on the season. Scored with RPS, the right metric for ordered
three-outcome football predictions.

The same calibration is fitted for every method, including the baselines, so the
comparison is fair. Because thresholds and parameters are fitted on the very
season being scored, the absolute numbers are optimistic; the *ranking* of the
methods is the part that carries information.
"""

import logging
import math
import statistics
from typing import Dict, List, Sequence, Tuple

from config import SCALE
from rating import EloRating

logger = logging.getLogger(__name__)

AWAY, DRAW, HOME = 0, 1, 2

# Elo's own logistic scale, ln(10)/400, used as the starting point for beta.
ELO_BETA = math.log(10) / 400


def outcome_of(home_goals: int, away_goals: int) -> int:
    if home_goals > away_goals:
        return HOME
    return DRAW if home_goals == away_goals else AWAY


def _sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


def probabilities(x: float, params: Sequence[float]) -> Tuple[float, float, float]:
    """Ordered logistic: P(away), P(draw), P(home) for predictor x."""
    theta0, theta1, beta = params
    p_away = _sigmoid(theta0 - beta * x)
    p_upto_draw = _sigmoid(theta1 - beta * x)
    return p_away, max(p_upto_draw - p_away, 1e-12), max(1.0 - p_upto_draw, 1e-12)


def rps(probs: Sequence[float], outcome: int) -> float:
    """Ranked probability score over the ordered outcomes away < draw < home."""
    total = 0.0
    cum_p = cum_o = 0.0
    for i in range(2):  # K-1 cumulative comparisons
        cum_p += probs[i]
        cum_o += 1.0 if outcome == i else 0.0
        total += (cum_p - cum_o) ** 2
    return total / 2.0


def log_loss(probs: Sequence[float], outcome: int) -> float:
    return -math.log(max(probs[outcome], 1e-12))


def _negative_log_likelihood(samples, params) -> float:
    return sum(log_loss(probabilities(x, params), o) for x, o in samples)


def fit_ordered_logistic(samples: List[Tuple[float, int]]) -> Tuple[float, float, float]:
    """Maximum likelihood by coordinate descent with a shrinking step.

    Three parameters on a smooth likelihood - no optimiser dependency needed.
    """
    params = [-0.8, 0.8, ELO_BETA]
    steps = [0.5, 0.5, ELO_BETA]

    for _ in range(60):
        improved = False
        for i in range(3):
            best = _negative_log_likelihood(samples, params)
            for direction in (1, -1):
                trial = list(params)
                trial[i] += direction * steps[i]
                if trial[0] > trial[1]:  # thresholds must stay ordered
                    continue
                value = _negative_log_likelihood(samples, trial)
                if value < best:
                    best, params, improved = value, trial, True
        if not improved:
            steps = [s / 2 for s in steps]
            if max(abs(s) for s in steps) < 1e-8:
                break
    return tuple(params)


def evaluate(samples: List[Tuple[float, int]], skip: int = 0) -> Dict[str, float]:
    """Fit the calibration on all samples, score the ones after `skip`."""
    params = fit_ordered_logistic(samples)
    scored = samples[skip:]
    predictions = [probabilities(x, params) for x, _ in scored]
    return {
        "rps": sum(rps(p, o) for p, (_, o) in zip(predictions, scored)) / len(scored),
        "log_loss": sum(log_loss(p, o) for p, (_, o) in zip(predictions, scored)) / len(scored),
        "n": len(scored),
    }


def elo_samples(matches: List[Dict], **elo_kwargs) -> List[Tuple[float, int]]:
    """Walk forward: predictor is the rating gap known *before* each match."""
    teams = sorted({m[side] for m in matches for side in ("home_team", "away_team")})
    elo = EloRating(**elo_kwargs)
    elo.initialize_teams(teams)

    samples = []
    for m in matches:
        gap = (elo.ratings[m["home_team"]] + elo.hfa) - elo.ratings[m["away_team"]]
        home_goals, away_goals = int(m["home_goals"]), int(m["away_goals"])
        samples.append((gap, outcome_of(home_goals, away_goals)))
        elo.update_from_match(
            m["home_team"], m["away_team"], home_goals, away_goals, int(m["matchday"])
        )
    return samples


def table_position_samples(matches: List[Dict]) -> List[Tuple[float, int]]:
    """Baseline the readers already have: the current league table.

    Predictor is the home team's position advantage, from the table as it stood
    before the match. Ties (matchday 1) give a zero predictor.
    """
    stats = {
        t: {"pts": 0, "gd": 0, "gf": 0}
        for m in matches
        for t in (m["home_team"], m["away_team"])
    }

    def positions():
        order = sorted(stats, key=lambda t: (-stats[t]["pts"], -stats[t]["gd"], -stats[t]["gf"], t))
        return {team: i for i, team in enumerate(order, 1)}

    samples = []
    for m in matches:
        pos = positions()
        # A smaller position number is better, so away - home is the home edge.
        samples.append((float(pos[m["away_team"]] - pos[m["home_team"]]), outcome_of(
            int(m["home_goals"]), int(m["away_goals"]))))

        hg, ag = int(m["home_goals"]), int(m["away_goals"])
        h, a = stats[m["home_team"]], stats[m["away_team"]]
        h["gf"] += hg; h["gd"] += hg - ag
        a["gf"] += ag; a["gd"] += ag - hg
        if hg > ag:
            h["pts"] += 3
        elif hg < ag:
            a["pts"] += 3
        else:
            h["pts"] += 1; a["pts"] += 1
    return samples


def base_rate_samples(matches: List[Dict]) -> List[Tuple[float, int]]:
    """Weakest baseline: a constant prediction at the league's own H/D/A rates."""
    return [(0.0, outcome_of(int(m["home_goals"]), int(m["away_goals"]))) for m in matches]


def estimate_hfa(matches: List[Dict]) -> float:
    """Home advantage in Elo points, derived from the observed home score rate.

    RPS cannot identify HFA: it enters the predictor as a constant offset, which
    the calibration thresholds absorb completely, so the grid search drifts
    without ever finding an optimum. Solve it directly instead - find the rating
    gap whose Elo expectation equals the home side's actual score rate.
    """
    score = sum(
        {HOME: 1.0, DRAW: 0.5, AWAY: 0.0}[outcome_of(int(m["home_goals"]), int(m["away_goals"]))]
        for m in matches
    ) / len(matches)
    return -SCALE * math.log10(1.0 / score - 1.0)


def sort_matches(matches: List[Dict]) -> List[Dict]:
    return sorted(
        (m for m in matches if m["status"] == "played"),
        key=lambda m: (m["date"], int(m["matchday"]), m["match_id"]),
    )


K_GRID = range(10, 36, 5)
HFA_GRID = range(30, 101, 10)
FLOOR_GRID = (0.5, 0.6, 0.7, 0.8)


def grid_search(matches: List[Dict], skip: int = 0):
    """Coarse grid, as specified: a fine grid on 182 matches fits noise."""
    results = []
    for k_base in K_GRID:
        for hfa in HFA_GRID:
            for floor in FLOOR_GRID:
                samples = elo_samples(matches, k_base=k_base, hfa=hfa, margin_floor=floor)
                scores = evaluate(samples, skip)
                results.append({"k_base": k_base, "hfa": hfa, "floor": floor, **scores})
    results.sort(key=lambda r: r["rps"])
    return results


def main():
    import csv
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent))
    from config import MATCHES_CSV, SEASON_PREVIOUS

    logging.basicConfig(level=logging.WARNING)
    rows = [r for r in csv.DictReader(open(MATCHES_CSV, encoding="utf-8"))
            if r["season"] == SEASON_PREVIOUS]
    matches = sort_matches(rows)
    n_teams = len({m["home_team"] for m in matches} | {m["away_team"] for m in matches})
    # The first five matchdays are the warm-up every method shares; report the
    # rest separately, because that is when anyone actually reads the ranking.
    skip = 5 * (n_teams // 2)

    print(f"Backtest {SEASON_PREVIOUS}: {len(matches)} matches, {n_teams} teams")
    print(f"Scoring from matchday 6 on ({len(matches) - skip} matches)\n")

    hfa = estimate_hfa(matches)
    print(f"HFA from the observed home score rate: {hfa:.1f} Elo points\n")

    results = grid_search(matches, skip)
    best = results[0]

    print("Grid minimum (RPS):")
    print(f"  K_BASE={best['k_base']}  HFA={best['hfa']}  floor={best['floor']}\n")

    print("Top 5 of the grid:")
    for r in results[:5]:
        print(f"  K={r['k_base']:>2} HFA={r['hfa']:>3} floor={r['floor']:.1f}"
              f"   RPS={r['rps']:.4f}  logloss={r['log_loss']:.4f}")

    tuned = elo_samples(matches, k_base=best["k_base"], hfa=best["hfa"],
                        margin_floor=best["floor"])
    plain = elo_samples(matches, k_base=best["k_base"], hfa=best["hfa"], use_margin=False)

    comparison = [
        ("Model (Elo + margin, tuned)", evaluate(tuned, skip)),
        ("Baseline 3: plain Elo, no margin", evaluate(plain, skip)),
        ("Baseline 2: table position", evaluate(table_position_samples(matches), skip)),
        ("Baseline 1: league H/D/A base rate", evaluate(base_rate_samples(matches), skip)),
    ]

    print(f"\n{'Method':<38}{'RPS':>9}{'LogLoss':>10}")
    for label, scores in comparison:
        print(f"{label:<38}{scores['rps']:>9.4f}{scores['log_loss']:>10.4f}")

    # A raw RPS comparison is not enough: the differences here are far smaller
    # than the spread between matches, so report the paired standard error too.
    model_rps = comparison[0][1]["rps"]
    table_rps = comparison[2][1]["rps"]
    mean, se = paired_advantage(tuned, table_position_samples(matches), skip)

    print(f"\nModel vs table baseline: RPS {model_rps:.4f} vs {table_rps:.4f}")
    print(f"  paired advantage {mean:+.5f} +/- {se:.5f} (se), t = {mean / se:+.2f}")
    if abs(mean) < 2 * se:
        print("  VERDICT: indistinguishable from the table baseline. The extra")
        print("  machinery is not justified by predictive accuracy - say so on the page.")
    else:
        print(f"  VERDICT: {'model' if mean > 0 else 'baseline'} is better, and the gap "
              f"survives its own standard error.")


def paired_advantage(model_samples, baseline_samples, skip: int) -> Tuple[float, float]:
    """Per-match RPS difference (positive = model better) with its standard error.

    Paired on the same matches, so the match-to-match variation cancels and the
    remaining spread is what actually separates the two methods.
    """
    model_params = fit_ordered_logistic(model_samples)
    base_params = fit_ordered_logistic(baseline_samples)
    diffs = [
        rps(probabilities(bx, base_params), o) - rps(probabilities(mx, model_params), o)
        for (mx, o), (bx, _) in zip(model_samples[skip:], baseline_samples[skip:])
    ]
    return statistics.mean(diffs), statistics.stdev(diffs) / len(diffs) ** 0.5


if __name__ == "__main__":
    main()

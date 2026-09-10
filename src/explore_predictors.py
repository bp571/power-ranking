"""Do form, venue or rest days predict the next result better than the ratings?

One protocol for every candidate, the same backtest.py uses: turn the predictor
into home/draw/away probabilities with an ordered logistic - three parameters
for every candidate, so none is flattered by having more - fit it on the season
and score from matchday 6 on with RPS. Each candidate is then compared against
the league's own home/draw/away rates, paired per match and with the standard
error, because that is the only thing that says whether a gap is real.

Nothing clears two standard errors. That result is the point, and it is why the
page carries this table instead of a claim: a reader can see for themselves that
the fancier lens is not the better forecast.

Stricter variant, measured while this was written: refitting the calibration on
each matchday from the matches before it (rather than once on the season) moves
every candidate into the 0.223-0.230 band and leaves the ordering just as
undecidable. Nothing here survives being asked twice.

    python src/explore_predictors.py
"""

from collections import defaultdict
from datetime import date

from backtest import (
    base_rate_samples,
    elo_samples,
    evaluate,
    outcome_of,
    paired_advantage,
    sort_matches,
    table_position_samples,
)
from config import FORM_WINDOW, HFA, SCALE, SEASON_PREVIOUS
from predict import goals, load, walk_forward
from rating import EloRating

# The warm-up every candidate shares: before this nobody knows anything.
SKIP_MATCHDAYS = 5

# Pseudo-matches shrinking a team's measured home lift towards "no lift at all".
VENUE_SHRINK = 6


def _base(rows):
    return [x for x, _ in base_rate_samples(rows)]


def _table_position(rows):
    return [x for x, _ in table_position_samples(rows)]


def _season_elo(rows):
    return [x for x, _ in elo_samples(rows)]


def _points_per_game(rows):
    """The simplest thing a reader could do with the table themselves."""
    points, played = defaultdict(int), defaultdict(int)
    out = []
    for r in rows:
        h, a = r["home_team"], r["away_team"]
        out.append((points[h] / played[h] if played[h] else 0.0)
                   - (points[a] / played[a] if played[a] else 0.0))
        hg, ag = goals(r)
        played[h] += 1
        played[a] += 1
        points[h] += 3 if hg > ag else (1 if hg == ag else 0)
        points[a] += 3 if ag > hg else (1 if hg == ag else 0)
    return out


def _form(rows):
    """The page's own headline number: Elo over the last FORM_WINDOW matchdays,
    restarted at 1500, as it stood before the match's own matchday."""
    teams = sorted({r[s] for r in rows for s in ("home_team", "away_team")})
    gaps = {}
    for matchday in sorted({int(r["matchday"]) for r in rows}):
        elo = EloRating()
        elo.initialize_teams(teams)
        for r in rows:
            if matchday - FORM_WINDOW <= int(r["matchday"]) < matchday:
                elo.update_from_match(r["home_team"], r["away_team"], *goals(r),
                                      int(r["matchday"]))
        for r in rows:
            if int(r["matchday"]) == matchday:
                gaps[r["match_id"]] = (elo.ratings[r["home_team"]] + HFA
                                       - elo.ratings[r["away_team"]])
    return [gaps[r["match_id"]] for r in rows]


def _expected_goals(rows):
    """The forecast the page publishes - see predict.py."""
    diffs = {r["match_id"]: d for r, d in walk_forward(rows)}
    return [diffs.get(r["match_id"], 0.0) for r in rows]


def _home_strength(rows):
    """A home advantage of this team's own, on top of the league-wide one.

    How much a side has over-performed its rating at home so far, minus how much
    the visitor has under-performed away, both shrunk towards zero. If some clubs
    really are harder to beat on their own pitch, this is where it shows up.
    """
    teams = sorted({r[s] for r in rows for s in ("home_team", "away_team")})
    elo = EloRating()
    elo.initialize_teams(teams)
    lift, seen = defaultdict(float), defaultdict(int)

    out = []
    for r in rows:
        h, a = r["home_team"], r["away_team"]
        gap = elo.ratings[h] + HFA - elo.ratings[a]
        out.append(SCALE * (lift[h, "home"] / (seen[h, "home"] + VENUE_SHRINK)
                            - lift[a, "away"] / (seen[a, "away"] + VENUE_SHRINK)))

        hg, ag = goals(r)
        expected = 1.0 / (1.0 + 10 ** (-gap / SCALE))
        actual = 1.0 if hg > ag else (0.5 if hg == ag else 0.0)
        lift[h, "home"] += actual - expected
        lift[a, "away"] += expected - actual
        seen[h, "home"] += 1
        seen[a, "away"] += 1
        elo.update_from_match(h, a, hg, ag, int(r["matchday"]))
    return out


def _rest_days(rows):
    """Days since each side last played, home minus away. Amateur fixture lists
    are irregular enough - midweek games, cup, postponements - that this varies."""
    last = {}
    out = []
    for r in rows:
        played_on = date.fromisoformat(r["date"])
        rest = []
        for team in (r["home_team"], r["away_team"]):
            rest.append((played_on - last[team]).days if team in last else 7)
            last[team] = played_on
        out.append(max(-14, min(14, rest[0] - rest[1])))
    return out


CANDIDATES = [
    ("Liga-Quote (kein Modell)", _base),
    ("Tabellenplatz", _table_position),
    ("Punkte pro Spiel", _points_per_game),
    ("Saison-Rating (Elo)", _season_elo),
    ("Form der letzten 5 Spieltage", _form),
    ("Erwartete Tore (Prognose)", _expected_goals),
    ("Heimstärke des Vereins", _home_strength),
    ("Ruhetage vor dem Spiel", _rest_days),
]


def compare(season=SEASON_PREVIOUS):
    """RPS per candidate plus its paired lead over the league base rate."""
    rows = sort_matches(load(season))
    outcomes = [outcome_of(*goals(r)) for r in rows]
    skip = sum(1 for r in rows if int(r["matchday"]) <= SKIP_MATCHDAYS)
    baseline = list(zip(_base(rows), outcomes))

    results = []
    for name, predictor in CANDIDATES:
        samples = list(zip(predictor(rows), outcomes))
        scores = evaluate(samples, skip)
        lead, se = paired_advantage(samples, baseline, skip)
        results.append({"name": name, "rps": scores["rps"], "n": scores["n"],
                        "lead": lead, "se": se, "baseline": predictor is _base})
    return sorted(results, key=lambda r: r["rps"])


def main():
    results = compare()
    print(f"Prädiktoren, gemessen an {SEASON_PREVIOUS} ab Spieltag {SKIP_MATCHDAYS + 1} "
          f"(n={results[0]['n']})\n")
    print(f"{'Ansatz':<32}{'RPS':>8}{'Vorsprung auf die Liga-Quote':>32}")
    for r in results:
        if r["baseline"]:
            lead = "Referenz"
        else:
            significant = abs(r["lead"]) > 2 * r["se"]
            lead = (f"{r['lead']:+.5f} +/- {r['se']:.5f}"
                    f"{'  *' if significant else ''}")
        print(f"{r['name']:<32}{r['rps']:>8.4f}{lead:>32}")
    print("\nKleiner ist besser. * = mehr als zwei Standardfehler vom Zufall entfernt.")


if __name__ == "__main__":
    main()

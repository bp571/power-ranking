# Power Ranking

Power ranking for the Kreisliga A Hunsrück-Mosel from fussball.de result data — 14 teams,
26 matchdays, 182 matches per season. Live at
[bp571.github.io/power-ranking](https://bp571.github.io/power-ranking/).

It answers the one question the official table cannot: how strong is a team really, once you account
for who they actually played? The table rewards points alone, so a team that beat the bottom four
looks identical to one that beat the top four. This project rates teams from results instead, with
strength of schedule, goal margin and home advantage in the number.

A one-person side project in Python. Simplicity beats sophistication: the ranking has to be
explainable to a teammate in one sentence and to keep working with a few minutes of attention after
each matchday. Everything below is implemented; [Not implemented](#not-implemented) is the roadmap.

*Notes for Claude: the user writes in German, this file is in English.*

## Commands

```
python run.py                     # current season (2026/27): scrape -> rate -> write the page
python run.py --season 2025/26    # a past season
python run.py --cached            # parse saved pages in data/raw, no network
python src/report.py              # rebuild the page from data/matches.csv alone, no network
python src/backtest.py            # re-run the walk-forward evaluation
```

After a matchday: `python run.py`, then commit and push — GitHub Pages rebuilds in about a minute.

## Pipeline

```
run.py
  ├─ scrape.collect  one request per matchday, 4 s apart -> data/raw/   (network only)
  ├─ parse           pages -> match rows, then validate()               (raises on any gap)
  ├─ overrides       data/manual_overrides.csv wins over scraped rows
  ├─ save            upsert into data/matches.csv by match_id
  ├─ rating          replay chronologically -> Elo per team
  ├─ score           shrink + normalize -> power score 0-100
  └─ report          docs/index.html
```

- **A season is identified by its Staffel id, not by the URL slug** — to add one, take its id from
  the season dropdown on any Staffel page and put it in `STAFFEL_IDS`.
- fussball.de sheds load during a season walk (sporadic 503s). A second pass retries the refused
  matchdays; one that stays unavailable has to be filled in via `data/manual_overrides.csv`.
- Goal counts and dates are obfuscated with per-request webfonts;
  [src/font_decoder.py](src/font_decoder.py) maps glyphs back to characters before parsing.
- [src/config.py](src/config.py) holds everything tunable in one place: Staffel ids, K/HFA/N0, team
  aliases, `KNOWN_TEAMS`. `rating.py` and `score.py` take these as defaults, so the backtest can
  override them per call.

**`data/matches.csv` is the contract**; the scraper is a replaceable adapter behind it. Columns:
`match_id, season, matchday, date, home_team, away_team, home_goals, away_goals, status`
(`played` | `scheduled`; only `played` rows feed the model; team names canonicalized through
`TEAM_ALIASES`). Validation runs on every scrape and raises rather than writing partial data:
exactly `n_teams * (n_teams - 1)` matches, dates parseable, scores 0–20, no team playing itself,
and for the current season the team set equal to `KNOWN_TEAMS`.

Only what fussball.de publishes for amateur leagues exists: date, both teams, goal counts, matchday.
No xG, no shots, no lineups — every metric has to be derivable from those fields alone.

## Model

Elo with a logarithmic goal-difference multiplier and a home-field term
([src/rating.py](src/rating.py), [src/score.py](src/score.py)), replayed chronologically:

```
E_home = 1 / (1 + 10^(-((R_home + HFA) - R_away) / 400))    HFA = 100
S      = 1 / 0.5 / 0                       win / draw / loss
margin = max(ln(|goal_diff| + 1), 0.8)     blowout damping
damp   = 2.2 / (0.001 * d_winner + 2.2)    d_winner = rating edge of the winning side
K      = 20 * margin * damp
R     += K * (S - E)

R_adj  = 1500 + (R - 1500) * n / (n + 20)  n = matches played, display time only
POWER  = clamp(50 + (R_adj - 1500) / 3, 0, 100)
```

Strength of schedule and recency are implicit in Elo — no separate terms. Unequal games played is a
non-issue because Elo is per-match. The 0–100 mapping uses a **fixed** divisor, never min-max over
the current league: min-max would force the worst team to 0 and the best to 100 every week and
destroy any sense of progress.

Why Elo: with 14 teams playing a double round robin the schedule is balanced, so any reasonable
method agrees on the broad ordering — transparency decided it. It updates match by match, which
yields the rating history the chart needs, and has three parameters. *Rejected:* Massey/Colley
least-squares (exact strength-of-schedule, but one end-of-season snapshot rather than a trajectory,
and Colley discards goal difference); per-team fitted models such as Poisson or Bradley-Terry
(182 matches over 14 teams is thin for parameter estimation, and accuracy will not survive roster
churn).

## Evidence — read before touching parameters

`python src/backtest.py` replays 2025/26 walk-forward, turns the rating gap into H/D/A probabilities
with an ordered logistic, and scores with RPS. The same calibration is fitted for every method, so
the comparison is fair. From matchday 6 on, n=147: **model 0.2170**, plain Elo without margin
0.2178, table position 0.2178, league base rate 0.2209.

**The model is not meaningfully better than the league table.** Paired per-match advantage
`+0.00078 ± 0.00171` (se), t = 0.46; the model wins 77 of 147, a coin flip, early and late alike.
That is what a balanced schedule predicts — everyone faces everyone twice, so little
strength-of-schedule error is left to correct. The value is as a different lens, margin- and
opponent-aware, not as a forecaster. **The page must never claim otherwise** — overclaiming is the
one failure mode that would make the project worse than not existing.

Three hypotheses about amateur football, measured on 2025/26 rather than assumed:

- **Results scatter more than in higher leagues — confirmed strongly.** 28.6% of matches end with a
  3+ goal margin, only 15.9% are draws. Simulating 14 *identical* teams over a season yields an Elo
  SD of 35.6 against the 51.6 observed: **48% of the apparent spread is luck.** This set `N0 = 20`,
  and it is why the ranking looks flat early — after five matchdays the whole league sits inside
  roughly 47-54 power points. Correct, not broken.
- **Home advantage is more pronounced — confirmed.** Home score rate 0.6401 → HFA = 100, against
  roughly 60-70 in professional football.
- **Momentum is more extreme — not detectable.** Lag-1 autocorrelation of rating residuals
  r = +0.004 (se 0.054); form over the last three matches predicting the next r = −0.023 (se 0.056).
  182 matches can only rule out |r| > 0.11, so a weak effect may hide, but nothing here justifies a
  recency term. **Do not add recency weighting to the base rating.** A "Form" column describing what
  recently happened is fine; selling it as predictive is not.

Two methodological notes:

- **RPS cannot identify `HFA`.** It enters as a constant offset that the calibration thresholds
  absorb completely, so the grid drifts monotonically without an optimum. `backtest.estimate_hfa()`
  solves it directly: the rating gap whose Elo expectation equals the observed home score rate.
- **`K_BASE` and the margin floor sit in a flat region** — RPS varies in the 4th decimal across
  K = 10–25, far below the paired se of 0.0017. They are set to sensible values there, not to the
  grid's argmin. **Re-tuning them on a new season would be fitting noise.**

## The page

[src/report.py](src/report.py) writes one self-contained `docs/index.html` — no external assets, no
build step, no image files. Rank-sorted table (rank, team, power, change vs. previous matchday,
matches played, record, goals, goal difference, official table position with its distance to the
power rank), an inline-SVG progression chart where clicking a
row highlights that team's line and greys the rest, and plain-language German text on what the
number can and cannot do plus a source link.

- **The chart's y-axis follows the data but is snapped to a 5-point grid and never narrower than 15
  points.** Without that floor an early season, where the league sits inside three points, would be
  blown up to full height and fake movement that isn't there.
- **A postponed match keeps its own matchday**, so playing it later corrects that matchday's point
  in the chart, while the Elo replay stays in true chronological order — the same order
  `run.rank()` uses, so table and chart cannot disagree.

## Publishing

GitHub Pages from `main` + `/docs`. No workflow: the committed `docs/index.html` *is* the deployment.

Pages is free only on **public** repos, which decides what may be committed. `.gitignore` excludes
`data/` (scraped match rows) and `tests/fixtures/` (verbatim fussball.de pages and their font
files) — the repo carries code and the derived page, nothing sourced from fussball.de. Two
consequences: **`data/matches.csv` exists only on the local machine and is not backed up by the
repo**, and `tests/test_parse.py` cannot run from a fresh clone.

fussball.de's Nutzungsbedingungen restrict systematic reuse and the data is the DFB's. For a
private, non-commercial ranking of one Staffel this is low-risk, but redistributing raw match data
publicly is the part that carries actual risk — publish only derived scores and link back.

## Tests

`tests/test_rating.py`: 15 property tests of the rating engine (symmetry, monotonicity in margin,
zero-sum updates, shrinkage). `tests/test_parse.py`: 5 tests running parser and font decoder against
2 saved pages in `tests/fixtures/`. Both need `pytest`, which is missing from the interpreter
currently on PATH (`python -m pytest` resolves to an unrelated venv).

## Not implemented

Considered and consciously left out:

- **Form (last N)** — `R_now − R_5_ago`, display only; blending form into the rating double-counts
  recent games, and the momentum test found nothing to blend.
- **Consistency/volatility** — SD of per-match rating deltas as a separate column, never folded into
  the score: with ~30 matches that SD is itself extremely noisy, and volatility is not
  straightforwardly good or bad.
- **Schedule difficulty ahead** — mean opponent rating of remaining fixtures, home/away adjusted. A
  site feature, not part of the score.
- **Forfeit detection** — `status="forfeit"` exists in the schema but is never set: a Spielwertung
  looks like an ordinary 0:2 in the schedule view, and the marker would need each match's detail
  page (~180 extra requests per season). 0:2/2:0 results are 7% of a season and only some are
  forfeits, so contamination is small. Revisit if a season shows unusually many.
- **Attack/defense split** (Poisson/Dixon-Coles) and **rating uncertainty** (Glicko-2) — both more
  informative than Elo, both needing roughly 2× the data or more parameters than one small league
  can tune. v2 alternatives, separate models.

**Not measurable from result data alone** — say so rather than faking it: match dominance
independent of the scoreline (needs xG or shots), squad quality and injuries (needs lineups), red
cards (needs match events), pitch and weather. fussball.de does not expose any of it for amateur
leagues. Do not build proxy metrics for them.

# Power Ranking — Pipeline

Power ranking for the Kreisliga A Hunsrück-Mosel from fussball.de result data.
14 teams, 26 matchdays, 182 matches per season. Live at
[bp571.github.io/power-ranking](https://bp571.github.io/power-ranking/).

Every stage below is implemented and in use. What is deliberately *not* implemented, and why, is in
[Not implemented](#not-implemented) — that section is the roadmap.

## Running it

```
python run.py                     # current season (2026/27): scrape -> rate -> write the page
python run.py --season 2025/26    # a past season
python run.py --cached            # parse saved pages in data/raw, no network
python src/report.py              # rebuild the page from data/matches.csv alone, no network
python src/backtest.py            # re-run the walk-forward evaluation
```

After a matchday:

```
python run.py
git commit -am "Spieltag N"
git push                          # GitHub Pages rebuilds in about a minute
```

## Pipeline

```
run.py
  │
  ├─ scrape.collect  fetch one page per matchday -> data/raw/     (network only)
  ├─ parse           pages -> match rows, then validate()          (raises on any gap)
  ├─ overrides       data/manual_overrides.csv wins over scraped rows
  ├─ save            upsert into data/matches.csv by match_id
  ├─ rating          replay chronologically -> Elo per team
  ├─ score           shrink + normalize -> power score 0-100
  └─ report          docs/index.html
```

**Scrape** ([src/scrape.py](src/scrape.py)) walks the matchdays of one Staffel, one request each,
4 s apart. **A season is identified by its Staffel id, not by the URL slug** — to add a season, take
its id from the season dropdown on any Staffel page and put it in `STAFFEL_IDS`.

fussball.de sheds load during a season walk (sporadic 503s). A second pass retries the refused
matchdays; a matchday that stays unavailable has to be filled in via `data/manual_overrides.csv`,
which is merged after parsing and wins over scraped rows.

Goal counts and dates on the page are obfuscated with per-request webfonts, so
[src/font_decoder.py](src/font_decoder.py) maps glyphs back to characters before parsing.

**Config** ([src/config.py](src/config.py)) holds everything tunable in one place: Staffel ids,
K/HFA/N0, team aliases, and the `KNOWN_TEAMS` validation set. `rating.py` and `score.py` take these
as defaults, so the backtest can still override them per call.

## Data contract

`data/matches.csv` is the contract; the scraper is a replaceable adapter behind it.

```
match_id      str    fussball.de's own match id (stable, 32 chars)
season        str    "2025/26"
matchday      int    1..26, taken from the page, not derived
date          str    ISO date "YYYY-MM-DD" (kickoff date)
home_team     str    canonical team name, mapped through config.TEAM_ALIASES
away_team     str    same
home_goals    int    >= 0, empty if not played
away_goals    int    >= 0, empty if not played
status        str    "played" | "scheduled"
```

Only `status == "played"` rows feed the model.

**Validation** runs on every scrape and raises rather than writing partial data: exactly
`n_teams * (n_teams - 1)` matches, every date parseable, scores 0–20, no team playing itself, and —
for the current season — the team set equal to `KNOWN_TEAMS`. A failure names the incomplete
matchdays.

Source data is only what fussball.de publishes for amateur leagues: date, both teams, the two goal
counts, matchday. No xG, no shots, no lineups — every metric has to be derivable from those fields.

## Model

**Elo with a logarithmic goal-difference multiplier and a home-field term**
([src/rating.py](src/rating.py), [src/score.py](src/score.py)). Matches are replayed in
chronological order; both teams' ratings are updated per match:

```
E_home = 1 / (1 + 10^(-((R_home + HFA) - R_away) / 400))
S      = 1 / 0.5 / 0                       win / draw / loss
margin = max(ln(|goal_diff| + 1), 0.8)     blowout damping
damp   = 2.2 / (0.001 * d_winner + 2.2)    d_winner = rating edge of the winning side
K      = 20 * margin * damp
R     += K * (S - E)
```

Display score, applied at render time, never fed back into the rating:

```
R_adj = 1500 + (R - 1500) * n / (n + 20)   n = matches played
POWER = clamp(50 + (R_adj - 1500) / 3, 0, 100)
```

The 0–100 mapping uses a **fixed** divisor, never min-max over the current league — min-max would
force the worst team to 0 and the best to 100 every week and destroy any sense of progress.

Why this model: with 14 teams each playing every other twice the schedule is balanced, so any
reasonable method agrees on the broad ordering — transparency decided it. Elo updates match by
match, which handles unequal games played and yields the rating history the progression chart
needs, and it has only three parameters to tune.

*Rejected:* Massey/Colley least-squares — solves strength-of-schedule exactly, but gives one
end-of-season snapshot rather than a trajectory, and Colley discards goal difference. Also rejected
for v1: per-team fitted models (Poisson, Bradley-Terry with margins) — 182 matches over 14 teams is
thin for parameter estimation, and the accuracy will not survive roster churn.

### What the model covers

| Aspect | How |
|---|---|
| Strength of schedule | Implicit in Elo; beating a high-rated opponent yields a larger update. No separate term. |
| Goal difference over points | Margin enters through a multiplier on K; `S ∈ {1, 0.5, 0}` retained. |
| Blowout damping | `max(ln(\|gd\|+1), 0.8)` plus the autocorrelation term, which stops favorites farming weak sides. |
| Home/away adjustment | `HFA = 100`, fitted from the observed home score rate, not copied from a reference. |
| Recency | Implicit: chronological Elo overwrites old results. No explicit decay — see [League dynamics](#league-dynamics-measured). |
| Unequal games played | Elo is per-match, so it is immune. `matches_played` is shown in the output. |
| Small-sample regression | `R_adj` above, at display time only. |

## The page

[src/report.py](src/report.py) writes one self-contained `docs/index.html` — no external assets, no
build step, no image files. It holds:

- the rank-sorted table: rank, team, power score, change vs. the previous matchday, matches played,
  record, goals, goal difference;
- an inline-SVG progression chart. Clicking a table row highlights that team's line and greys the
  rest, which is what keeps 14 series readable;
- plain-language German text stating what the number can and cannot do, and a source link.

Two rendering decisions worth keeping:

- **The chart's y-axis follows the data but is snapped to a 5-point grid and never narrower than 15
  points.** Without that floor an early season, where the whole league sits inside three points,
  would be blown up to full height and fake movement that isn't there.
- **A postponed match keeps its own matchday.** Playing it later corrects that matchday's point in
  the chart, while the Elo replay stays in true chronological order — the same order `run.rank()`
  uses, so the table and the chart cannot disagree.

**The page must not claim to predict better than the league table** — see the backtest below.
Overclaiming is the one failure mode that would make the project worse than not existing.

## Publishing

GitHub Pages, served from `main` + `/docs`. No workflow and no build step: the committed
`docs/index.html` *is* the deployment.

Pages is free only on **public** repos, which decides what may be committed. `.gitignore` therefore
excludes both `data/` (the scraped match rows) and `tests/fixtures/` (verbatim fussball.de pages and
their font files) — the repo carries code and the derived page, nothing sourced from fussball.de.
Two consequences: **`data/matches.csv` exists only on the local machine and is not backed up by the
repo**, and `tests/test_parse.py` cannot run from a fresh clone.

fussball.de's Nutzungsbedingungen restrict systematic reuse and the data is the DFB's. For a
private, non-commercial ranking of one Staffel this is low-risk, but redistributing raw match data
publicly is the part that carries actual risk — publish only derived scores and link back to
fussball.de as the source.

## Backtest

`python src/backtest.py` replays 2025/26 walk-forward — each match predicted from ratings of matches
strictly before it — turns the rating gap into home/draw/away probabilities with an ordered
logistic, and scores with RPS. The same calibration is fitted for every method, baselines included,
so the comparison is fair. Scored from matchday 6 on, n=147:

| Method | RPS | LogLoss |
|---|---|---|
| Model (Elo + margin, tuned) | **0.2170** | 0.9758 |
| Baseline 3: plain Elo, no margin | 0.2178 | 0.9778 |
| Baseline 2: table position | 0.2178 | 0.9775 |
| Baseline 1: league H/D/A base rate | 0.2209 | 0.9854 |

**The model is not meaningfully better than the league table.** The paired per-match advantage is
`+0.00078 ± 0.00171` (se), t = 0.46 — the model wins 77 of 147 matches, a coin flip. Splitting the
season by phase changes nothing; it is indistinguishable early *and* late.

This is not a bug, and it is roughly what the balanced schedule predicts: with 14 teams playing a
double round robin, everyone faces everyone twice, so there is little strength-of-schedule error
left for the model to correct. The ranking's value is as a different lens — margin-aware and
opponent-aware — not as a forecaster.

### League dynamics, measured

Three hypotheses about amateur football were checked against the 2025/26 season. Two hold, one does
not — and they were adjudicated by data rather than assumed, because "high variance" and "strong
momentum" pull the parameters in opposite directions.

| Hypothesis | Verdict | Evidence |
|---|---|---|
| Results scatter more than in higher leagues | **confirmed, strongly** | 28.6% of matches end with a 3+ goal margin; only 15.9% are draws. Simulating 14 *identical* teams over a full season yields an Elo SD of 35.6 against the 51.6 observed — **48% of the apparent spread is luck**. |
| Home advantage is more pronounced | **confirmed** | Home score rate 0.6401 → HFA = 100 Elo points, against roughly 60-70 in professional football. |
| Momentum is more extreme | **not detectable** | Lag-1 autocorrelation of rating residuals r = +0.004 (se 0.054); form over the last three matches predicting the next r = -0.023 (se 0.056). Both indistinguishable from zero. |

On momentum: 182 matches can only rule out a *large* effect (roughly |r| > 0.11), so a weak one may
hide. But nothing in this data justifies a recency term, and the streaks people remember are what
this test is designed to be fooled by. **Do not add recency weighting to the base rating.** A "Form"
column describing what recently happened is fine; selling it as predictive is not.

The variance finding is what set `N0 = 20`, and it is why the ranking looks flat in the first weeks:
after five matchdays the whole league sits inside roughly 47-54 power points. That is not a broken
scale, it is the correct answer to "who is better?" that early.

### Two methodological notes worth keeping

- **RPS cannot identify `HFA`.** It enters the predictor as a constant offset, which the calibration
  thresholds absorb completely, so the grid drifts monotonically without an optimum (RPS still
  falling at HFA=220). `backtest.estimate_hfa()` solves it directly instead: the rating gap whose
  Elo expectation equals the observed home score rate (0.6401 in 2025/26) → **HFA = 100**.
- `K_BASE` and the margin floor sit in a flat region — RPS varies in the 4th decimal across
  K = 10–25, far below the paired standard error of 0.0017. They are set to sensible values in that
  region, not to the grid's argmin. **Re-tuning them on a new season would be fitting noise.**

## Tests

`tests/test_rating.py` holds 15 property tests of the rating engine (symmetry, monotonicity in
margin, zero-sum updates, shrinkage behaviour). `tests/test_parse.py` runs the parser and the font
decoder against 2 saved fussball.de pages in `tests/fixtures/`, 5 tests.

Both need `pytest`, which is not installed in the interpreter currently on PATH on this machine
(`python -m pytest` resolves to an unrelated venv). `pip install -r requirements.txt` in the
project's own environment fixes it.

## Not implemented

Ideas that were considered and consciously left out. This is the roadmap.

| Idea | What it measures | How it would work | Cost |
|---|---|---|---|
| Form (last N) | What recently happened | Rating change over the last 5 matches, `R_now − R_5_ago`. **Display only** — blending form into the rating double-counts recent games, and the momentum test above found nothing to blend. | Low |
| Consistency vs. volatility | Whether a team is reliable or streaky | Standard deviation of per-match rating deltas, as a separate column — **not folded into the power score**, because with ~30 matches that standard deviation is itself extremely noisy and volatility is not straightforwardly good or bad. | Low |
| Schedule difficulty ahead | Preview of upcoming fixtures | Mean opponent rating of remaining fixtures, home/away adjusted. A site feature, not part of the score. | Low |
| Forfeit detection | Matches decided off the pitch carry no performance information | `status="forfeit"` exists in the schema but is never set: a Spielwertung looks like an ordinary 0:2 in the schedule view, and the marker would have to come from each match's detail page (~180 extra requests per season). **Deliberately deferred** — 0:2/2:0 results are 7% of a season and only a fraction of those are forfeits, so the contamination is small. Revisit if a season shows an unusual number of them. | Medium |
| Attack/defense split | Whether strength is offensive or defensive | Poisson/Dixon-Coles: fit per-team `attack` and `defense` parameters plus a home term by maximum likelihood on the goal counts. More interpretable than Elo and predicts exact scorelines, but needs ~2× the data to stabilize. Separate model, v2. | Medium |
| Rating uncertainty | Confidence interval per team | Switch to **Glicko-2**, which tracks a rating deviation and inflates it during inactivity. Better statistics, but the extra parameters are hard to tune on one small league. v2 alternative. | Medium |

**Not measurable from result data alone** — state this rather than faking it: match dominance
independent of the scoreline (needs xG or shot data), squad quality and injury effects (needs
lineups), red cards and their effect on the margin (needs match events), and pitch/weather context.
All of these require a data source fussball.de does not expose for amateur leagues. Do not build
proxy metrics for them.

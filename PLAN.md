# Power Ranking — Implementation Plan

Local amateur football league power ranking from fussball.de result data.
Kreisliga A Hunsrück-Mosel, 14 teams, 26 matchdays, 182 matches per season.

## Status

| Step | State |
|---|---|
| 1. Scrape + parse current season | **done** — validated against the site |
| 2. Previous season as backtest set | **done** — reconstructed table matches the published one exactly |
| 3. Rating engine | **done** — 14 property tests in `tests/test_rating.py` |
| 4. Backtest and tune | **done** — parameters fixed; **the model does not beat the league table** (see below) |
| 5. Publish (`report.py`, HTML) | **done** — one self-contained `out/ranking.html`; hosting open |

## How it works

```
run.py  --season 2026/27 (default) | 2025/26      --cached: parse saved pages, no network
  │
  ├─ scrape.collect  fetch one page per matchday -> data/raw/     (network only)
  ├─ parse           pages -> match rows, then validate()          (raises on any gap)
  ├─ overrides       data/manual_overrides.csv wins over scraped rows
  ├─ save            upsert into data/matches.csv by match_id
  ├─ rating          replay chronologically -> Elo per team
  ├─ score           shrink + normalize -> power score 0-100
  └─ report          docs/index.html (also standalone: python src/report.py)
```

`src/config.py` holds everything tunable: Staffel ids, K/HFA/N0, team aliases, and the
`KNOWN_TEAMS` validation set. **A season is identified by its Staffel id, not by the URL slug** —
to add a season, take its id from the season dropdown on any Staffel page.

`src/report.py` renders the page; `run.py` prints the ranking to stdout and writes it. The page can
be rebuilt from `data/matches.csv` alone, without touching the network: `python src/report.py`.

**Schema — `data/matches.csv`** (the contract; the scraper is a replaceable adapter):

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
`n_teams * (n_teams - 1)` matches, every date parseable, scores 0–20, no team playing itself,
and — for the current season — the team set equal to `KNOWN_TEAMS`. A failure names the
incomplete matchdays.

**Known limitations**
- fussball.de sheds load during a season walk (sporadic 503s). A second pass retries the refused
  matchdays; a matchday that stays unavailable must be filled in via `data/manual_overrides.csv`.
- `tests/test_parse.py` needs `pytest`, which is not installed in the interpreter on this machine.

## Aspects

### Core — in the model

| Aspekt | Status | Note |
|---|---|---|
| Strength of schedule | done | Implicit in Elo; beating a high-rated opponent yields a larger update. No separate term. |
| Goal difference over points | done | Margin enters through a multiplier on K; `S ∈ {1, 0.5, 0}` retained. |
| Blowout damping | done | `mult = max(ln(|gd|+1), 0.7)` plus the autocorrelation term `2.2/(d_winner·0.001+2.2)`. |
| Home/away adjustment | done, fitted | `HFA = 100`, derived from the observed home score rate, not copied from a reference. |
| Recency | done implicitly | Chronological Elo overwrites old results. No explicit decay in the base rating. |
| Unequal games played | structural | Elo is per-match, so it is immune. Show `matches_played` in the output. |
| Small-sample regression | done | `R_shown = 1500 + (R−1500)·n/(n+5)` at display time. |
| Form (last N) | open | Rating change over the last 5 matches, `R_now − R_5_ago`. **Display only** — blending form into the rating double-counts recent games. |

### Später — optional

| Aspekt | Was es misst | Berechnung | Gewichtung | Datenaufwand |
|---|---|---|---|---|
| Consistency vs. volatility | Whether a team is reliable or streaky | Standard deviation of per-match rating deltas. Report it as a separate "Volatilität" column — **do not fold it into the power score**, because with ~30 matches the standard deviation is itself extremely noisy and volatility is not straightforwardly good or bad. | Display only | Low |
| Attack/defense split | Whether strength is offensive or defensive | Poisson/Dixon-Coles style: fit per-team `attack` and `defense` parameters plus a home term by maximum likelihood on the goal counts. More interpretable output than Elo and predicts exact scorelines, but needs ~2× the data to stabilize. | Separate model, v2 | Medium |
| Rating uncertainty | Confidence interval per team | Switch from Elo to **Glicko-2**, which tracks a rating deviation and inflates it during inactivity. Better statistics, but the extra parameters are hard to tune on one small league. | v2 alternative | Medium |
| Schedule difficulty ahead | Preview of upcoming fixtures | Mean opponent rating of remaining fixtures, home/away adjusted. Nice site feature, not part of the score. | Display only | Low |
| Forfeit detection | Matches decided off the pitch carry no performance information | `status="forfeit"` exists in the schema but is never set: a Spielwertung looks like an ordinary 0:2 in the schedule view, and the marker would have to come from each match's detail page (~180 extra requests per season). **Deliberately deferred** — 0:2/2:0 results are 7% of a season and only a fraction of those are forfeits, so the contamination is small. Revisit if a season shows an unusual number of them. | Excluded from rating once implemented | Medium |

**Not measurable from result data alone** — state this rather than faking it: match dominance independent of the scoreline (needs xG or shot data), squad quality and injury effects (needs lineups), red cards and their effect on the margin (needs match events), and pitch/weather context. All of these require a data source fussball.de does not expose for amateur leagues. Do not build proxy metrics for them.

## Model

**Elo with a logarithmic goal-difference multiplier and a home-field term**, implemented in
[src/rating.py](src/rating.py) and [src/score.py](src/score.py). With 14 teams each playing every
other twice the schedule is balanced, so any reasonable method agrees on the broad ordering —
transparency decided it. Elo updates match by match, which handles unequal games played and yields
the rating history the progression chart needs, and it has only three parameters to tune.

*Rejected:* Massey/Colley least-squares — solves strength-of-schedule exactly, but gives one
end-of-season snapshot rather than a trajectory, and Colley discards goal difference. Also
rejected for v1: per-team fitted models (Poisson, Bradley-Terry with margins) — 182 matches over
14 teams is thin for parameter estimation, and the accuracy will not survive roster churn.

The 0–100 mapping uses a **fixed** divisor (`POWER = clamp(50 + (R_adj−1500)/8, 0, 100)`), never
min-max over the current league — min-max would force the worst team to 0 and the best to 100
every week and destroy any sense of progress. Check the spread in the backtest, adjust the divisor
once, then leave it fixed.

## Backtest result (step 4, done)

`python src/backtest.py` replays 2025/26 walk-forward — each match predicted from ratings of
matches strictly before it — turns the rating gap into home/draw/away probabilities with an ordered
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
left for the model to correct. **Whatever the page claims, it must not claim to predict better than
the table.** The ranking's value is as a different lens — margin-aware and opponent-aware — not as a
forecaster.

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

The variance finding is what set `N0 = 20` (see `config.py`), and it is why the ranking looks flat
in the first weeks: after five matchdays the whole league sits inside 47.5-53.7 power points. That
is not a broken scale, it is the correct answer to "who is better?" that early.

### Two methodological notes worth keeping:

- **RPS cannot identify `HFA`.** It enters the predictor as a constant offset, which the calibration
  thresholds absorb completely, so the grid drifts monotonically without an optimum (RPS still
  falling at HFA=220). `backtest.estimate_hfa()` solves it directly instead: the rating gap whose
  Elo expectation equals the observed home score rate (0.6401 in 2025/26) → **HFA = 100**.
- `K_BASE` and the margin floor sit in a flat region — RPS varies in the 4th decimal across
  K = 10–25, far below the paired standard error of 0.0017. They are set to sensible values in that
  region, not to the grid's argmin. Re-tuning them on a new season would be fitting noise.

## Remaining work

**Step 5 — Publish (done).** `src/report.py` writes one self-contained `docs/index.html`: no
external assets, no build step, no image files — upload the single file. It holds the rank-sorted
table (rank, team, power score, change vs. last matchday, matches played, record, goals, goal
difference) and an inline-SVG progression chart; clicking a table row highlights that team's line
and greys the rest, which is what keeps 14 series readable. The chart's y-axis follows the data but
is snapped to a 5-point grid and never narrower than 15 points — without that floor an early season,
where the whole league sits inside three points, would be blown up to full height and fake movement
that isn't there. The power score itself is still on the fixed 0–100 scale.

The page states the step-4 finding in plain language — this ranking does not predict results better
than the league table, it reads the same season differently — plus why the table looks flat early
and how much of the spread is luck. Overclaiming is the one failure mode that would make the
project worse than not existing.

*Deliberately dropped:* the WhatsApp PNG and matplotlib charts. One HTML page is the whole output.

**Hosting — GitHub Pages, served from `main` + `/docs`.** No workflow and no build step: the
committed `docs/index.html` *is* the deployment, so publishing a matchday is `python run.py` plus a
commit and push.

Pages is free only on **public** repos, which decides what may be committed. `.gitignore` therefore
excludes both `data/` (the scraped match rows) and `tests/fixtures/` (verbatim fussball.de pages and
their font files) — the repo carries code and the derived page, nothing sourced from fussball.de.
Two consequences: **`data/matches.csv` exists only on the local machine and is not backed up by the
repo**, and `tests/test_parse.py` cannot run from a fresh clone.

**Open:** have someone in the league who has not seen the project read the table and correctly say
why the second-placed team outranks the leader.

**Publishing note:** fussball.de's Nutzungsbedingungen restrict systematic reuse and the data is the
DFB's. For a private, non-commercial ranking of one Staffel this is low-risk, but redistributing raw
match data publicly is the part that carries actual risk — publish only derived scores and link back
to fussball.de as the source.

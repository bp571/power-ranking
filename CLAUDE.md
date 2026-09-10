# Power Ranking

Power ranking for the Kreisliga A Hunsrück-Mosel from fussball.de result data — 14 teams,
26 matchdays, 182 matches per season. Live at
[bp571.github.io/power-ranking](https://bp571.github.io/power-ranking/).

It answers the one question the official table cannot: **who is playing well right now?** The table
is a season-long ledger, so a side that started badly and has since turned it around still reads as
a bottom-four team. The page leads with a form rating over the last five matchdays — opponent- and
margin-aware, so it is not just points — and keeps the season-long power score beside it for
context. The canonical case: SC Weiler finished 2025/26 tenth in the table and third in form.

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
python src/predict.py             # next matchday's probabilities in the terminal
python src/explore_predictors.py  # the predictor comparison the page publishes
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
  └─ report          + form over the last 5 matchdays -> docs/index.html
                     + predict     scheduled rows -> next matchday's probabilities
                     + explore_predictors  the candidate table published beside them
```

- The forecast is the one part that reads `status="scheduled"` rows; everything else ignores
  them. A season with nothing scheduled left simply renders without that section.

- **A season is identified by its Staffel id, not by the URL slug** — to add one, take its id from
  the season dropdown on any Staffel page and put it in `STAFFEL_IDS`.
- fussball.de sheds load during a season walk (sporadic 503s). A second pass retries the refused
  matchdays; one that stays unavailable has to be filled in via `data/manual_overrides.csv`.
- Goal counts and dates are obfuscated with per-request webfonts;
  [src/font_decoder.py](src/font_decoder.py) maps glyphs back to characters before parsing.
- [src/config.py](src/config.py) holds everything tunable in one place: Staffel ids, K/HFA/N0,
  `FORM_WINDOW`, team aliases, `KNOWN_TEAMS`. `rating.py`, `score.py` and `report.py` take these as
  defaults, so the backtest can override them per call.

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

Two more, measured when the page was rebuilt around form:

- **Teams drift within a season — direction visible, rate unmeasurable.** Hinrunde and Rückrunde
  contain the *same 91 pairings*, so strength of schedule is controlled by construction and the
  split-half correlation is a clean drift test. It has almost no power: simulated seasons ranging
  from no drift to `corr(strength MD1, MD26) = 0.2` put the observed r = +0.084 between the 42nd
  and 56th percentile, indistinguishable. A drifting state-space model agreed by failing the same
  way — its likelihood was flat in the drift rate over the whole plausible range. **Do not try to
  estimate a drift rate from one season.** The *ordering* is solid, though: the risers and fallers
  the raw results show do come out in the right order, which is what the form window reports.
- **A short window cannot measure current strength — it can only describe.** Simulating known true
  strengths and rating from a trailing window only: last 4 matchdays r = 0.21 with the truth,
  last 8 r = 0.28, last 13 r = 0.36, all 26 r = 0.47. Halving the window costs roughly half the
  remaining signal. `FORM_WINDOW = 5` is chosen against that knowledge, not in ignorance of it,
  and the page says outright that the column describes rather than predicts.

Five more, measured when the page gained a forecast (`python src/explore_predictors.py`, same
protocol as the backtest: one ordered logistic with three parameters per candidate, fitted on
2025/26 and scored from matchday 6 on, n=147, paired against the league's own H/D/A rates):

| Candidate | RPS | lead over the base rate |
|---|---|---|
| Form, last 5 matchdays | 0.2157 | +0.0052 ± 0.0052 |
| Expected goals (Poisson) | 0.2164 | +0.0045 ± 0.0044 |
| Season Elo | 0.2170 | +0.0039 ± 0.0048 |
| Table position | 0.2178 | +0.0031 ± 0.0039 |
| Points per game | 0.2185 | +0.0024 ± 0.0023 |
| Rest days before the match | 0.2207 | +0.0002 ± 0.0006 |
| Club's own home strength | 0.2207 | +0.0002 ± 0.0007 |
| League H/D/A base rate | 0.2209 | reference |

- **Nothing clears two standard errors, and the ordering itself is noise.** Refitting the
  calibration walk-forward on each matchday instead of once on the season moves every candidate
  into 0.223–0.230 and reshuffles them — season Elo then lands *exactly* on the base rate. With
  ~13 candidates tried, a best t of 1.8 is what the null produces. **Do not read this table as a
  ranking of methods**; read it as the spread being smaller than its own error.
- **Form does not predict, even where it looks best.** Its top row here is in-sample calibration
  and t = 1.01; walk-forward it is t = 0.34. This changes nothing about `FORM_WINDOW` or about the
  page's wording — the momentum finding above still stands.
- **No club has a home advantage of its own.** Split-half over the season (each side's home
  over-performance in the Hinrunde against the Rückrunde) gives r = **−0.194** over 14 teams. The
  league-wide `HFA = 100` is the whole story. **Do not add per-team venue terms.**
- **Rest days are worse than nothing** — the irregular amateur fixture list (midweek, cup,
  postponements) makes this pure overfitting.
- **Goals carry at least as much as results.** The Poisson expected-goal difference is the one
  candidate that is stable across shrinkage settings (2/4/8 all land at 0.223 walk-forward). Not
  significant either, but it is why `predict.py` forecasts from goals rather than from the Elo gap.

Two methodological notes:

- **RPS cannot identify `HFA`.** It enters as a constant offset that the calibration thresholds
  absorb completely, so the grid drifts monotonically without an optimum. `backtest.estimate_hfa()`
  solves it directly: the rating gap whose Elo expectation equals the observed home score rate.
- **`K_BASE` and the margin floor sit in a flat region** — RPS varies in the 4th decimal across
  K = 10–25, far below the paired se of 0.0017. They are set to sensible values there, not to the
  grid's argmin. **Re-tuning them on a new season would be fitting noise.**

## The page

[src/report.py](src/report.py) writes one self-contained `docs/index.html` — no external assets, no
build step, no image files. Form-sorted table (rank, team with its last five results, form, change
vs. previous matchday, season power score, matches played, record, goals, goal difference, official
table position with its distance to the form rank), a full-width pitch laying the league out by
form, one inline-SVG progression chart for the form window — clicking a row highlights that team in
table, pitch and chart at once — then the next matchday's forecast and the predictor table behind
it, side by side in the same grid, and a source link.

**Every explanation sits with the thing it explains.** The page carries no essay at the end: the
legend for the form table, the notes under the forecast and the note under the predictor table are
all the prose there is, and each one is next to its own table. The one sentence that cannot be
dropped is the disclaimer in the form table's subline — five matches describe, they do not predict.

**The page is sorted by form, not by the power score.** The table already tells a reader who has
the points; what it cannot tell them is that the team in twelfth has won four of five. That gap is
the page's reason to exist, so `report.form_series()` — Elo over the last `FORM_WINDOW = 5`
matchdays only, restarted from 1500 at every matchday — is the headline number, and the power
score rides alongside in a quieter **Saison** column. The chip beside the official position is now
the distance to the *form* rank: 2025/26 ends with SC Weiler tenth in the table and third in form.

**Three editorial markers, each by a fixed rule with a floor**, so a reader can check a badge
against the row it sits on: *Mannschaft der Stunde* (largest positive gap from form rank to table
position, needs ≥ 2 places), *Formsprung* (largest gain over the previous matchday, needs ≥ 1.5
points, skipped if it would land on the same row), *Topspiel* in the forecast (best combined form of
the two sides — deliberately **not** "closest percentages", which span a few points all season and
would mark noise). Below their floors the badges simply do not render. They are drawn in an amber
that no data uses, because green and wine mean above and below average everywhere else on the page.

Two consequences that must not be undone by accident:

- **The form rating gets no shrinkage** (`to_power()`, not `normalize_to_power_score()`). With
  `N0 = 20` a five-match window keeps 5/25 of its deviation and the whole league collapses back
  onto 50, which erases the column. The honesty lives in the wording instead — the form table's
  own subline says outright that five matches describe rather than measure.
- **The window is hard, not a decay.** Down-weighting old matches instead — Elo with a bigger K,
  or a drifting state-space filter, both tried and both removed — cannot go this short: reweighting
  keeps every match in the estimate forever, so the effective memory stalls around nine matchdays
  and the values leave the 0–100 scale before it gets shorter. Restarting from 1500 at each
  matchday drops them outright, which is the only thing that produces a five-match view.

- **The chart's y-axis follows the data but is snapped to a 5-point grid and never narrower than 15
  points.** Without that floor an early season, where the league sits inside three points, would be
  blown up to full height and fake movement that isn't there.
- **A postponed match keeps its own matchday**, so playing it later corrects that matchday's point
  in the chart, while the Elo replay stays in true chronological order.
- **`run.rank()` delegates to `report.build_table()`** rather than replaying the season a second
  time, so the terminal output and the published page are the same numbers in the same order by
  construction. It prints form, season score, matches and the official position with its distance
  to the form rank — the same columns the page leads with.

### The forecast

Below the dashboard, [src/predict.py](src/predict.py) turns the next matchday into H/D/A
percentages and expected goals, and [src/explore_predictors.py](src/explore_predictors.py) puts the
candidate table straight underneath it. Publishing a forecast is only defensible next to the
measurement that says how little it is worth, so **the two sections ship together** — never the
forecast alone.

Four things that must not be undone by accident:

- **One model for the whole section.** A small Poisson attack/defence fit gives both sides an
  expected number of goals, and the *same* difference goes through the ordered logistic to become
  the percentages. Elo-based percentages beside Poisson goals contradicted each other in sign in
  **31%** of 2025/26 matches, which on a page reads as a bug rather than as two lenses. If the
  forecast ever moves back to the Elo gap, the goals column has to go with it.
- **The goals column shows the match total, never the pair.** `1,3 : 2,8` next to "44% Heim" is
  the same contradiction one model deeper: the two expected goals are weak evidence and the
  percentages correctly fall back towards the home-heavy base rate, but two numbers of very
  different evidential weight sitting side by side with equal visual weight read as a bug. The sum
  (`4,1`) carries the one thing the percentages do not — open game or grind — and cannot disagree
  with them. **Do not restore the per-team pair.** Measured: bucketing 2025/26 by expected-goal
  difference, the fitted slope β = 0.266 *is* the maximum-likelihood value and the likelihood is
  flat from β = 0.15 to 0.45 (nll moves 0.35 over 168 matches). One goal of expected supremacy is
  worth about six percentage points of win probability in this league. That is the finding, not a
  damping bug.
- **The calibration is fitted on `SEASON_PREVIOUS`**, so it never sees a match it scores. This is
  the one place a stale season id would silently degrade the page rather than raise.
- **The track record is recomputed, not stored.** A forecast is a pure function of the results
  before it, so replaying `matches.csv` reproduces exactly what the page showed — no
  `predictions.csv` to drift out of sync. It is compared against the constant league base rate,
  which is the honest zero point, and the page prints the comparison even while it is losing.
- **The percentages are flat on purpose.** They span roughly 44–65% for the home side and a draw
  is never the most likely outcome. Both surprise readers, so the page explains both in as many
  words rather than hiding them.

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
- **Attack/defense split in the *ranking*** (Poisson/Dixon-Coles). A shrunk Poisson fit now drives
  the forecast, where its expected goals are the point; folding it into the ranking is a different
  claim and still needs roughly 2× the data. The lever for that is more Staffeln, not a bigger
  model: ten of them is ~1800 matches a season and only costs another id in `STAFFEL_IDS`.
- **Storing published forecasts** — a `data/predictions.csv` would be state that can drift from the
  code that wrote it, for a record `predict.track_record()` recomputes exactly. Only worth it if
  the forecast method ever changes mid-season, and then the honest move is to reset the record.
- **Carrying ratings over between seasons.** Last season's final ratings beat this season's own Elo
  after five matchdays (RPS 0.2322 vs 0.2395), which is what the "48% of the spread is luck"
  finding predicts. But 7 of 14 teams are new in 2026/27 and n=34 — far too thin to act on. Revisit
  only in a season with little promotion and relegation churn.
- **A drifting state-space model** (Glicko-2 / Kalman) — built, measured, removed. It rates
  strength as a hidden state with a random walk and reports an uncertainty band, which is the
  principled way to ask "how strong now". Two findings killed it: the drift rate is unidentifiable
  from one season (flat likelihood, see Evidence), and its memory cannot be pushed below ~9
  matchdays, so it answered the season question a second time instead of the form question. RPS
  was a tie with Elo throughout. **Do not rebuild it without new data** — more Staffeln would
  change that calculus, a new season alone would not.

**Not measurable from result data alone** — say so rather than faking it: match dominance
independent of the scoreline (needs xG or shots), squad quality and injuries (needs lineups), red
cards (needs match events), pitch and weather. fussball.de does not expose any of it for amateur
leagues. Do not build proxy metrics for them.

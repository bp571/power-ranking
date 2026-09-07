# Power Ranking

A power ranking for a local German amateur football league (~16 teams, ~30 matchdays). It answers
the one question the official table cannot: how strong is a team really, once you account for who
they actually played?

The league table rewards points alone and ignores opponent quality — a team that beat the bottom
four looks identical to one that beat the top four. This project rates every team from match
results instead, so strength of schedule, goal margin and home advantage are part of the number.

Source data is fussball.de. Only what that site publishes for amateur leagues exists: date, both
teams, the two goal counts, matchday. No xG, no shots, no lineups — every metric has to be
derivable from those fields alone.

Built and maintained by one person as a side project, in Python. The output is shared with the
league's players as a simple static web page and a shareable image. Simplicity beats
sophistication: the ranking has to be explainable to a teammate in one sentence, and the whole
thing has to keep working with a few minutes of attention after each matchday.

The full technical concept — data acquisition, schema, model, validation, roadmap — is in
[PLAN.md](PLAN.md).

Notes for Claude: the user writes in German; PLAN.md is in English.

"""Render the ranking as a single self-contained HTML page (out/ranking.html).

Only derived numbers are published - power scores, records, goal difference -
never the scraped match rows themselves; fussball.de is linked as the source.
"""

import csv
import html
import math
import os
from collections import defaultdict
from datetime import date

from config import MATCHES_CSV, POWER_SCALE_DIVISOR, SEASON_CURRENT, STAFFEL_NAME
from rating import EloRating
from score import normalize_to_power_score

# docs/ is what GitHub Pages serves from the main branch, so the generated page
# is the deploy - no build step, no workflow.
OUT_HTML = os.path.join(os.path.dirname(__file__), "..", "docs", "index.html")

SOURCE_URL = "https://www.fussball.de"

# The y-axis follows the data, but snapped to a 5-point grid and never narrower
# than Y_MIN_SPAN. Without that floor an early season - where the whole league
# sits inside three points - would be blown up to full height and fake movement
# that isn't there.
Y_GRID = 5
Y_MIN_SPAN = 15

# One colour per rank slot; only selected teams use theirs, the rest stay grey.
PALETTE = [
    "#1f77b4", "#d62728", "#2ca02c", "#ff7f0e", "#9467bd", "#8c564b", "#e377c2",
    "#17becf", "#bcbd22", "#7f7f7f", "#393b79", "#b5651d", "#5254a3", "#637939",
]


def num(x, decimals=1):
    """German decimal comma."""
    return f"{x:.{decimals}f}".replace(".", ",")


def load_played(season):
    with open(MATCHES_CSV, encoding="utf-8", newline="") as f:
        return [r for r in csv.DictReader(f) if r["season"] == season and r["status"] == "played"]


def replay(rows):
    """Chronological Elo replay - the same order run.rank() uses, so the final
    ratings agree - recording each team's power score after each of its matches.

    A postponed match keeps its own matchday, so playing it later corrects that
    matchday's point in the chart while the ratings stay in true match order.
    """
    teams = sorted({r[side] for r in rows for side in ("home_team", "away_team")})
    elo = EloRating()
    elo.initialize_teams(teams)

    played = {t: 0 for t in teams}
    by_matchday = {t: {} for t in teams}  # team -> matchday -> power after that match

    for r in sorted(rows, key=lambda r: (r["date"], int(r["matchday"]))):
        md = int(r["matchday"])
        elo.update_from_match(
            r["home_team"], r["away_team"], int(r["home_goals"]), int(r["away_goals"]), md
        )
        ratings = elo.get_ratings()
        for team in (r["home_team"], r["away_team"]):
            played[team] += 1
            by_matchday[team][md] = normalize_to_power_score(ratings[team], played[team])

    matchdays = sorted({int(r["matchday"]) for r in rows})
    series = {}
    for team in teams:
        values, last = [], None
        for md in matchdays:
            last = by_matchday[team].get(md, last)
            values.append(last)
        series[team] = values

    return elo.get_ratings(), played, matchdays, series


def team_stats(rows):
    """Record and goals per team, straight from the results."""
    stats = defaultdict(lambda: {"w": 0, "d": 0, "l": 0, "gf": 0, "ga": 0, "pts": 0})
    for r in rows:
        hg, ag = int(r["home_goals"]), int(r["away_goals"])
        h, a = stats[r["home_team"]], stats[r["away_team"]]
        h["gf"] += hg
        h["ga"] += ag
        a["gf"] += ag
        a["ga"] += hg
        if hg > ag:
            h["w"] += 1
            a["l"] += 1
            h["pts"] += 3
        elif hg < ag:
            h["l"] += 1
            a["w"] += 1
            a["pts"] += 3
        else:
            h["d"] += 1
            a["d"] += 1
            h["pts"] += 1
            a["pts"] += 1
    return stats


def official_positions(stats):
    """Position in the official table: points, then goal difference, then goals
    scored - the tie-breaks fussball.de uses before a direct comparison."""
    order = sorted(
        stats,
        key=lambda t: (stats[t]["pts"], stats[t]["gf"] - stats[t]["ga"], stats[t]["gf"]),
        reverse=True,
    )
    return {team: i + 1 for i, team in enumerate(order)}


def build_table(rows):
    ratings, played, matchdays, series = replay(rows)
    stats = team_stats(rows)
    positions = official_positions(stats)

    table = []
    for team, values in series.items():
        power = values[-1]
        previous = values[-2] if len(values) > 1 else None
        s = stats[team]
        table.append(
            {
                "team": team,
                "power": power,
                "delta": None if previous is None else power - previous,
                "matches": played[team],
                "record": (s["w"], s["d"], s["l"]),
                "gf": s["gf"],
                "ga": s["ga"],
                "position": positions[team],
                "series": values,
            }
        )
    table.sort(key=lambda t: t["power"], reverse=True)
    return table, matchdays


def y_axis(table):
    """Snapped, minimum-width range covering every plotted value."""
    values = [v for t in table for v in t["series"]]
    lo = int(math.floor((min(values) - 1) / Y_GRID)) * Y_GRID
    hi = int(math.ceil((max(values) + 1) / Y_GRID)) * Y_GRID
    while hi - lo < Y_MIN_SPAN:
        lo -= Y_GRID
        if hi - lo < Y_MIN_SPAN:
            hi += Y_GRID
    step = Y_GRID if hi - lo <= 30 else 2 * Y_GRID
    return lo, hi, step


def svg_chart(table, matchdays):
    """Inline SVG, one polyline per team. Selection is done in the browser."""
    w, h = 820, 380
    left, right, top, bottom = 44, 14, 14, 30
    span = max(len(matchdays) - 1, 1)
    y_min, y_max, y_step = y_axis(table)

    def x(i):
        return left + (w - left - right) * i / span

    def y(power):
        frac = (power - y_min) / (y_max - y_min)
        return h - bottom - (h - bottom - top) * frac

    parts = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Verlauf der Power Scores">']

    for tick in range(y_min, y_max + 1, y_step):
        parts.append(
            f'<line class="grid" x1="{left}" y1="{y(tick):.1f}" x2="{w - right}" '
            f'y2="{y(tick):.1f}"/>'
            f'<text class="tick" x="{left - 8}" y="{y(tick) + 4:.1f}" '
            f'text-anchor="end">{tick}</text>'
        )
    for i, md in enumerate(matchdays):
        if len(matchdays) <= 14 or md % 2 == 0 or i == len(matchdays) - 1:
            parts.append(
                f'<text class="tick" x="{x(i):.1f}" y="{h - 10}" '
                f'text-anchor="middle">{md}</text>'
            )
    for rank, t in enumerate(table):
        points = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(t["series"]))
        dots = "".join(
            f'<circle cx="{x(i):.1f}" cy="{y(v):.1f}" r="2.5"/>'
            for i, v in enumerate(t["series"])
        )
        parts.append(
            f'<g class="line" id="line{rank}" style="--c:{PALETTE[rank % len(PALETTE)]}">'
            f'<polyline points="{points}"/>{dots}</g>'
        )

    parts.append("</svg>")
    return "\n".join(parts)


def render(season, rows):
    table, matchdays = build_table(rows)
    matchday = matchdays[-1]
    last_date = max(r["date"] for r in rows)
    last_date = ".".join(reversed(last_date.split("-")))
    generated = date.today().strftime("%d.%m.%Y")

    body_rows = []
    for rank, t in enumerate(table):
        w, d, l = t["record"]
        if t["delta"] is None:
            delta = '<span class="flat">–</span>'
        else:
            sign = "+" if t["delta"] >= 0 else "−"
            cls = "up" if t["delta"] > 0.05 else ("down" if t["delta"] < -0.05 else "flat")
            delta = f'<span class="{cls}">{sign}{num(abs(t["delta"]))}</span>'
        # Positive: the power ranking places the team higher than the table does.
        diff = t["position"] - (rank + 1)
        if diff == 0:
            gap = '<span class="flat">±0</span>'
        else:
            cls = "up" if diff > 0 else "down"
            gap = f'<span class="{cls}">{"+" if diff > 0 else "−"}{abs(diff)}</span>'
        body_rows.append(
            f'<tr data-rank="{rank}" style="--c:{PALETTE[rank % len(PALETTE)]}">'
            f'<td class="rank">{rank + 1}</td>'
            f'<td class="team"><span class="sw"></span>{html.escape(t["team"])}</td>'
            f'<td class="power">{num(t["power"])}</td>'
            f"<td>{delta}</td>"
            f"<td>{t['matches']}</td>"
            f"<td>{w}-{d}-{l}</td>"
            f"<td>{t['gf']}:{t['ga']}</td>"
            f"<td>{t['gf'] - t['ga']:+d}</td>"
            f"<td class=\"tab\">{t['position']}{gap}</td>"
            f"</tr>"
        )

    return f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Power Ranking – {html.escape(STAFFEL_NAME)} {season}</title>
<style>
  :root {{ --fg:#1a1d21; --muted:#6b7280; --grey:#cbd0d6; --line:#e5e7eb; --bg:#fff; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--fg);
         font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }}
  main {{ max-width:820px; margin:0 auto; padding:28px 16px 64px; }}
  h1 {{ font-size:26px; margin:0 0 4px; }}
  h2 {{ font-size:17px; margin:36px 0 8px; }}
  .sub {{ color:var(--muted); margin:0 0 24px; font-size:14px; }}
  p {{ margin:0 0 12px; }}
  .lead {{ font-size:15px; }}
  table {{ width:100%; border-collapse:collapse; font-size:15px; }}
  th, td {{ padding:7px 6px; text-align:right; border-bottom:1px solid var(--line); }}
  th {{ font-size:12px; text-transform:uppercase; letter-spacing:.04em;
        color:var(--muted); font-weight:600; white-space:nowrap; }}
  th:nth-child(2), td.team {{ text-align:left; }}
  td.rank {{ color:var(--muted); width:26px; }}
  td.power {{ font-weight:700; font-variant-numeric:tabular-nums; }}
  td {{ font-variant-numeric:tabular-nums; }}
  tbody tr {{ cursor:pointer; }}
  tbody tr:hover {{ background:#f7f8fa; }}
  .sw {{ display:inline-block; width:9px; height:9px; border-radius:2px;
         background:var(--grey); margin-right:8px; vertical-align:middle; }}
  tr.sel .sw {{ background:var(--c); }}
  tr.sel td.team {{ font-weight:600; }}
  .up {{ color:#15803d; }} .down {{ color:#b91c1c; }} .flat {{ color:var(--muted); }}
  td.tab span {{ margin-left:6px; font-size:13px; }}
  .chart {{ margin-top:8px; }}
  svg {{ width:100%; height:auto; display:block; }}
  .grid {{ stroke:var(--line); stroke-width:1; }}
  .tick {{ fill:var(--muted); font-size:11px; }}
  .line polyline {{ fill:none; stroke:var(--grey); stroke-width:1.5;
                    stroke-linejoin:round; }}
  .line circle {{ fill:var(--grey); }}
  .line.sel polyline {{ stroke:var(--c); stroke-width:2.5; }}
  .line.sel circle {{ fill:var(--c); }}
  .hint {{ color:var(--muted); font-size:13px; margin:10px 0 0; }}
  .note {{ font-size:14px; color:var(--fg); background:#f7f8fa;
           border-left:3px solid var(--grey); padding:12px 14px; margin:16px 0; }}
  footer {{ margin-top:40px; padding-top:14px; border-top:1px solid var(--line);
            color:var(--muted); font-size:13px; }}
  a {{ color:inherit; }}
</style>
</head>
<body>
<main>
  <h1>Power Ranking</h1>
  <p class="sub">{html.escape(STAFFEL_NAME)} · Saison {season} · Stand: Spieltag {matchday}
     ({last_date})</p>

  <p class="lead">Der Power Score bewertet jedes Team danach, <strong>gegen wen</strong> es
  gespielt hat und <strong>wie deutlich</strong> die Ergebnisse ausfielen – nicht nur danach,
  wie viele Punkte am Ende dastehen. Ein Sieg gegen einen starken Gegner zählt mehr als einer
  gegen einen schwachen, und ein 4:0 mehr als ein 1:0. 50 ist Ligadurchschnitt.</p>

  <table>
    <thead>
      <tr>
        <th>#</th><th>Team</th><th>Power</th><th>+/&minus;</th>
        <th>Sp</th><th>S-U-N</th><th>Tore</th><th>Diff</th><th>Tabelle</th>
      </tr>
    </thead>
    <tbody>
      {chr(10).join("      " + r for r in body_rows).strip()}
    </tbody>
  </table>
  <p class="hint"><strong>Tabelle</strong> ist der offizielle Tabellenplatz; der Wert dahinter
  ist die Differenz zum Platz in diesem Ranking. <span class="up">+2</span> heißt: hier zwei
  Plätze besser als in der Tabelle, das Team hat also für seine Punkte die stärkeren Gegner
  geschlagen oder deutlicher gewonnen. Die Spalte <strong>+/&minus;</strong> ist dagegen die
  Veränderung des Power Scores gegenüber dem letzten Spieltag.</p>

  <h2>Verlauf</h2>
  <div class="chart">{svg_chart(table, matchdays)}</div>
  <p class="hint">Zeile in der Tabelle anklicken, um ein Team im Diagramm hervorzuheben.
  X-Achse: Spieltag, Y-Achse: Power Score. Achtung beim Vergleich mit früheren Wochen:
  die Y-Achse passt sich dem aktuellen Wertebereich an.</p>

  <h2>Was die Zahl kann – und was nicht</h2>
  <div class="note"><strong>Dieses Ranking sagt Spiele nicht besser voraus als die
  Tabelle.</strong> Das wurde an der kompletten Vorsaison nachgerechnet: Vorhersagen aus dem
  Power Score sind statistisch genauso gut wie Vorhersagen aus dem Tabellenplatz, der
  Unterschied ist reines Rauschen. Das ist auch zu erwarten – bei 14 Teams spielt jeder gegen
  jeden zweimal, damit hat am Ende niemand einen leichteren Spielplan gehabt. Der Power Score
  ist ein <em>anderer Blick</em> auf dieselbe Saison, keine Glaskugel.</div>

  <p>Zwei Dinge, die man beim Lesen wissen sollte:</p>
  <p><strong>Am Saisonanfang liegt alles eng beieinander.</strong> Nach fünf Spieltagen steht
  die ganze Liga in einem Bereich von wenigen Punkten um die 50. Das ist kein Fehler, sondern
  die ehrliche Antwort: So früh weiß man schlicht noch nicht, wer besser ist. Erst mit mehr
  Spielen zieht sich das Feld auseinander.</p>
  <p><strong>Ein großer Teil des Abstands ist Zufall.</strong> In dieser Liga enden 28,6 % der
  Spiele mit drei oder mehr Toren Unterschied. Eine Simulation mit 14 exakt gleich starken
  Teams erzeugt allein durch Glück rund die Hälfte der Streuung, die real zu sehen ist.
  Deshalb werden die Werte bewusst zur Mitte hin gedämpft – kleine Unterschiede in der Tabelle
  bedeuten wenig.</p>

  <h2>Wie gerechnet wird</h2>
  <p>Ein Elo-System, wie man es vom Schach kennt: Jedes Team startet bei 1500 Punkten, nach
  jedem Spiel gibt der Verlierer Punkte an den Sieger ab. Wie viele, hängt davon ab, wie
  überraschend das Ergebnis war und wie hoch gewonnen wurde. Heimvorteil ist eingerechnet
  (er ist in dieser Liga rund 100 Elo-Punkte wert, gemessen an der eigenen Saison, nicht
  geschätzt). Am Ende wird das Rating auf eine Skala von 0 bis 100 umgelegt, mit
  {num(POWER_SCALE_DIVISOR)} Elo-Punkten je Power-Punkt. Nur ausgetragene Spiele zählen;
  ungleiche Spielanzahl ist deshalb kein Problem.</p>

  <footer>
    Datenquelle: <a href="{SOURCE_URL}">fussball.de</a> (DFB) – dort stehen die offizielle
    Tabelle und alle Ergebnisse. Diese Seite zeigt nur daraus berechnete Werte.
    Privates, nicht-kommerzielles Projekt · Stand der Berechnung: {generated}
  </footer>
</main>
<script>
  const rows = [...document.querySelectorAll('tbody tr')];
  const svg = document.querySelector('svg');
  const selected = new Set(['0', '1', '2']);

  function apply() {{
    rows.forEach(tr => {{
      const on = selected.has(tr.dataset.rank);
      const line = document.getElementById('line' + tr.dataset.rank);
      tr.classList.toggle('sel', on);
      line.classList.toggle('sel', on);
      if (on) svg.appendChild(line);   // draw highlighted lines on top
    }});
  }}

  rows.forEach(tr => tr.addEventListener('click', () => {{
    const r = tr.dataset.rank;
    selected.has(r) ? selected.delete(r) : selected.add(r);
    apply();
  }}));

  apply();
</script>
</body>
</html>
"""


def write_report(season, rows, path=OUT_HTML):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(render(season, rows))
    return path


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Rebuild out/ranking.html from matches.csv")
    parser.add_argument("--season", default=SEASON_CURRENT)
    args = parser.parse_args()
    print(write_report(args.season, load_played(args.season)))

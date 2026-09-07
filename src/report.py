"""Render the ranking as a single self-contained HTML page (docs/index.html).

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
    "#1b6ca8", "#9c2f4a", "#1c7a58", "#d1802a", "#6d4fa2", "#0f8b9e", "#b8477e",
    "#5f7a1f", "#7a5445", "#3550a0", "#c2562a", "#2e8f3f", "#556070", "#86722a",
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
    left, right, top, bottom = 40, 54, 16, 32
    span = max(len(matchdays) - 1, 1)
    y_min, y_max, y_step = y_axis(table)

    def x(i):
        return left + (w - left - right) * i / span

    def y(power):
        frac = (power - y_min) / (y_max - y_min)
        return h - bottom - (h - bottom - top) * frac

    parts = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Verlauf der Power Scores">']

    for tick in range(y_min, y_max + 1, y_step):
        # 50 is the league average - the one gridline worth reading against.
        mid = " mid" if tick == 50 else ""
        parts.append(
            f'<line class="grid{mid}" x1="{left}" y1="{y(tick):.1f}" x2="{w - right}" '
            f'y2="{y(tick):.1f}"/>'
            f'<text class="tick" x="{left - 8}" y="{y(tick) + 4:.1f}" '
            f'text-anchor="end">{tick}</text>'
        )
    if y_min <= 50 <= y_max:
        parts.append(f'<text class="avg" x="{w - right + 7}" y="{y(50) + 4:.1f}">Liga-Ø</text>')
    for i, md in enumerate(matchdays):
        if len(matchdays) <= 14 or md % 2 == 0 or i == len(matchdays) - 1:
            parts.append(
                f'<text class="tick" x="{x(i):.1f}" y="{h - 10}" '
                f'text-anchor="middle">{md}</text>'
            )
    for rank, t in enumerate(table):
        points = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(t["series"]))
        dots = "".join(
            f'<circle cx="{x(i):.1f}" cy="{y(v):.1f}" r="3"/>'
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

    # Bars read against the 50 midline; the widest gap in the league fills half
    # the track, so the column shows the shape of the field, not absolute Elo.
    spread = max(abs(t["power"] - 50) for t in table) or 1

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
            gap = '<span class="chip flat">±0</span>'
        else:
            cls = "up" if diff > 0 else "down"
            gap = f'<span class="chip {cls}">{"+" if diff > 0 else "−"}{abs(diff)}</span>'
        edge = t["power"] - 50
        bar = (
            f'<span class="bar"><i class="{"pos" if edge >= 0 else "neg"}" '
            f'style="width:{max(abs(edge) / spread * 50, 1.5):.1f}%"></i></span>'
        )
        body_rows.append(
            f'<tr data-rank="{rank}" tabindex="0" '
            f'style="--c:{PALETTE[rank % len(PALETTE)]};--i:{rank}">'
            f'<td class="rank">{rank + 1}</td>'
            f'<td class="team"><span class="sw"></span>{html.escape(t["team"])}</td>'
            f'<td class="power"><div class="pw"><b>{num(t["power"])}</b>{bar}</div></td>'
            f"<td>{delta}</td>"
            f"<td class=\"s-hide\">{t['matches']}</td>"
            f"<td>{w}-{d}-{l}</td>"
            f"<td class=\"s-hide\">{t['gf']}:{t['ga']}</td>"
            f"<td class=\"s-hide\">{t['gf'] - t['ga']:+d}</td>"
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
  :root {{
    --slate:#1b2a36; --ink:#16202a; --muted:#69747f;
    --paper:#edf0f3; --card:#fff; --line:#e3e7eb; --track:#e8ecef;
    --up:#1c7a58; --down:#9c2f4a; --grey:#d7dde2;
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--paper); color:var(--ink);
         font:16px/1.6 system-ui,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
         -webkit-font-smoothing:antialiased; }}
  .wrap {{ max-width:940px; margin:0 auto; padding:0 20px; }}
  .prose {{ max-width:64ch; }}
  p {{ margin:0 0 12px; }}

  .band {{ background:var(--slate); color:#fff; padding:30px 0 26px; }}
  .band .wrap {{ display:flex; align-items:flex-end; justify-content:space-between;
                 gap:24px; flex-wrap:wrap; }}
  .league {{ margin:0 0 6px; color:#94a6b4; font-size:14px; }}
  h1 {{ margin:0; font-size:clamp(30px,6vw,48px); font-weight:800;
        letter-spacing:-.03em; line-height:1; }}
  .md {{ text-align:right; line-height:1.2; }}
  .md .k {{ display:block; color:#94a6b4; font-size:13px; }}
  .md .n {{ display:block; font-size:44px; font-weight:700; letter-spacing:-.02em;
            font-variant-numeric:tabular-nums; }}
  .md .dt {{ display:block; color:#94a6b4; font-size:13px;
             font-variant-numeric:tabular-nums; }}

  main {{ padding:28px 0 56px; }}
  .lead {{ font-size:17px; line-height:1.65; margin:0 0 26px; }}
  h2 {{ font-size:19px; font-weight:700; letter-spacing:-.01em; margin:44px 0 12px; }}

  .card {{ background:var(--card); border:1px solid var(--line); border-radius:12px;
           padding:6px 16px; overflow-x:auto; }}
  table {{ width:100%; border-collapse:collapse; font-size:15px; }}
  th, td {{ padding:10px 7px; text-align:right; border-bottom:1px solid var(--line);
            font-variant-numeric:tabular-nums; }}
  tbody tr:last-child td {{ border-bottom:0; }}
  th {{ font-size:12.5px; color:var(--muted); font-weight:600; white-space:nowrap;
        padding-top:14px; padding-bottom:10px; }}
  th.l, td.team, td.power {{ text-align:left; }}
  td.rank {{ color:var(--muted); width:28px; font-size:14px; }}
  td.team {{ font-weight:500; white-space:nowrap; }}
  td.power {{ width:190px; }}
  .pw {{ display:flex; align-items:center; gap:12px; }}
  .pw b {{ font-size:16px; font-weight:700; min-width:40px; }}
  .bar {{ position:relative; flex:1; min-width:70px; height:8px;
          background:var(--track); border-radius:99px; }}
  .bar::before {{ content:""; position:absolute; left:50%; top:-3px; bottom:-3px;
                  width:1px; background:#c4ccd3; }}
  .bar i {{ position:absolute; top:0; bottom:0; border-radius:99px;
            animation:grow .45s cubic-bezier(.2,.8,.3,1) both;
            animation-delay:calc(var(--i) * 30ms); }}
  .bar i.pos {{ left:50%; background:var(--up); transform-origin:left; }}
  .bar i.neg {{ right:50%; background:var(--down); transform-origin:right; }}
  @keyframes grow {{ from {{ transform:scaleX(0); }} to {{ transform:scaleX(1); }} }}

  tbody tr {{ cursor:pointer; }}
  tbody tr:hover {{ background:#f4f7f9; }}
  tbody tr:focus-visible {{ outline:2px solid var(--slate); outline-offset:-2px; }}
  .sw {{ display:inline-block; width:10px; height:10px; border-radius:3px;
         border:1.5px solid var(--grey); margin-right:9px; vertical-align:-1px; }}
  tr.sel {{ background:#f4f7f9; background:color-mix(in srgb, var(--c) 7%, #fff); }}
  tr.sel .sw {{ background:var(--c); border-color:var(--c); }}
  tr.sel td.team {{ font-weight:650; }}
  tr.sel td.rank {{ box-shadow:inset 3px 0 0 var(--c); }}

  .up {{ color:var(--up); }} .down {{ color:var(--down); }} .flat {{ color:var(--muted); }}
  .chip {{ display:inline-block; margin-left:7px; padding:1px 7px; border-radius:99px;
           font-size:12px; font-weight:600; }}
  .chip.up {{ background:#e3f1ea; }} .chip.down {{ background:#f6e5ea; }}
  .chip.flat {{ background:#eef1f3; }}

  .legend {{ display:flex; flex-wrap:wrap; gap:8px; margin:0 0 10px; min-height:26px; }}
  .legend span {{ display:inline-flex; align-items:center; gap:7px; font-size:13px;
                  background:var(--card); border:1px solid var(--line);
                  border-radius:99px; padding:3px 11px 3px 9px; }}
  .legend i {{ width:8px; height:8px; border-radius:50%; }}

  svg {{ width:100%; height:auto; display:block; }}
  .grid {{ stroke:#eef1f4; stroke-width:1; }}
  .grid.mid {{ stroke:#bcc6cf; stroke-dasharray:4 4; }}
  .tick, .avg {{ fill:var(--muted); font-size:11px; }}
  .line polyline {{ fill:none; stroke:#dfe4e9; stroke-width:1.5; stroke-linejoin:round;
                    stroke-linecap:round; }}
  .line circle {{ display:none; }}
  .line.sel polyline {{ stroke:var(--c); stroke-width:2.6; }}
  .line.sel circle {{ display:inline; fill:#fff; stroke:var(--c); stroke-width:2; }}

  .hint {{ color:var(--muted); font-size:13.5px; line-height:1.55; margin:12px 0 0;
           max-width:64ch; }}
  .note {{ background:var(--card); border:1px solid var(--line);
           border-left:3px solid var(--down); border-radius:10px;
           padding:14px 18px; margin:16px 0 20px; max-width:64ch; font-size:15px; }}
  .note p {{ margin:0; }}
  footer {{ margin-top:44px; padding-top:16px; border-top:1px solid #dfe4e9;
            color:var(--muted); font-size:13px; max-width:64ch; }}
  a {{ color:inherit; text-underline-offset:2px; }}

  @media (max-width:700px) {{
    .s-hide {{ display:none; }}
    .band {{ padding:22px 0 20px; }}
    .md .n {{ font-size:34px; }}
    td.team {{ white-space:normal; }}
    th, td {{ padding:9px 5px; }}
  }}
  @media (prefers-reduced-motion:reduce) {{
    .bar i {{ animation:none; }}
  }}
</style>
</head>
<body>
<header class="band">
  <div class="wrap">
    <div>
      <p class="league">{html.escape(STAFFEL_NAME)} · Saison {season}</p>
      <h1>Power Ranking</h1>
    </div>
    <div class="md">
      <span class="k">Spieltag</span>
      <span class="n">{matchday}</span>
      <span class="dt">{last_date}</span>
    </div>
  </div>
</header>
<main class="wrap">
  <p class="lead prose">Der Power Score bewertet jedes Team danach, <strong>gegen wen</strong> es
  gespielt hat und <strong>wie deutlich</strong> die Ergebnisse ausfielen – nicht nur danach,
  wie viele Punkte am Ende dastehen. Ein Sieg gegen einen starken Gegner zählt mehr als einer
  gegen einen schwachen, und ein 4:0 mehr als ein 1:0. 50 ist Ligadurchschnitt: der Balken zeigt
  nach rechts, wenn ein Team darüber liegt, nach links, wenn darunter.</p>

  <div class="card">
    <table>
      <thead>
        <tr>
          <th>#</th><th class="l">Team</th><th class="l">Power</th><th>+/&minus;</th>
          <th class="s-hide">Sp</th><th>S-U-N</th><th class="s-hide">Tore</th>
          <th class="s-hide">Diff</th><th>Tabelle</th>
        </tr>
      </thead>
      <tbody>
        {chr(10).join("        " + r for r in body_rows).strip()}
      </tbody>
    </table>
  </div>
  <p class="hint"><strong>Tabelle</strong> ist der offizielle Tabellenplatz; der Wert dahinter
  ist die Differenz zum Platz in diesem Ranking. <span class="chip up">+2</span> heißt: hier zwei
  Plätze besser als in der Tabelle, das Team hat also für seine Punkte die stärkeren Gegner
  geschlagen oder deutlicher gewonnen. Die Spalte <strong>+/&minus;</strong> ist dagegen die
  Veränderung des Power Scores gegenüber dem letzten Spieltag.</p>

  <h2>Verlauf</h2>
  <div class="legend" id="legend"></div>
  <div class="card">{svg_chart(table, matchdays)}</div>
  <p class="hint">Zeile in der Tabelle anklicken, um ein Team im Diagramm hervorzuheben.
  X-Achse: Spieltag, Y-Achse: Power Score. Achtung beim Vergleich mit früheren Wochen:
  die Y-Achse passt sich dem aktuellen Wertebereich an.</p>

  <h2>Was die Zahl kann – und was nicht</h2>
  <div class="note"><p><strong>Dieses Ranking sagt Spiele nicht besser voraus als die
  Tabelle.</strong> Das wurde an der kompletten Vorsaison nachgerechnet: Vorhersagen aus dem
  Power Score sind statistisch genauso gut wie Vorhersagen aus dem Tabellenplatz, der
  Unterschied ist reines Rauschen. Das ist auch zu erwarten – bei 14 Teams spielt jeder gegen
  jeden zweimal, damit hat am Ende niemand einen leichteren Spielplan gehabt. Der Power Score
  ist ein <em>anderer Blick</em> auf dieselbe Saison, keine Glaskugel.</p></div>

  <div class="prose">
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
  </div>

  <footer>
    Datenquelle: <a href="{SOURCE_URL}">fussball.de</a> (DFB) – dort stehen die offizielle
    Tabelle und alle Ergebnisse. Diese Seite zeigt nur daraus berechnete Werte.
    Privates, nicht-kommerzielles Projekt · Stand der Berechnung: {generated}
  </footer>
</main>
<script>
  const rows = [...document.querySelectorAll('tbody tr')];
  const svg = document.querySelector('svg');
  const legend = document.getElementById('legend');
  const selected = new Set(['0', '1', '2']);

  function apply() {{
    legend.textContent = '';
    rows.forEach(tr => {{
      const on = selected.has(tr.dataset.rank);
      const line = document.getElementById('line' + tr.dataset.rank);
      tr.classList.toggle('sel', on);
      line.classList.toggle('sel', on);
      if (on) {{
        svg.appendChild(line);   // draw highlighted lines on top
        const chip = document.createElement('span');
        const dot = document.createElement('i');
        dot.style.background = tr.style.getPropertyValue('--c');
        chip.append(dot, tr.querySelector('.team').textContent);
        legend.appendChild(chip);
      }}
    }});
  }}

  function toggle(tr) {{
    const r = tr.dataset.rank;
    selected.has(r) ? selected.delete(r) : selected.add(r);
    apply();
  }}

  rows.forEach(tr => {{
    tr.addEventListener('click', () => toggle(tr));
    tr.addEventListener('keydown', e => {{
      if (e.key === 'Enter' || e.key === ' ') {{ e.preventDefault(); toggle(tr); }}
    }});
  }});

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

    parser = argparse.ArgumentParser(description="Rebuild docs/index.html from matches.csv")
    parser.add_argument("--season", default=SEASON_CURRENT)
    args = parser.parse_args()
    print(write_report(args.season, load_played(args.season)))

"""Render the ranking as a single self-contained HTML page (docs/index.html).

Only derived numbers are published - power scores, records, goal difference -
never the scraped match rows themselves; fussball.de is linked as the source.
"""

import base64
import csv
import html
import math
import os
from collections import defaultdict
from datetime import date

from config import (
    LOGO_DIR,
    MATCHES_CSV,
    POWER_SCALE_DIVISOR,
    SEASON_CURRENT,
    STAFFEL_NAME,
    team_slug,
)
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


def load_logos():
    """Slug -> data URI, so the page stays self-contained. A team without a file
    renders without a crest rather than breaking the row."""
    if not os.path.isdir(LOGO_DIR):
        return {}
    logos = {}
    for name in sorted(os.listdir(LOGO_DIR)):
        if name.endswith(".png"):
            with open(os.path.join(LOGO_DIR, name), "rb") as f:
                encoded = base64.b64encode(f.read()).decode("ascii")
            logos[name[:-4]] = f"data:image/png;base64,{encoded}"
    return logos


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


def last5_form(rows, n=5):
    """Each team's last n results, oldest to newest, as 'w'/'d'/'l' - the same
    chronological order the Elo replay uses, so the dots read left to right."""
    history = defaultdict(list)
    for r in sorted(rows, key=lambda r: (r["date"], int(r["matchday"]))):
        hg, ag = int(r["home_goals"]), int(r["away_goals"])
        h, a = r["home_team"], r["away_team"]
        if hg > ag:
            history[h].append("w")
            history[a].append("l")
        elif hg < ag:
            history[h].append("l")
            history[a].append("w")
        else:
            history[h].append("d")
            history[a].append("d")
    return {team: results[-n:] for team, results in history.items()}


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
    form = last5_form(rows)

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
                "form": form[team],
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


def hero_half_span(table):
    """Half-width of the pitch scale, snapped to the same 5-point grid, symmetric
    around the 50-point league average."""
    half = max(abs(t["power"] - 50) for t in table)
    return max(int(math.ceil(half / Y_GRID)) * Y_GRID, Y_GRID)


def lanes(xs, min_gap, n=5):
    """Beeswarm: push tokens that would overlap into a neighbouring lane."""
    last = [-1e9] * n
    out = [0] * len(xs)
    for i in sorted(range(len(xs)), key=lambda i: xs[i]):
        slot = next((k for k in range(n) if xs[i] - last[k] >= min_gap), None)
        if slot is None:
            slot = min(range(n), key=lambda k: last[k])
        out[i] = slot
        last[slot] = xs[i]
    return out


def svg_pitch(table):
    """The league as a formation on a landscape pitch: one numbered token per
    team along the long axis, right of the 50-point league average green, left
    wine.

    The number on the token is the rank, so a token can be traced straight into
    the table beside it. All labelling is HTML around the drawing - SVG text
    scaled down to a phone would shrink to a few pixels.
    """
    w, h = 860, 250
    pad, cy, r = 10, 125, 20
    half = hero_half_span(table)
    lo, hi = 50 - half, 50 + half
    inner_l, inner_r = pad + 66, w - pad - 66

    def x(p):
        return inner_l + (inner_r - inner_l) * (p - lo) / (hi - lo)

    cx = x(50)
    p = [
        f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Alle {len(table)} Teams nach Power '
        f'Score von {lo} links bis {hi} rechts; die Nummer im Kreis ist der Rang">'
    ]
    # Pitch markings - the frame the scale is read against, nothing else.
    p.append(
        f'<g class="pg">'
        f'<rect x="{pad}" y="{pad}" width="{w - 2 * pad}" height="{h - 2 * pad}" rx="10"/>'
        f'<rect x="{pad}" y="{cy - 62}" width="64" height="124"/>'
        f'<rect x="{w - pad - 64}" y="{cy - 62}" width="64" height="124"/>'
        f'<rect x="{pad}" y="{cy - 28}" width="22" height="56"/>'
        f'<rect x="{w - pad - 22}" y="{cy - 28}" width="22" height="56"/>'
        f'<circle cx="{cx:.1f}" cy="{cy}" r="44"/>'
        f"</g>"
    )
    p.append(
        f'<line class="axis" x1="{inner_l - 28}" y1="{cy}" x2="{inner_r + 28}" y2="{cy}"/>'
    )

    at = [x(t["power"]) for t in table]
    lane = lanes(at, 2 * r + 4)
    for i, t in enumerate(table):
        dy = (0, -44, 44, -88, 88)[lane[i]]
        cls = "pos" if t["power"] >= 50 else "neg"
        p.append(
            f'<g class="ptok" data-rank="{i}">'
            f'<circle class="{cls}" cx="{at[i]:.1f}" cy="{cy + dy}" r="{r}"/>'
            f'<text x="{at[i]:.1f}" y="{cy + dy + 8}">{i + 1}</text>'
            f"<title>{i + 1}. {html.escape(t['team'])} - {num(t['power'])}</title></g>"
        )
    p.append("</svg>")
    return "\n".join(p), lo, hi


def svg_spark(values, y_min, y_max):
    """Row-sized trajectory, drawn on the same scale as the big chart."""
    w, h, pad = 58, 20, 3
    span = max(len(values) - 1, 1)

    def x(i):
        return pad + (w - 2 * pad) * i / span

    def y(v):
        return (h - pad) - (h - 2 * pad) * (v - y_min) / (y_max - y_min)

    pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(values))
    base = ""
    if y_min <= 50 <= y_max:
        base = f'<line class="sb" x1="{pad}" y1="{y(50):.1f}" x2="{w - pad}" y2="{y(50):.1f}"/>'
    cls = "pos" if values[-1] >= 50 else "neg"
    return (
        f'<svg class="spark {cls}" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
        f'aria-hidden="true">{base}<polyline points="{pts}"/>'
        f'<circle cx="{x(span):.1f}" cy="{y(values[-1]):.1f}" r="2.4"/></svg>'
    )


def svg_chart(table, matchdays):
    """Inline SVG, one polyline per team, each ending in its rank token so a
    highlighted line can be named without looking anywhere else."""
    w, h = 900, 400
    left, right, top, bottom = 44, 34, 18, 36
    span = max(len(matchdays) - 1, 1)
    y_min, y_max, y_step = y_axis(table)

    def x(i):
        return left + (w - left - right) * i / span

    def y(power):
        frac = (power - y_min) / (y_max - y_min)
        return h - bottom - (h - bottom - top) * frac

    parts = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Verlauf der Power Scores">']

    # Same reading direction as everywhere else: the half above 50 is green.
    if y_min <= 50 <= y_max:
        parts.append(
            f'<rect class="zone pos" x="{left}" y="{top}" width="{w - left - right}" '
            f'height="{y(50) - top:.1f}"/>'
            f'<rect class="zone neg" x="{left}" y="{y(50):.1f}" width="{w - left - right}" '
            f'height="{h - bottom - y(50):.1f}"/>'
        )

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
                f'<text class="tick" x="{x(i):.1f}" y="{h - 13}" '
                f'text-anchor="middle">{md}</text>'
            )
    for rank, t in enumerate(table):
        points = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(t["series"]))
        dots = "".join(
            f'<circle cx="{x(i):.1f}" cy="{y(v):.1f}" r="3.2"/>'
            for i, v in enumerate(t["series"])
        )
        ex, ey = x(len(t["series"]) - 1), y(t["series"][-1])
        cls = "pos" if t["power"] >= 50 else "neg"
        end = (
            f'<g class="end"><circle class="{cls}" cx="{ex:.1f}" cy="{ey:.1f}" r="13"/>'
            f'<text x="{ex:.1f}" y="{ey + 4.6:.1f}">{rank + 1}</text></g>'
        )
        parts.append(
            f'<g class="line" id="line{rank}" style="--c:{PALETTE[rank % len(PALETTE)]}">'
            f'<polyline points="{points}"/>{dots}{end}</g>'
        )

    parts.append("</svg>")
    return "\n".join(parts)


def render(season, rows):
    table, matchdays = build_table(rows)
    matchday = matchdays[-1]
    last_date = max(r["date"] for r in rows)
    last_date = ".".join(reversed(last_date.split("-")))
    generated = date.today().strftime("%d.%m.%Y")
    y_min, y_max, _ = y_axis(table)

    top_team, bottom_team = table[0], table[-1]
    range_pts = top_team["power"] - bottom_team["power"]
    # The team whose power rank sits furthest from its official table position.
    out_rank, out = max(enumerate(table), key=lambda p: abs(p[1]["position"] - (p[0] + 1)))
    out_diff = out["position"] - (out_rank + 1)
    plural = "Platz" if abs(out_diff) == 1 else "Plätze"
    if out_diff > 0:
        out_note = f"{abs(out_diff)} {plural} besser als Tabellenplatz {out['position']}"
    elif out_diff < 0:
        out_note = f"{abs(out_diff)} {plural} schlechter als Tabellenplatz {out['position']}"
    else:
        out_note = "Ranking und Tabelle sind überall deckungsgleich"
    out_cls = "up" if out_diff > 0 else ("down" if out_diff < 0 else "flat")

    pitch_svg, pitch_lo, pitch_hi = svg_pitch(table)
    logos = load_logos()

    body_rows = []
    for rank, t in enumerate(table):
        w, d, l = t["record"]
        side = "pos" if t["power"] >= 50 else "neg"
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
        segs = "".join(f'<i class="{r}"></i>' for r in t["form"])
        tok = f'<span class="tok {side}">{rank + 1}</span>'
        crest = logos.get(team_slug(t["team"]))
        crest = f'<img class="lg" src="{crest}" alt="">' if crest else ""

        body_rows.append(
            f'<tr data-rank="{rank}" tabindex="0" '
            f'style="--c:{PALETTE[rank % len(PALETTE)]};--i:{rank}">'
            f'<td class="rank">{tok}</td>'
            f'<td class="team"><div class="tc">{crest}<div>'
            f'<span class="tn">{html.escape(t["team"])}</span>'
            f'<span class="form"><span class="seg" aria-hidden="true">{segs}</span>'
            f'<span class="rt">{w}-{d}-{l}</span></span></div></div></td>'
            f'<td class="power"><div class="pw"><b>{num(t["power"])}</b></div></td>'
            f'<td class="s-hide spk">{svg_spark(t["series"], y_min, y_max)}</td>'
            f"<td>{delta}</td>"
            f"<td class=\"s-hide\">{t['matches']}</td>"
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
    --slate:#1b2a36; --slate-2:#22343f; --ink:#16202a; --muted:#5c6874;
    --paper:#edf0f3; --card:#fff; --line:#e3e7eb; --track:#e8ecef;
    --up:#1c7a58; --down:#9c2f4a; --grey:#d7dde2;
    /* Same semantics, lifted for legibility on the slate ground. */
    --up-l:#57c79b; --down-l:#e8788f; --on-slate:#a9bccb;
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--paper); color:var(--ink);
         font:16px/1.6 system-ui,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
         -webkit-font-smoothing:antialiased; }}
  .wrap {{ max-width:1760px; margin:0 auto; padding:0 clamp(16px,2.4vw,36px); }}
  .prose {{ max-width:64ch; }}
  p {{ margin:0 0 12px; }}
  a {{ color:inherit; text-underline-offset:2px; }}
  a:focus-visible {{ outline:2px solid currentColor; outline-offset:3px; border-radius:2px; }}

  /* ---- The token: one numbered disc, coloured by side of the 50 average ---- */
  .tok {{ display:inline-flex; align-items:center; justify-content:center;
          width:26px; height:26px; border-radius:50%; flex:0 0 auto;
          font-size:13px; font-weight:700; color:#fff;
          font-variant-numeric:tabular-nums; }}
  .tok.pos {{ background:var(--up); }}
  .tok.neg {{ background:var(--down); }}

  /* ---- Hero: one compact strip so the dashboard below stays in view ------- */
  .band {{ background:var(--slate); color:#fff; padding:24px 0 22px;
           background-image:repeating-linear-gradient(90deg,
             rgba(255,255,255,.022) 0 46px, rgba(255,255,255,0) 46px 92px); }}
  .top {{ display:flex; flex-wrap:wrap; align-items:flex-end; gap:18px 30px; }}
  .brand {{ flex:1 1 300px; }}
  .league {{ margin:0 0 6px; color:var(--on-slate); font-size:14px; }}
  h1 {{ margin:0; font-size:clamp(28px,4vw,44px); font-weight:800;
        letter-spacing:-.03em; line-height:1; }}
  .tag {{ margin:9px 0 0; color:var(--on-slate); font-size:14.5px; max-width:46ch; }}
  .md {{ flex:0 0 auto; text-align:right; line-height:1.2; }}
  .md .k {{ display:block; color:var(--on-slate); font-size:13px; }}
  .md .n {{ display:block; font-size:56px; font-weight:700; letter-spacing:-.02em;
            font-variant-numeric:tabular-nums; }}
  .md .dt {{ display:block; color:var(--on-slate); font-size:13px;
             font-variant-numeric:tabular-nums; }}
  .cells {{ flex:1 1 100%; display:grid; grid-template-columns:repeat(2,1fr); gap:12px; }}
  .cell {{ background:var(--slate-2); border:1px solid rgba(255,255,255,.09);
           border-radius:10px; padding:10px 13px; }}
  .cell .k {{ display:block; color:var(--on-slate); font-size:11.5px;
              letter-spacing:.06em; text-transform:uppercase; }}
  .cell .v {{ display:block; font-size:18px; font-weight:700; letter-spacing:-.01em;
              margin-top:3px; line-height:1.25; }}
  .cell .v.sm {{ font-size:15px; }}
  .cell .s {{ display:block; color:var(--on-slate); font-size:12.5px; margin-top:2px; }}
  .cell .v.up {{ color:var(--up-l); }} .cell .v.down {{ color:var(--down-l); }}

  /* ---- Body -------------------------------------------------------------- */
  main {{ padding:40px 0 56px; }}
  h2 {{ font-size:19px; font-weight:700; letter-spacing:-.01em; margin:0 0 10px;
        padding-top:12px; position:relative; }}
  h2::before {{ content:""; position:absolute; top:0; left:0; width:26px; height:3px;
                background:var(--slate); border-radius:2px; }}
  .sub {{ color:var(--muted); font-size:13.5px; margin:-2px 0 12px; max-width:64ch; }}

  /* The dashboard: table on the left, pitch and chart on the right, so that
     clicking a team is visible in all three at once. */
  .dash {{ display:grid; grid-template-columns:1fr; gap:26px; align-items:start; }}
  .col section + section {{ margin-top:22px; }}

  .card {{ background:var(--card); border:1px solid var(--line); border-radius:12px;
           padding:0; overflow-x:auto; }}
  .card.rank {{ background-image:repeating-linear-gradient(90deg,
                 rgba(27,42,54,.017) 0 64px, rgba(27,42,54,0) 64px 128px); }}
  table {{ width:100%; border-collapse:collapse; font-size:15px; }}
  th, td {{ padding:9px 7px; text-align:right; border-bottom:1px solid var(--line);
            font-variant-numeric:tabular-nums; }}
  th:first-child, td:first-child {{ padding-left:14px; }}
  th:last-child, td:last-child {{ padding-right:14px; }}
  tbody tr:last-child td {{ border-bottom:0; }}
  thead th {{ background:var(--slate); color:#dbe6ee; border-bottom:0;
              font-size:11.5px; font-weight:600; letter-spacing:.06em;
              text-transform:uppercase; white-space:nowrap; padding-top:10px;
              padding-bottom:10px; }}
  th.l, td.team, td.power {{ text-align:left; }}
  td.rank {{ width:48px; }}
  td.team {{ line-height:1.25; min-width:150px; }}
  .tc {{ display:flex; align-items:center; gap:9px; }}
  .lg {{ width:26px; height:26px; object-fit:contain; flex:0 0 auto; }}
  .tn {{ display:block; font-weight:600; }}
  .form {{ display:flex; align-items:center; gap:7px; margin-top:3px; }}
  .pw b {{ font-size:17px; font-weight:700; }}

  td.spk {{ width:64px; }}
  .spark {{ display:block; width:58px; height:20px; }}
  .spark .sb {{ stroke:#ccd4da; stroke-width:1; stroke-dasharray:2 3; }}
  .spark polyline {{ fill:none; stroke-width:1.8; stroke-linejoin:round;
                     stroke-linecap:round; }}
  .spark.pos polyline {{ stroke:var(--up); }} .spark.pos circle {{ fill:var(--up); }}
  .spark.neg polyline {{ stroke:var(--down); }} .spark.neg circle {{ fill:var(--down); }}

  .seg {{ display:flex; gap:4px; flex:0 0 auto; align-items:center; }}
  .seg i {{ width:7px; height:7px; border-radius:50%; flex:0 0 auto; }}
  .seg i.w {{ background:var(--up); }}
  .seg i.d {{ background:#aeb9c2; }}
  .seg i.l {{ background:var(--down); }}
  .rt {{ color:var(--muted); font-size:12.5px; }}

  tbody tr[data-rank] {{ cursor:pointer; }}
  tbody tr[data-rank]:hover {{ background:#f4f7f9; }}
  tbody tr:focus-visible {{ outline:2px solid var(--slate); outline-offset:-2px; }}
  tr.sel {{ background:#f4f7f9; background:color-mix(in srgb, var(--c) 7%, #fff); }}
  tr.sel td:first-child {{ box-shadow:inset 4px 0 0 var(--c); }}
  tr.sel .tok {{ box-shadow:0 0 0 2px #fff, 0 0 0 4px var(--c); }}

  .up {{ color:var(--up); }} .down {{ color:var(--down); }} .flat {{ color:var(--muted); }}
  .chip {{ display:inline-block; margin-left:7px; padding:1px 7px; border-radius:99px;
           font-size:12px; font-weight:600; }}
  .chip.up {{ background:#dcefe6; }} .chip.down {{ background:#f6e5ea; }}
  .chip.flat {{ background:#eef1f3; }}

  /* ---- The pitch panel ---------------------------------------------------- */
  .pitchcol {{ background:var(--slate); border-radius:12px; padding:12px 14px 11px;
               background-image:repeating-linear-gradient(90deg,
                 rgba(255,255,255,.022) 0 40px, rgba(255,255,255,0) 40px 80px); }}
  .pwrap {{ position:relative; }}
  .pwrap svg {{ width:100%; height:auto; display:block; }}
  .pg rect, .pg circle {{ fill:none; stroke:rgba(255,255,255,.15); stroke-width:1.4; }}
  .axis {{ stroke:rgba(255,255,255,.17); stroke-width:1; stroke-dasharray:3 4; }}
  .ptok {{ cursor:pointer; }}
  .ptok circle {{ stroke:var(--slate); stroke-width:2.5; }}
  .ptok circle.pos {{ fill:var(--up-l); }}
  .ptok circle.neg {{ fill:var(--down-l); }}
  .ptok text {{ fill:var(--slate); font-size:23px; font-weight:700; text-anchor:middle; }}
  .ptok.sel circle {{ stroke:#fff; stroke-width:3.5; }}
  .pends {{ display:flex; justify-content:space-between; gap:12px; margin:0 0 6px; }}
  .pend {{ display:flex; align-items:center; gap:7px; margin:0;
           color:var(--on-slate); font-size:12px; }}
  .pend i {{ width:9px; height:9px; border-radius:50%; flex:0 0 auto; }}
  .pend i.pos {{ background:var(--up-l); }} .pend i.neg {{ background:var(--down-l); }}
  .pend b {{ color:#fff; font-weight:700; font-variant-numeric:tabular-nums; }}
  .phead {{ margin:10px 0 0; padding-top:9px; color:var(--on-slate); font-size:12px;
            line-height:1.5; border-top:1px solid rgba(255,255,255,.1); }}

  /* ---- Chart -------------------------------------------------------------- */
  .card.chart {{ padding:10px 12px; }}
  .chart svg {{ width:100%; height:auto; display:block; }}
  .zone.pos {{ fill:var(--up); opacity:.05; }}
  .zone.neg {{ fill:var(--down); opacity:.05; }}
  .grid {{ stroke:#eef1f4; stroke-width:1; }}
  .tick {{ fill:var(--muted); font-size:11px; }}
  .line polyline {{ fill:none; stroke:#e0e5ea; stroke-width:1.5; stroke-linejoin:round;
                    stroke-linecap:round; }}
  .line circle {{ display:none; }}
  .line .end {{ display:none; }}
  .line.sel polyline {{ stroke:var(--c); stroke-width:2.8; }}
  .line.sel circle {{ display:inline; fill:#fff; stroke:var(--c); stroke-width:2; }}
  .line.sel .end {{ display:inline; }}
  .end circle {{ display:inline; stroke:#fff; stroke-width:2.5; }}
  .end circle.pos {{ fill:var(--up); }}
  .end circle.neg {{ fill:var(--down); }}
  .end text {{ fill:#fff; font-size:13px; font-weight:700; text-anchor:middle; }}

  .scale {{ margin:20px 0 22px; max-width:64ch; }}
  .scale .track {{ position:relative; height:12px; border-radius:99px;
                   background:linear-gradient(90deg,#f2e6ea,#eef1f3,#e2efe8); }}
  .scale .occ {{ position:absolute; top:-3px; height:18px; border-radius:99px;
                 background:var(--slate); }}
  .scale .mid {{ position:absolute; left:50%; top:-6px; bottom:-6px; width:1px;
                 background:#98a4ae; }}
  .scale .ends {{ display:flex; justify-content:space-between; margin-top:8px;
                  color:var(--muted); font-size:12.5px; }}
  .scale .ends b {{ color:var(--ink); font-weight:600; }}

  .hint {{ color:var(--muted); font-size:13.5px; line-height:1.55; margin:12px 0 0;
           max-width:64ch; }}
  .note {{ background:var(--card); border:1px solid var(--line);
           border-left:3px solid var(--down); border-radius:10px;
           padding:14px 18px; margin:16px 0 20px; max-width:64ch; font-size:15px; }}
  .note p {{ margin:0; }}
  .below {{ margin-top:40px; }}
  footer {{ margin-top:44px; padding-top:16px; border-top:1px solid #dfe4e9;
            color:var(--muted); font-size:13px; max-width:64ch; }}

  /* Two columns as soon as the table fits next to the pitch without scrolling. */
  @media (min-width:1400px) {{
    .dash {{ grid-template-columns:minmax(0,1.12fr) minmax(0,1fr); gap:28px; }}
    .cells {{ flex:2 1 640px; }}
  }}
  @media (max-width:1399px) {{
    /* Stacked: keep one comfortable measure instead of stretching to 1760px. */
    .dash {{ max-width:1080px; margin:0 auto; }}
  }}
  @media (max-width:700px) {{
    .s-hide {{ display:none; }}
    .band {{ padding:20px 0 20px; }}
    .md .n {{ font-size:44px; }}
    .cells {{ grid-template-columns:1fr; gap:8px; }}
    .cell .v {{ font-size:17px; }}
    th, td {{ padding:9px 5px; }}
    th:first-child, td:first-child {{ padding-left:10px; }}
    th:last-child, td:last-child {{ padding-right:10px; }}
    td.rank {{ width:38px; }}
    td.team {{ min-width:0; }}
    .tc {{ gap:7px; }}
    .lg {{ width:22px; height:22px; }}
    .tok {{ width:23px; height:23px; font-size:12px; }}
    /* The chart scales with the viewport, so its labels need bigger user units. */
    .tick {{ font-size:19px; }}
  }}
</style>
</head>
<body>
<header class="band">
  <div class="wrap">
    <div class="top">
      <div class="brand">
        <p class="league">{html.escape(STAFFEL_NAME)} · Saison {season}</p>
        <h1>Power Ranking</h1>
        <p class="tag">Wie stark ein Team wirklich ist – gemessen daran, gegen wen es
        gespielt hat und wie deutlich.</p>
      </div>
      <div class="md">
        <span class="k">Spieltag</span>
        <span class="n">{matchday}</span>
        <span class="dt">{last_date}</span>
      </div>
      <div class="cells">
        <div class="cell">
          <span class="k">Spitzenreiter</span>
          <span class="v sm">{html.escape(top_team["team"])}</span>
          <span class="s">Power {num(top_team["power"])} · Tabellenplatz
          {top_team["position"]}</span>
        </div>
        <div class="cell">
          <span class="k">Größter Unterschied zur Tabelle</span>
          <span class="v sm {out_cls}">{html.escape(out["team"])}</span>
          <span class="s">{out_note}</span>
        </div>
      </div>
    </div>
  </div>
</header>
<main class="wrap">
  <div class="dash">
    <div class="col">
      <section>
        <h2>Rangliste</h2>
        <div class="card rank">
          <table>
            <thead>
              <tr>
                <th>#</th><th class="l">Team · S-U-N</th><th class="l">Power</th>
                <th class="s-hide">Verlauf</th><th>+/&minus;</th>
                <th class="s-hide">Sp</th><th class="s-hide">Tore</th>
                <th class="s-hide">Diff</th><th>Tabelle</th>
              </tr>
            </thead>
            <tbody>
              {chr(10).join("              " + r for r in body_rows).strip()}
            </tbody>
          </table>
        </div>
        <p class="hint"><strong>Tabelle</strong> ist der offizielle Tabellenplatz; der Wert
        dahinter ist die Differenz zum Platz in diesem Ranking. <span class="chip up">+2</span>
        heißt: hier zwei Plätze besser als in der Tabelle, das Team hat also für seine Punkte
        die stärkeren Gegner geschlagen oder deutlicher gewonnen. <strong>+/&minus;</strong> ist
        dagegen die Veränderung gegenüber dem letzten Spieltag. Die Punkte unter dem Teamnamen
        sind die letzten fünf Spiele, chronologisch von links nach rechts: grün Sieg, grau
        Unentschieden, rot Niederlage.</p>
      </section>
    </div>

    <div class="col">
      <section>
        <h2>Aufstellung</h2>
        <p class="sub">Ein Trikot je Team, aufgestellt nach Power Score.
        <strong>Die Nummer ist der Platz in der Rangliste.</strong></p>
        <div class="pitchcol">
          <div class="pends">
            <p class="pend"><i class="neg"></i>schwächer<b>{pitch_lo}</b></p>
            <p class="pend"><b>{pitch_hi}</b>stärker<i class="pos"></i></p>
          </div>
          <div class="pwrap">{pitch_svg}</div>
          <p class="phead">Links liegen die Teams unter dem Ligadurchschnitt, rechts davon die
          darüber. Ein Trikot antippen hebt das Team überall hervor.</p>
        </div>
      </section>

      <section>
        <h2>Verlauf</h2>
        <p class="sub">Power Score über alle bisherigen Spieltage. Die Nummer am Ende einer
        Linie ist wieder der Rang.</p>
        <div class="card chart">{svg_chart(table, matchdays)}</div>
        <p class="hint">X-Achse: Spieltag, Y-Achse: Power Score. Achtung beim Vergleich mit
        früheren Wochen: die Y-Achse passt sich dem aktuellen Wertebereich an.</p>
      </section>
    </div>
  </div>

  <div class="below">
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
  </div>

  <figure class="scale">
    <div class="track"><span class="occ" style="left:{bottom_team["power"]:.1f}%; width:{max(range_pts, 0.8):.1f}%"></span><span class="mid"></span></div>
    <div class="ends">
      <span>0</span>
      <span>Die ganze Liga: <b>{num(bottom_team["power"])} – {num(top_team["power"])}</b></span>
      <span>100</span>
    </div>
  </figure>

  <div class="prose">
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
  </div>
</main>
<script>
  const rows = [...document.querySelectorAll('tbody tr[data-rank]')];
  const tokens = [...document.querySelectorAll('.ptok')];
  const svg = document.querySelector('.chart svg');
  const selected = new Set(['0', '1', '2']);

  function apply() {{
    rows.forEach(tr => {{
      const on = selected.has(tr.dataset.rank);
      const line = document.getElementById('line' + tr.dataset.rank);
      tr.classList.toggle('sel', on);
      line.classList.toggle('sel', on);
      if (on) svg.appendChild(line);   // draw highlighted lines on top
    }});
    tokens.forEach(g => g.classList.toggle('sel', selected.has(g.dataset.rank)));
  }}

  function toggle(rank) {{
    selected.has(rank) ? selected.delete(rank) : selected.add(rank);
    apply();
  }}

  rows.forEach(tr => {{
    tr.addEventListener('click', () => toggle(tr.dataset.rank));
    tr.addEventListener('keydown', e => {{
      if (e.key === 'Enter' || e.key === ' ') {{ e.preventDefault(); toggle(tr.dataset.rank); }}
    }});
  }});
  tokens.forEach(g => g.addEventListener('click', () => toggle(g.dataset.rank)));

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

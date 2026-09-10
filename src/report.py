"""Render the ranking as a single self-contained HTML page (docs/index.html).

Only derived numbers are published - power scores, records, goal difference -
never the scraped match rows themselves; fussball.de is linked as the source.
"""

import base64
import html
import math
import os
from collections import defaultdict
from datetime import date

from config import (
    FONT_DIR,
    FORM_WINDOW,
    LOGO_DIR,
    POWER_SCALE_DIVISOR,
    R0,
    SEASON_CURRENT,
    SEASON_PREVIOUS,
    STAFFEL_NAME,
    team_slug,
)
from explore_predictors import SKIP_MATCHDAYS, compare
from predict import forecast, load, next_matchday, track_record
from rating import EloRating
from score import clamp, normalize_to_power_score

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


def font_face(family, filename, weights):
    """One @font-face with the woff2 inlined, so the page makes no external
    request. Both files are the variable Latin cut Google Fonts serves, which
    covers German umlauts, the en dash and the minus sign the page uses."""
    path = os.path.join(FONT_DIR, filename)
    if not os.path.isfile(path):
        return ""
    with open(path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("ascii")
    return (
        "@font-face{font-family:'%s';font-style:normal;font-weight:%s;"
        "font-display:swap;src:url(data:font/woff2;base64,%s) format('woff2');}"
        % (family, weights, encoded)
    )


def stroke(path):
    """One highlighter stroke as a background image: a wobbling outline filled
    with a gradient, so both the shape and the pressure are uneven. Stretched to
    whatever box it is given - a blob has no aspect ratio to preserve."""
    return (
        "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' "
        "viewBox='0 0 200 40' preserveAspectRatio='none'%3E%3Cdefs%3E"
        "%3ClinearGradient id='g' x1='0' x2='1'%3E"
        "%3Cstop offset='0' stop-color='%23face3a' stop-opacity='.07'/%3E"
        "%3Cstop offset='.05' stop-color='%23face3a' stop-opacity='.44'/%3E"
        "%3Cstop offset='.34' stop-color='%23face3a' stop-opacity='.25'/%3E"
        "%3Cstop offset='.61' stop-color='%23face3a' stop-opacity='.42'/%3E"
        "%3Cstop offset='.87' stop-color='%23face3a' stop-opacity='.22'/%3E"
        "%3Cstop offset='1' stop-color='%23face3a' stop-opacity='0'/%3E"
        "%3C/linearGradient%3E%3C/defs%3E"
        f"%3Cpath d='{path}' fill='url(%23g)'/%3E%3C/svg%3E\")"
    )


STROKE_A = stroke("M1,9 C26,2 58,13 92,6 C128,0 163,10 199,3 "
                  "L198,31 C170,38 132,27 96,34 C62,40 27,30 2,37 Z")
STROKE_B = stroke("M2,13 C34,5 66,17 100,10 C134,3 168,13 198,5 "
                  "L197,29 C166,36 130,25 98,32 C64,38 30,28 3,36 Z")

# The pen stroke that ties a written note to the number it is about: a curve
# and a two-line head, drawn open so it reads as ink rather than as an icon.
ARROW = (
    '<svg class="arw" viewBox="0 0 48 22" aria-hidden="true">'
    '<path d="M1.5,16.5 C9,18.5 22,16.5 32,9"/>'
    '<path d="M25.5,5.5 L33.5,7.8 L29.5,14.5"/>'
    "</svg>"
)

WEEKDAYS = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")


def short_date(iso):
    d = date.fromisoformat(iso)
    return f"{WEEKDAYS[d.weekday()]} {d.day:02d}.{d.month:02d}."


def signed(n):
    """Typographic minus, so the goal difference matches the form change column
    instead of setting a hyphen next to it."""
    return f"+{n}" if n >= 0 else f"&minus;{abs(n)}"


def pct(x):
    return f"{round(x * 100)}&nbsp;%"


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


def to_power(rating):
    """Elo points to the 0-100 scale, without the small-sample shrinkage.

    The season column keeps the shrinkage (normalize_to_power_score); the form
    column cannot. With N0 = 20 a five-match window would keep a fifth of its
    deviation and the whole league would sit back on 50 - the column would show
    nothing. Same divisor, so the two numbers stay on one scale and a row can be
    read across.
    """
    return clamp(50 + (rating - R0) / POWER_SCALE_DIVISOR, 0, 100)


def form_series(rows, matchdays, window=FORM_WINDOW):
    """Elo over the last `window` matchdays only, recomputed for every matchday.

    This is the page's headline number, and it is a *description* of a stretch of
    football, not an estimate of strength. It answers the question the official
    table cannot: this team is twelfth, but how are they playing right now?

    A hard window, not a decay. Weighting old matches down instead - Elo with a
    higher K, or a drifting state-space model - was tried and cannot go this
    short: reweighting keeps every match in the estimate forever, so the
    effective memory stalls around nine matchdays and the values run off the
    0-100 scale before it gets shorter. Restarting from 1500 each matchday drops
    the old matches outright, which is the only way to get a five-match view.

    Elo rather than plain points because it matters *who* the five were against:
    two sides can both take twelve of fifteen and belong in different places.
    """
    teams = sorted({r[side] for r in rows for side in ("home_team", "away_team")})
    ordered = sorted(rows, key=lambda r: (r["date"], int(r["matchday"])))

    series = {team: [] for team in teams}
    for md in matchdays:
        elo = EloRating()
        elo.initialize_teams(teams)
        for r in ordered:
            if md - window < int(r["matchday"]) <= md:
                elo.update_from_match(
                    r["home_team"], r["away_team"],
                    int(r["home_goals"]), int(r["away_goals"]), int(r["matchday"]),
                )
        ratings = elo.get_ratings()
        for team in teams:
            series[team].append(to_power(ratings[team]))
    return series


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
    """Ranked by current form, with the season power score alongside it.

    Form leads because it is the one question the official table cannot answer -
    a reader already knows who has collected the points. Power stays in the row
    so the two readings sit side by side instead of competing for the headline.
    """
    ratings, played, matchdays, series = replay(rows)
    forms = form_series(rows, matchdays)
    stats = team_stats(rows)
    positions = official_positions(stats)
    dots = last5_form(rows)

    table = []
    for team, values in series.items():
        f = forms[team]
        s = stats[team]
        table.append(
            {
                "team": team,
                "form": f[-1],
                "delta": None if len(f) < 2 else f[-1] - f[-2],
                "power": values[-1],
                "matches": played[team],
                "record": (s["w"], s["d"], s["l"]),
                "dots": dots[team],
                "gf": s["gf"],
                "ga": s["ga"],
                "position": positions[team],
                "series": values,
                "fseries": f,
            }
        )
    table.sort(key=lambda t: t["form"], reverse=True)
    return table, matchdays


def y_axis(table, key="series"):
    """Snapped, minimum-width range covering every plotted value."""
    values = [v for t in table for v in t[key]]
    lo = int(math.floor((min(values) - 1) / Y_GRID)) * Y_GRID
    hi = int(math.ceil((max(values) + 1) / Y_GRID)) * Y_GRID
    while hi - lo < Y_MIN_SPAN:
        lo -= Y_GRID
        if hi - lo < Y_MIN_SPAN:
            hi += Y_GRID
    step = Y_GRID if hi - lo <= 30 else 2 * Y_GRID
    return lo, hi, step


# The jersey outline, drawn once around its own centre so placing a token is a
# translate: shoulders at -18, hem at 19, sleeves out to +-21. The crest sits on
# a white patch on the chest, the rank badge on the lower right of the hem.
JERSEY = (
    "M-7,-18 L-13,-18 L-21,-10 L-15.5,-1 L-12,-5.5 L-12.5,19 "
    "L12.5,19 L12,-5.5 L15.5,-1 L21,-10 L13,-18 L7,-18 "
    "C6.5,-13 -6.5,-13 -7,-18 Z"
)
# The jersey is drawn at TOKEN_SCALE, so sleeve to sleeve is 42 * scale; the gap
# leaves a little air before a token is pushed a lane up.
TOKEN_SCALE = 1.25
TOKEN_GAP = 62
# Vertical offset per beeswarm lane, middle outwards.
LANE_OFFSETS = (0, -52, 52, -104, 104)


def hero_half_span(table):
    """Half-width of the pitch scale, snapped to the same 5-point grid, symmetric
    around the 50-point league average."""
    half = max(abs(t["form"] - 50) for t in table)
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


def svg_pitch(table, logos):
    """The league as a formation on a landscape pitch: one token per team along
    the long axis, right of the 50-point league average green, left wine.

    Every token is a jersey carrying the club crest, with the rank in a corner
    badge so it can be traced straight into the table beside it; a team without
    a crest wears the rank as its shirt number instead. All labelling is HTML
    around the drawing - SVG text scaled down to a phone would shrink to a few
    pixels.
    """
    w, pad = 1600, 12
    half = hero_half_span(table)
    lo, hi = 50 - half, 50 + half
    inner_l, inner_r = pad + 120, w - pad - 120

    def x(p):
        return inner_l + (inner_r - inner_l) * (p - lo) / (hi - lo)

    at = [x(t["form"]) for t in table]
    lane = lanes(at, TOKEN_GAP)
    # The pitch is only as deep as the beeswarm actually got. Drawn at a fixed
    # depth it left a third of the grass empty above and below the tokens, which
    # reads as missing teams rather than as a tightly packed league.
    used = max(abs(LANE_OFFSETS[k]) for k in lane)
    h = 2 * (used + 78)
    cy = h // 2
    box_h = min(192, h - 2 * pad - 16)
    goal_h = box_h * 0.46

    cx = x(50)
    p = [
        f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Alle {len(table)} Teams nach Form '
        f'von {lo} links bis {hi} rechts; die Nummer am Trikot ist der Rang">'
    ]
    # Pitch markings - the frame the scale is read against, nothing else.
    p.append(
        f'<g class="pg">'
        f'<rect x="{pad}" y="{pad}" width="{w - 2 * pad}" height="{h - 2 * pad}" rx="3"/>'
        f'<rect x="{pad}" y="{cy - box_h / 2:.1f}" width="104" height="{box_h}"/>'
        f'<rect x="{w - pad - 104}" y="{cy - box_h / 2:.1f}" width="104" height="{box_h}"/>'
        f'<rect x="{pad}" y="{cy - goal_h / 2:.1f}" width="36" height="{goal_h:.1f}"/>'
        f'<rect x="{w - pad - 36}" y="{cy - goal_h / 2:.1f}" width="36" height="{goal_h:.1f}"/>'
        f'<circle cx="{cx:.1f}" cy="{cy}" r="{min(58, box_h / 2.6):.1f}"/>'
        f"</g>"
    )
    # The halfway line is the league average, and it is the one reference the
    # whole drawing is read against - drawn solid, not implied by the circle.
    p.append(
        f'<line class="half" x1="{cx:.1f}" y1="{pad}" x2="{cx:.1f}" y2="{h - pad}"/>'
        f'<line class="axis" x1="{inner_l - 40}" y1="{cy}" x2="{inner_r + 40}" y2="{cy}"/>'
    )
    # A chalk mark every five points, so a jersey's distance from the middle can
    # be counted off instead of only compared. Labelled in HTML at the ends -
    # SVG text scaled to a phone would be a few pixels tall.
    for v in range(lo, hi + 1, Y_GRID):
        if v != 50:
            p.append(f'<line class="stick" x1="{x(v):.1f}" y1="{cy - 7}" '
                     f'x2="{x(v):.1f}" y2="{cy + 7}"/>')

    for i, t in enumerate(table):
        dy = LANE_OFFSETS[lane[i]]
        cls = "pos" if t["form"] >= 50 else "neg"
        crest = logos.get(team_slug(t["team"]))
        if crest:
            face = (
                f'<circle class="in" cy="6.5" r="11.5"/>'
                f'<image href="{crest}" x="-10" y="-3.5" width="20" height="20" '
                f'preserveAspectRatio="xMidYMid meet"/>'
                f'<circle class="rkb" cx="16" cy="16" r="8"/>'
                f'<text class="rk" x="16" y="20">{i + 1}</text>'
            )
        else:
            face = f'<text y="14">{i + 1}</text>'
        p.append(
            f'<g class="ptok" data-rank="{i}" '
            f'transform="translate({at[i]:.1f} {cy + dy}) scale({TOKEN_SCALE})">'
            f'<path class="{cls}" d="{JERSEY}"/>'
            f"{face}"
            f"<title>{i + 1}. {html.escape(t['team'])} - {num(t['form'])}</title></g>"
        )
    p.append("</svg>")
    return "\n".join(p), lo, hi


def svg_chart(table, matchdays, key="series", prefix="line"):
    """Inline SVG, one polyline per team, each ending in its rank token so a
    highlighted line can be named without looking anywhere else."""
    w, h = 900, 400
    left, right, top, bottom = 44, 34, 18, 36
    tick_x = left - 8
    span = max(len(matchdays) - 1, 1)
    y_min, y_max, y_step = y_axis(table, key)

    def x(i):
        return left + (w - left - right) * i / span

    def y(power):
        frac = (power - y_min) / (y_max - y_min)
        return h - bottom - (h - bottom - top) * frac

    label = "Formverlauf" if key == "fseries" else "Verlauf der Saisonwerte"
    parts = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{label} je Spieltag">']

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
            f'<text class="tick" x="{tick_x}" y="{y(tick) + 4:.1f}" '
            f'text-anchor="end">{tick}</text>'
        )
    for i, md in enumerate(matchdays):
        if len(matchdays) <= 14 or md % 2 == 0 or i == len(matchdays) - 1:
            parts.append(
                f'<text class="tick" x="{x(i):.1f}" y="{h - 13}" '
                f'text-anchor="middle">{md}</text>'
            )
    for rank, t in enumerate(table):
        values = t[key]
        points = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(values))
        dots = "".join(
            f'<circle cx="{x(i):.1f}" cy="{y(v):.1f}" r="3.2"/>' for i, v in enumerate(values)
        )
        ex, ey = x(len(values) - 1), y(values[-1])
        cls = "pos" if values[-1] >= 50 else "neg"
        end = (
            f'<g class="end"><circle class="{cls}" cx="{ex:.1f}" cy="{ey:.1f}" r="13"/>'
            f'<text x="{ex:.1f}" y="{ey + 4.6:.1f}">{rank + 1}</text></g>'
        )
        parts.append(
            f'<g class="line" id="{prefix}{rank}" style="--c:{PALETTE[rank % len(PALETTE)]}">'
            f'<polyline points="{points}"/>{dots}{end}</g>'
        )

    parts.append("</svg>")
    return "\n".join(parts)


def crest_img(logos, team):
    crest = logos.get(team_slug(team))
    return f'<img class="lg sm" src="{crest}" alt="">' if crest else ""


def forecast_section(season, played, logos, forms):
    """The next matchday as probabilities. Empty string if nothing is scheduled,
    which is what a finished season looks like."""
    matchday, fixtures = next_matchday(load(season, "scheduled"))
    tips = forecast(played, fixtures)
    if not tips:
        return ""

    # The one fixture worth marking, by the page's own headline number: the pair
    # with the best combined form. Deliberately not "closest percentages" - those
    # sit inside a few points of each other all season, so picking the tightest
    # one would be marking noise.
    top = max(range(len(tips)),
              key=lambda i: forms.get(tips[i]["home"], 50) + forms.get(tips[i]["away"], 50))

    body = []
    for i, t in enumerate(tips):
        best = max(("p_home", "p_draw", "p_away"), key=t.__getitem__)
        cells = "".join(
            f'<td class="p{" best" if key == best else ""}">{pct(t[key])}</td>'
            for key in ("p_home", "p_draw", "p_away")
        )
        sides = "".join(
            f'<div class="fxt">{crest_img(logos, t[side])}'
            f'<span>{html.escape(t[side])}</span></div>' for side in ("home", "away")
        )
        bar = "".join(f'<i class="{cls}" style="width:{t[key]:.1%}"></i>'
                      for cls, key in (("w", "p_home"), ("d", "p_draw"), ("l", "p_away")))
        mark = '<span class="badge">Topspiel</span>' if i == top else ""
        body.append(
            f'<tr class="{"hl marked" if i == top else ""}">'
            f'<td class="l dt">{short_date(t["date"])}</td>'
            f'<td class="l fx">{sides}<div class="bar">{bar}</div>{mark}</td>'
            f'{cells}'
            f'<td class="xg s-hide">{num(t["xg_home"] + t["xg_away"])}</td></tr>'
        )

    league_goals = sum(int(r["home_goals"]) + int(r["away_goals"]) for r in played) / len(played)

    record = track_record(played)
    if record:
        ahead = "vorn" if record["rps"] < record["base_rps"] else "hinten"
        balance = (
            f'<strong>Bilanz.</strong> {record["hits"]} von {record["n"]} Spielen dieser '
            f'Saison richtig – gemeint ist jeweils der wahrscheinlichste Ausgang. Als '
            f'Fehlerwert gerechnet (kleiner ist besser): {num(record["rps"], 3)} gegen '
            f'{num(record["base_rps"], 3)}, wenn man stur die Liga-Quote tippt, die Prognose '
            f'liegt also knapp {ahead}. Bei {record["n"]} Spielen heißt das noch nichts; über '
            f'die Vorsaison gerechnet lag die Trefferquote bei 57&nbsp;%.'
        )
    else:
        balance = "<strong>Bilanz.</strong> Noch zu wenige Spiele für eine Bilanz."

    return f"""<section class="fcast">
    <h2>Prognose für Spieltag {matchday}</h2>
    <p class="sub">Was das Modell für die nächsten Spiele erwartet – Wahrscheinlichkeiten,
    keine Tipps.</p>
    <div class="card">
      <table>
        <thead>
          <tr>
            <th class="l">Termin</th><th class="l">Begegnung</th>
            <th>Heim</th><th>Remis</th><th>Ausw.</th>
            <th class="s-hide">Tore erwartet</th>
          </tr>
        </thead>
        <tbody>
          {chr(10).join("          " + r for r in body).strip()}
        </tbody>
      </table>
    </div>
    <div class="cols hints">
    <p class="hint"><strong>Wie das gerechnet wird.</strong> Aus allen bisherigen Ergebnissen
    bekommt jedes Team eine Angriffs- und eine Abwehrstärke. Daraus folgt, wie viele Tore beide
    Seiten in dieser Paarung im Schnitt erzielen – Heimvorteil eingerechnet –, und aus dem
    Abstand zwischen beiden werden die drei Prozentwerte. Die Umrechnung ist an der kompletten
    Vorsaison geeicht, nicht geschätzt. <strong>Tore erwartet</strong> ist die Summe für beide
    Mannschaften, also eher ein Hinweis auf offenes Spiel oder Abtasten als auf den Sieger:
    Der Ligaschnitt dieser Saison liegt bei {num(league_goals)} Toren pro Spiel.
    <strong>Topspiel</strong> markiert die Paarung mit der besten gemeinsamen Form beider
    Teams – eine Auszeichnung nach der Formtabelle oben, keine Aussage über den Ausgang.</p>
    <p class="hint">Zwei Dinge fallen auf und sind beide richtig so. Ein <strong>Remis ist nie
    der wahrscheinlichste Ausgang</strong>, obwohl rund jedes sechste Spiel remis endet – für
    ein Unentschieden müssen beide Seiten dieselbe Zahl treffen, jede einzelne Torzahl ist
    unwahrscheinlicher als „irgendein Sieg“. Und die <strong>Prozente liegen eng beieinander</strong>,
    auch wenn ein Team klar stärker eingeschätzt wird. Das ist gemessen und nicht gedämpft: Ein
    ganzes Tor Vorsprung in der Erwartung verschiebt die Siegchance in dieser Liga nur um rund
    sechs Prozentpunkte, weil die Ergebnisse hier zu stark streuen, um mehr herzugeben.</p>
    <p class="hint">{balance}</p>
    </div>
  </section>
"""


def predictor_section():
    """The measured table of things that ought to predict better. None does, and
    showing that is the honest way to publish a forecast at all."""
    body = []
    for r in compare():
        lead = ("<span class=\"flat\">Referenz</span>" if r["baseline"] else
                f'{num(r["lead"] * 1000, 1)} <span class="pm">± {num(r["se"] * 1000, 1)}</span>')
        # The zero point everything else is measured against gets ruled off, the
        # way a sheet rules off the line a column is totalled on.
        body.append(f'<tr class="{"base" if r["baseline"] else ""}">'
                    f'<td class="l">{r["name"]}</td>'
                    f'<td>{num(r["rps"], 4)}</td><td class="ld">{lead}</td></tr>')

    return f"""<section class="fcast">
    <h2>Was besser sein müsste – und es nicht ist</h2>
    <p class="sub">Jeder dieser Ansätze wurde mit demselben Verfahren in Wahrscheinlichkeiten
    umgerechnet und an der Saison {SEASON_PREVIOUS} nachgerechnet, ab Spieltag
    {SKIP_MATCHDAYS + 1}.</p>
    <div class="card">
      <table>
        <thead>
          <tr><th class="l">Ansatz</th><th>Fehler</th><th>Vorsprung auf die Liga-Quote</th></tr>
        </thead>
        <tbody>
          {chr(10).join("          " + r for r in body).strip()}
        </tbody>
      </table>
    </div>
    <p class="hint"><strong>Fehler</strong> ist der mittlere Prognosefehler über alle Spiele,
    kleiner ist besser. <strong>Vorsprung</strong> ist der Abstand zur simpelsten aller
    Auskünfte – „in dieser Liga gewinnt meistens das Heimteam“ –, in Tausendsteln und mit
    Standardfehler dahinter. Kein einziger Ansatz erreicht zwei Standardfehler: Keiner ist
    nachweisbar besser als diese Auskunft. Auch die Reihenfolge in der Tabelle ist selbst
    Zufall – rechnet man die Eichung strenger, tauschen die Zeilen die Plätze. Deshalb ist die
    Prognose ein Blickwinkel und kein Tipp, und deshalb bleibt die Form auf dieser Seite eine
    Beschreibung.</p>
  </section>
"""


def render(season, rows):
    table, matchdays = build_table(rows)
    matchday = matchdays[-1]
    last_date = max(r["date"] for r in rows)
    last_date = ".".join(reversed(last_date.split("-")))
    generated = date.today().strftime("%d.%m.%Y")

    top_team, bottom_team = table[0], table[-1]
    # The team the table is most wrong about right now - the whole reason the
    # page exists, so it opens the page as a sentence rather than sitting in a
    # box in the corner. Worded as two readings of the same season, never as a
    # claim about next week.
    out_rank, out = max(enumerate(table), key=lambda p: abs(p[1]["position"] - (p[0] + 1)))
    out_diff = out["position"] - (out_rank + 1)
    plural = "Platz" if abs(out_diff) == 1 else "Plätze"
    if out_diff == 0:
        lede_pre = "Diese Woche sind sich beide einig:"
        lede_who = "Form und Tabelle"
        lede_post = "Kein Team steht in dieser Formtabelle anders als in der offiziellen."
    else:
        lede_pre = f"Die Tabelle sagt Platz {out['position']}."
        lede_who = html.escape(out["team"])
        nur = "" if out_diff > 0 else "nur "
        richtung = "besser" if out_diff > 0 else "schlechter"
        lede_post = (f"Die letzten fünf Spieltage sagen {nur}Platz {out_rank + 1} – "
                     f"{abs(out_diff)} {plural} {richtung}, als die Tabelle vermuten lässt.")

    # Two markers on the table, each by a fixed rule so a reader can check them
    # against the row they sit on. Both have a floor: one place of difference or
    # a form change of half a point is well inside the noise this page keeps
    # warning about, and a badge would sell it as a story.
    # Not `out` above: that is the largest gap in either direction, and the badge
    # only ever marks a team playing above its table place.
    best_gap, hot_rank = max((t["position"] - (rank + 1), rank) for rank, t in enumerate(table))
    hot_rank = hot_rank if best_gap >= 2 else None
    jumps = [(t["delta"], rank) for rank, t in enumerate(table) if t["delta"] is not None]
    best_delta, best_rank = max(jumps, default=(0, None))
    jump_rank = best_rank if best_delta >= 1.5 and best_rank != hot_rank else None

    logos = load_logos()
    pitch_svg, pitch_lo, pitch_hi = svg_pitch(table, logos)

    # Forecast and the measurement of what it is worth belong side by side - the
    # page is not allowed to publish one without the other, and set as two
    # columns of the same row that pairing is shown rather than only asserted.
    # A finished season has no fixtures left, and then the comparison stands on
    # its own at the page's normal measure.
    fcast = forecast_section(season, rows, logos, {t["team"]: t["form"] for t in table})
    predictors = predictor_section()
    if fcast:
        outlook = (f'<div class="dash fdash"><div class="col">{fcast}</div>'
                   f'<div class="col">{predictors}</div></div>')
    else:
        outlook = f'<div class="fdash solo">{predictors}</div>'

    body_rows = []
    for rank, t in enumerate(table):
        w, d, l = t["record"]
        side = "pos" if t["form"] >= 50 else "neg"
        if t["delta"] is None:
            delta = '<span class="flat">–</span>'
        else:
            sign = "+" if t["delta"] >= 0 else "−"
            cls = "up" if t["delta"] > 0.05 else ("down" if t["delta"] < -0.05 else "flat")
            delta = f'<span class="{cls}">{sign}{num(abs(t["delta"]))}</span>'
        # Positive: the team is playing better than its table place suggests.
        diff = t["position"] - (rank + 1)
        if diff == 0:
            gap = '<span class="chip flat">±0</span>'
        else:
            cls = "up" if diff > 0 else "down"
            gap = f'<span class="chip {cls}">{"+" if diff > 0 else "−"}{abs(diff)}</span>'
        segs = "".join(f'<i class="{r}"></i>' for r in t["dots"])
        tok = f'<span class="tok {side}">{rank + 1}</span>'
        crest = logos.get(team_slug(t["team"]))
        crest = f'<img class="lg" src="{crest}" alt="">' if crest else ""
        if rank == hot_rank:
            # The highlighter across the row does the pointing for this one.
            badge = '<span class="badge">Mannschaft der Stunde</span>'
        elif rank == jump_rank:
            badge = f'<span class="badge">Formsprung{ARROW}</span>'
        else:
            badge = ""

        # The badge sits beside the name block rather than inside it, and CSS
        # takes it out of flow: writing on a printed sheet cannot move what was
        # printed first, so an annotation must not change a row's height.
        # Only the team of the hour gets the highlighter on top of its note; the
        # form jump is the smaller finding and stays a written remark, so the
        # two markers are told apart by how loudly they are marked.
        body_rows.append(
            f'<tr data-rank="{rank}" tabindex="0" '
            f'class="{"marked" if rank == hot_rank else ""}" '
            f'style="--c:{PALETTE[rank % len(PALETTE)]};--i:{rank}">'
            f'<td class="rank">{tok}</td>'
            f'<td class="team"><div class="tc">{crest}<div>'
            f'<span class="tn">{html.escape(t["team"])}</span>'
            f'<span class="form"><span class="seg" aria-hidden="true">{segs}</span>'
            f'<span class="rt">{w}-{d}-{l}</span></span></div></div>{badge}</td>'
            f'<td class="power"><div class="pw"><b>{num(t["form"])}</b></div></td>'
            f"<td>{delta}</td>"
            f'<td class="power season">{num(t["power"])}</td>'
            f"<td class=\"s-hide\">{t['matches']}</td>"
            f"<td class=\"s-hide\">{t['gf']}:{t['ga']}</td>"
            f"<td class=\"s-hide\">{signed(t['gf'] - t['ga'])}</td>"
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
  {font_face("Archivo Narrow", "archivo-narrow.woff2", "400 700")}
  {font_face("Source Sans 3", "source-sans-3.woff2", "300 700")}
  {font_face("Caveat", "caveat.woff2", "500 700")}
  :root {{
    /* Two faces, far apart on purpose. The narrow print grotesque carries every
       figure, heading and table head - this is a page about one number per team,
       so the numbers get the voice. The humanist sans carries club names and the
       long plain-language passages, where width and openness matter more. */
    --display:"Archivo Narrow","Arial Narrow",system-ui,sans-serif;
    --body:"Source Sans 3","Segoe UI",system-ui,sans-serif;
    /* Only the two editorial markers use this - the bits of the sheet a person
       wrote rather than the model computed. Subset to letters and a space, so
       a badge label with a digit in it would fall back to the display face. */
    --hand:"Caveat","Segoe Script",cursive;

    /* Uncoated stock and printing ink: this is a notice pinned up after the
       weekend, not a broadcast graphic. */
    --paper:#e7e4dd; --card:#fcfbf8; --ink:#23201b; --muted:#6b6459;
    --line:#d8d3c8; --track:#dcd7cc; --sel:#f1eee4;
    /* Above and below the 50-point league average. These two mean the same
       thing everywhere on the page and are the one thing that must not drift. */
    --up:#16764f; --down:#96263f; --draw:#a9a294;
    /* The pitch is the only dark surface on the page, and it is grass rather
       than a dark UI panel - so both meanings need a lifted variant on it. */
    --turf:#1f3129; --chalk:rgba(255,255,255,.26); --on-turf:#a7bdaf;
    --up-l:#63d9a6; --down-l:#f2808f;
    /* Editorial markers only, in a colour no measurement uses: amber ink for
       the written notes, a highlighter yellow for the rows they point at. */
    --mark:#7a4a09; --mark-bg:#f6e2bc; --mark-line:#e6cb96;
  }}
  * {{ box-sizing:border-box; }}
  /* The stock itself, then what has happened to it. Top three layers are four
     coffee marks in fixed places, so the sheet looks the same from one matchday
     to the next; under them the sparse dark flecks of recycled paper and a fine
     fibre grain, both drawn by the browser rather than shipped as images. All
     of it sits on the ground only - every card and the pitch are opaque. */
  body {{ margin:0; background-color:var(--paper); color:var(--ink);
         font:16.5px/1.62 var(--body); -webkit-font-smoothing:antialiased;
         background-repeat:no-repeat, no-repeat, no-repeat, no-repeat,
                           repeat, repeat;
         background-size:186px 168px, 118px 112px, 148px 132px, 96px 92px,
                         240px 240px, 190px 190px;
         background-position:5% 2.4%, 93% 5%, 86% 71%, 11% 92%, 0 0, 0 0;
         background-image:
           radial-gradient(ellipse at 50% 50%, rgba(122,84,40,0) 0 43%,
             rgba(122,84,40,.075) 45% 49%, rgba(122,84,40,.03) 50.5% 54%,
             rgba(122,84,40,0) 56%),
           radial-gradient(ellipse at 50% 50%, rgba(122,84,40,0) 0 45%,
             rgba(122,84,40,.06) 47% 51%, rgba(122,84,40,0) 53%),
           radial-gradient(ellipse at 50% 50%, rgba(122,84,40,.032) 0 34%,
             rgba(122,84,40,.05) 44% 48%, rgba(122,84,40,0) 51%),
           radial-gradient(ellipse at 50% 50%, rgba(122,84,40,.045) 0 30%,
             rgba(122,84,40,0) 64%),
           url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='240' height='240'%3E%3Cfilter id='f'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='1.1' numOctaves='1' stitchTiles='stitch'/%3E%3CfeColorMatrix type='saturate' values='0'/%3E%3CfeComponentTransfer%3E%3CfeFuncA type='linear' slope='7' intercept='-4.85'/%3E%3C/feComponentTransfer%3E%3C/filter%3E%3Crect width='240' height='240' filter='url(%23f)' opacity='0.5'/%3E%3C/svg%3E"),
           url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='190' height='190'%3E%3Cfilter id='p'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.55 1.3' numOctaves='5' stitchTiles='stitch'/%3E%3CfeColorMatrix type='saturate' values='0'/%3E%3C/filter%3E%3Crect width='190' height='190' filter='url(%23p)' opacity='0.115'/%3E%3C/svg%3E"); }}
  .wrap {{ max-width:1760px; margin:0 auto; padding:0 clamp(16px,2.4vw,36px); }}
  .prose {{ max-width:66ch; }}
  p {{ margin:0 0 12px; }}
  strong, b {{ font-weight:600; }}
  a {{ color:inherit; text-underline-offset:2px; }}
  a:focus-visible {{ outline:2px solid currentColor; outline-offset:3px; }}
  :where(tr, .ptok path, .line polyline) {{ transition:background-color .12s ease,
    box-shadow .12s ease, stroke .12s ease, stroke-width .12s ease; }}
  @media (prefers-reduced-motion:reduce) {{ * {{ transition:none !important; }} }}

  /* ---- The token: one numbered disc, coloured by side of the 50 average ---- */
  .tok {{ display:inline-flex; align-items:center; justify-content:center;
          width:27px; height:27px; border-radius:50%; flex:0 0 auto;
          font:700 14px var(--display); color:#fff;
          font-variant-numeric:tabular-nums; }}
  .tok.pos {{ background:var(--up); }}
  .tok.neg {{ background:var(--down); }}

  /* ---- Masthead ----------------------------------------------------------- */
  /* No dark hero band: the sheet starts on paper and the heavy rule under the
     wordmark is what says "this is the head of the notice". */
  .mast {{ padding:22px 0 0; }}
  .mhead {{ display:flex; flex-wrap:wrap; align-items:flex-end; gap:10px 32px;
            justify-content:space-between; padding-bottom:10px;
            border-bottom:3px solid var(--ink); }}
  h1 {{ margin:0; font:700 clamp(30px,3.9vw,50px)/1 var(--display); }}
  .tag {{ margin:7px 0 0; color:var(--muted); font-size:15px; max-width:74ch; }}
  .where {{ margin:0; color:var(--muted); font-size:14.5px; line-height:1.4;
            text-align:right; }}
  .where b {{ color:var(--ink); font-weight:600;
              font-variant-numeric:tabular-nums; }}

  /* The claim the official table cannot make, stated as a sentence. It is the
     reason the page exists, so it opens the page. The two league extremes sit
     beside it rather than under it - stacked, the head ran half a screen tall
     before the ranking itself came into view. */
  .lede {{ display:flex; flex-wrap:wrap; align-items:flex-end;
           justify-content:space-between; gap:18px 52px; padding:18px 0 20px; }}
  .claim {{ flex:1 1 460px; }}
  .claim p {{ margin:0; max-width:46ch; font-size:clamp(16px,1.35vw,19px);
              color:var(--muted); }}
  .claim .who {{ font:700 clamp(28px,3.9vw,50px)/1.04 var(--display);
                 color:var(--ink); margin:2px 0 4px; max-width:20ch; }}

  .facts {{ flex:0 0 auto; display:flex; flex-direction:column; gap:5px; }}
  .fact {{ display:flex; align-items:baseline; gap:9px; margin:0; }}
  .fact .k {{ color:var(--muted); font-size:14.5px; }}
  .fact .t {{ font-weight:600; }}
  .fact .n {{ font:700 21px var(--display); font-variant-numeric:tabular-nums; }}
  .fact .n.pos {{ color:var(--up); }} .fact .n.neg {{ color:var(--down); }}

  /* ---- Body -------------------------------------------------------------- */
  main {{ padding:0 0 60px; }}
  h2 {{ font:600 22px/1.15 var(--display); margin:28px 0 8px; padding-top:9px;
        border-top:2.5px solid var(--ink); }}
  section > h2:first-child {{ margin-top:0; }}
  .sub {{ color:var(--muted); font-size:14.5px; margin:-1px 0 13px; max-width:66ch; }}

  /* The pitch runs across the full page width - it is the one view that shows
     the whole league at once, and the tokens need the room. */
  .pitchsec {{ margin:0 0 6px; }}

  /* The dashboard below it: table on the left, the form chart on the right, so
     that clicking a team is visible in all three at once. */
  .dash {{ display:grid; grid-template-columns:1fr; gap:26px; align-items:start; }}
  .col section + section {{ margin-top:22px; }}

  .card {{ background:var(--card); border:1px solid var(--line); border-radius:3px;
           padding:0; overflow-x:auto; }}
  table {{ width:100%; border-collapse:collapse;
           font:15.5px var(--display); font-variant-numeric:tabular-nums; }}
  th, td {{ padding:9px 8px; text-align:right; border-bottom:1px solid var(--line); }}
  th:first-child, td:first-child {{ padding-left:14px; }}
  th:last-child, td:last-child {{ padding-right:14px; }}
  tbody tr:last-child td {{ border-bottom:0; }}
  /* A ruled head rather than a dark bar: the whole page is a printed sheet, and
     the double rule is how a sheet separates the head from the entries. */
  thead th {{ font:600 13.5px var(--display); color:var(--ink); white-space:nowrap;
              border-bottom:2px solid var(--ink); padding-top:11px;
              padding-bottom:7px; }}
  th.l, td.team, td.power {{ text-align:left; }}
  td.rank {{ width:46px; }}
  td.team {{ line-height:1.3; min-width:232px; }}
  .tc {{ display:flex; align-items:center; gap:10px; }}
  .lg {{ width:28px; height:28px; object-fit:contain; flex:0 0 auto; }}
  /* A club name that wraps makes its row taller than every other one, which is
     exactly the ragged scan the form table exists to avoid. It keeps one line
     and widens its column instead; the card already scrolls if that is too
     much. The phone rule below hands wrapping back, where width is the scarce
     thing rather than rhythm. */
  .tn {{ display:block; font-family:var(--body); font-weight:600; font-size:15.5px;
         white-space:nowrap; }}
  /* nowrap so an annotation too long for the cell runs out of it rather than
     dropping onto a line of its own; the phone rule below puts wrapping back. */
  .form {{ display:flex; align-items:center; flex-wrap:nowrap; gap:4px 8px; margin-top:2px; }}
  /* The one bold thing on the page: the form figure, set large in the narrow
     face. Everything around it stays quiet. */
  .pw b {{ font:700 23px var(--display); }}

  .seg {{ display:flex; gap:4px; flex:0 0 auto; align-items:center; }}
  .seg i {{ width:7px; height:7px; border-radius:50%; flex:0 0 auto; }}
  .seg i.w {{ background:var(--up); }}
  .seg i.d {{ background:var(--draw); }}
  .seg i.l {{ background:var(--down); }}
  .rt {{ color:var(--muted); font-size:13px; }}

  tbody tr[data-rank] {{ cursor:pointer; }}
  tbody tr[data-rank]:hover {{ background:#f2efe7; }}
  tbody tr:focus-visible {{ outline:2px solid var(--ink); outline-offset:-2px; }}
  /* One treatment for every selected row. Tinting each row with its own colour
     read as several different states instead of one. */
  tr.sel {{ background-color:var(--sel); }}

  /* The row an editorial marker points at, gone over with a highlighter. Two
     strokes, neither of them the width of the row - a marker starts and stops
     where the hand stops, and the second pass never lands on the first.
     Background images rather than positioned pseudo-elements: `position:
     relative` on a <tr> throws off column widths under `border-collapse:
     collapse`, which squeezed the marked row's cells to a fraction of the
     others. The wobble is in the SVG outline, the uneven pressure in its
     gradient, and a background paints under the cell content for free. */
  tr.marked {{ background-repeat:no-repeat, no-repeat;
            background-size:63% 64%, 46% 42%;
            background-position:2% 56%, 10% 88%;
            background-image:{STROKE_A}, {STROKE_B}; }}
  tr.sel td:first-child {{ box-shadow:inset 4px 0 0 var(--c); }}
  tr.sel .tok {{ box-shadow:0 0 0 2px var(--sel), 0 0 0 4px var(--c); }}

  .up {{ color:var(--up); }} .down {{ color:var(--down); }} .flat {{ color:var(--muted); }}
  td.tab {{ white-space:nowrap; }}
  /* Square: a chip is a measured difference. Badges below are stamps and stay
     rounded, so the two never read as the same kind of thing. */
  .chip {{ display:inline-block; margin-left:6px; padding:1px 6px; border-radius:2px;
           font:600 12.5px var(--display); }}
  .chip.up {{ background:#d6e8de; color:#0e5c3d; }}
  .chip.down {{ background:#f2dbe0; color:#7d1f34; }}
  .chip.flat {{ background:#e4e0d6; color:var(--muted); }}

  /* Editorial marker, deliberately in a colour no data uses: green and wine
     mean above and below average everywhere else on the page, and a badge is
     not a measurement. Set as a pen annotation for the same reason - a ring
     drawn round a row by hand cannot be mistaken for something the model
     computed. The lopsided radii are what make the ring look drawn; they scale
     with the label instead of distorting the way a stretched drawing would.
     Taken out of flow entirely and anchored to the cell rather than laid out
     in it: an annotation written onto a sheet cannot push the print around, so
     it must not change a row's height or a column's width. It is free to run
     over the rule below it and across the cell next door. */
  td.team, td.fx {{ position:relative; }}
  .badge {{ position:absolute; z-index:5; left:150px; bottom:-3px;
            padding:2px 13px 3px; white-space:nowrap;
            font:700 16px/1.2 var(--hand);
            color:var(--mark); transform:rotate(-4.2deg); }}
  /* Two overlapping ovals, because that is how a ring round something on paper
     actually comes out - one pass never closes. Percentage radii keep it an
     oval at any label length instead of a rounded box. */
  .badge::before, .badge::after {{ content:""; position:absolute; inset:0;
            border:1.5px solid rgba(122,74,9,.5);
            border-radius:47% 53% 44% 56%/62% 58% 42% 38%; }}
  .badge::after {{ border-radius:53% 47% 57% 43%/45% 40% 60% 55%;
            transform:rotate(1.3deg) scale(1.035); opacity:.5; }}
  /* Drawn outside the ring and clear of the digits, so the note reaches the
     column it is about without covering anything measured. */
  .arw {{ position:absolute; left:100%; top:-3px; width:46px; height:21px;
          margin-left:3px; overflow:visible; }}
  .arw path {{ fill:none; stroke:rgba(122,74,9,.62); stroke-width:1.9;
               stroke-linecap:round; stroke-linejoin:round; }}

  /* ---- Forecast and the predictor comparison ------------------------------ */
  /* The two share one row of the same grid the dashboard uses: publishing a
     forecast is only defensible next to the measurement of how little it is
     worth, and side by side that is shown rather than only written down. */
  .fdash {{ margin-top:34px; }}
  .fdash.solo {{ max-width:1080px; }}
  .fcast td.l {{ text-align:left; }}
  .fcast td.dt {{ color:var(--muted); font-size:13.5px; white-space:nowrap; }}
  .fcast td.p {{ width:64px; }}
  .fcast td.best {{ font-weight:700; }}
  .fcast td.xg, .fcast td.ld {{ color:var(--muted); white-space:nowrap; }}
  .fcast tr.base > td {{ border-top:2px solid var(--ink); }}
  .fcast td.l:first-child + td {{ font-family:var(--body); }}
  .fcast .pm {{ font-size:12.5px; }}
  /* The highlighter carries the marking here, so no tinted row underneath it,
     and the strokes get their own lengths - two rows marked identically would
     look stamped rather than written. */
  .fcast tr.hl > td:first-child {{ box-shadow:inset 3px 0 0 var(--mark); }}
  .fcast tr.marked {{ background-size:56% 58%, 38% 34%;
            background-position:3% 34%, 14% 78%; }}
  /* In the empty right half of the fixture cell, clear of both club names. */
  .fx .badge {{ left:auto; right:5%; bottom:auto; top:50%;
                transform:translateY(-50%) rotate(-4.8deg); }}
  .fxt {{ display:flex; align-items:center; gap:8px; line-height:1.3; }}
  .fxt span {{ font-weight:600; }}
  .fxt + .fxt {{ margin-top:3px; }}
  .lg.sm {{ width:21px; height:21px; }}
  /* Same three colours as the result dots, and the same meaning: seen from the
     home side, win / draw / loss. */
  .bar {{ display:flex; height:5px; max-width:280px; margin-top:7px;
          overflow:hidden; background:var(--track); }}
  .bar i.w {{ background:var(--up); }}
  .bar i.d {{ background:var(--draw); }}
  .bar i.l {{ background:var(--down); }}

  /* ---- The pitch panel ---------------------------------------------------- */
  /* Grass, with the mown bands the stripes on a real pitch make - the one place
     on the page where a texture depicts the actual object. */
  /* Four layers, front to back: a grain so the green is not a flat fill, a
     vignette for the fall-off a real pitch has towards the touchlines, the fine
     lines the mower leaves, and the wide mown bands across the pitch. */
  .pitchcol {{ background-color:var(--turf); border-radius:3px; padding:11px 14px 10px;
               background-repeat:repeat, no-repeat, repeat, repeat;
               background-size:150px 150px, 100% 100%, 100% 100%, 100% 100%;
               background-image:
                 url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='150' height='150'%3E%3Cfilter id='g'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3CfeColorMatrix type='saturate' values='0'/%3E%3C/filter%3E%3Crect width='150' height='150' filter='url(%23g)' opacity='0.13'/%3E%3C/svg%3E"),
                 radial-gradient(130% 155% at 50% 50%, rgba(0,0,0,0) 36%,
                   rgba(0,0,0,.32) 100%),
                 repeating-linear-gradient(0deg, rgba(255,255,255,.017) 0 4px,
                   rgba(255,255,255,0) 4px 9px),
                 repeating-linear-gradient(90deg, rgba(255,255,255,.055) 0 58px,
                   rgba(0,0,0,.07) 58px 116px); }}
  .pwrap {{ position:relative; }}
  .pwrap svg {{ width:100%; height:auto; display:block; }}
  .pg rect, .pg circle {{ fill:none; stroke:var(--chalk); stroke-width:1.6; }}
  .half {{ stroke:var(--chalk); stroke-width:1.6; }}
  .stick {{ stroke:rgba(255,255,255,.19); stroke-width:1.4; }}
  .axis {{ stroke:rgba(255,255,255,.15); stroke-width:1; stroke-dasharray:3 5; }}
  .ptok {{ cursor:pointer; }}
  .ptok path {{ stroke:var(--turf); stroke-width:2.5; stroke-linejoin:round; }}
  .ptok path.pos {{ fill:var(--up-l); }}
  .ptok path.neg {{ fill:var(--down-l); }}
  /* The crest sits on a white chest patch, so the shirt colour still reads as
     the above/below-average marker the legend explains. */
  .ptok circle.in {{ fill:#fff; }}
  .ptok circle.rkb {{ fill:var(--turf); stroke:#fff; stroke-width:1.5; }}
  .ptok text {{ fill:var(--turf); font:700 23px var(--display); text-anchor:middle; }}
  .ptok text.rk {{ fill:#fff; font-size:12px; }}
  .ptok.sel path {{ stroke:#fff; stroke-width:3.5; }}
  .pends {{ display:flex; justify-content:space-between; gap:12px; margin:0 0 5px; }}
  .pend {{ display:flex; align-items:center; gap:7px; margin:0;
           color:var(--on-turf); font-size:12.5px; }}
  .pend i {{ width:9px; height:9px; border-radius:50%; flex:0 0 auto; }}
  .pend i.pos {{ background:var(--up-l); }} .pend i.neg {{ background:var(--down-l); }}
  .pend b {{ color:#fff; font:700 15px var(--display);
             font-variant-numeric:tabular-nums; }}
  .phead {{ margin:9px 0 0; padding-top:8px; color:var(--on-turf); font-size:12.5px;
            line-height:1.5; border-top:1px solid rgba(255,255,255,.12); }}

  /* ---- Chart -------------------------------------------------------------- */
  .card.chart {{ padding:10px 12px; }}
  .chart svg {{ width:100%; height:auto; display:block; }}
  .zone.pos {{ fill:var(--up); opacity:.05; }}
  .zone.neg {{ fill:var(--down); opacity:.05; }}
  .grid {{ stroke:#e9e5da; stroke-width:1; }}
  .tick {{ fill:var(--muted); font:11.5px var(--display); }}
  .line polyline {{ fill:none; stroke:#d5cfc2; stroke-width:1.5; stroke-linejoin:round;
                    stroke-linecap:round; }}
  .line circle {{ display:none; }}
  .line .end {{ display:none; }}
  .line.sel polyline {{ stroke:var(--c); stroke-width:2.8; }}
  .line.sel circle {{ display:inline; fill:var(--card); stroke:var(--c); stroke-width:2; }}
  .line.sel .end {{ display:inline; }}
  .end circle {{ display:inline; stroke:var(--card); stroke-width:2.5; }}
  .end circle.pos {{ fill:var(--up); }}
  .end circle.neg {{ fill:var(--down); }}
  .end text {{ fill:#fff; font:700 13px var(--display); text-anchor:middle; }}
  /* The season score is context, not the headline: same column width, quieter. */
  td.season {{ color:var(--muted); font-weight:600; }}

  .hint {{ color:var(--muted); font-size:14px; line-height:1.58; margin:12px 0 0;
           max-width:66ch; }}
  /* The legend for the table sits in the chart column, not under the table it
     explains: side by side the chart ran three hundred pixels short of the
     fourteen rows beside it, and a legend reads as well across the gutter as
     underneath. Ruled off so it is not taken for a note about the chart. */
  .hint.legend {{ margin-top:15px; padding-top:12px;
                  border-top:1px solid var(--line); }}
  /* The small print under the forecast, set denser and in two columns so the
     explanation does not run longer than the table it explains. */
  .cols {{ column-count:2; column-gap:46px; max-width:1000px;
           font-size:15.5px; line-height:1.58; }}
  .cols p {{ margin:0 0 13px; break-inside:avoid; }}
  .cols.hints {{ margin-top:14px; }}
  .cols .hint {{ max-width:none; margin:0 0 13px; }}

  /* The rule runs the width of the sheet, the text keeps a readable measure. */
  footer {{ margin-top:40px; padding-top:14px; border-top:2.5px solid var(--ink);
            color:var(--muted); font-size:13.5px; }}
  footer p {{ margin:0; max-width:100ch; }}

  /* Two columns as soon as the table fits next to the chart without scrolling. */
  @media (min-width:1400px) {{
    /* Nine columns of table need the room more than five matchdays of chart. */
    .dash {{ grid-template-columns:minmax(0,60fr) minmax(0,40fr); gap:30px; }}
    /* Seeing the line move is the point of clicking a row, so the chart follows
       the table down. It now carries the legend too, so it can get taller than
       a short window - then it scrolls in place rather than hiding its foot. */
    .chartcol {{ position:sticky; top:18px;
                 max-height:calc(100vh - 36px); overflow-y:auto; }}
  }}
  @media (max-width:1399px) {{
    /* Stacked: keep one comfortable measure instead of stretching to 1760px. */
    .dash, .pitchsec, .fdash {{ max-width:1080px;
                                margin-left:auto; margin-right:auto; }}
  }}
  /* Below this the two columns of small print would be forty characters wide,
     which is worse than one column of the same text. */
  @media (max-width:900px) {{
    .cols {{ column-count:1; max-width:66ch; font-size:16px; }}
  }}
  @media (max-width:700px) {{
    .s-hide {{ display:none; }}
    .mhead {{ align-items:flex-start; }}
    .where {{ text-align:left; }}
    .lede {{ padding:19px 0 18px; }}
    .facts {{ gap:6px 26px; padding:13px 0 24px; }}
    th, td {{ padding:9px 5px; }}
    th:first-child, td:first-child {{ padding-left:10px; }}
    th:last-child, td:last-child {{ padding-right:10px; }}
    td.rank {{ width:38px; }}
    td.team {{ min-width:0; }}
    .tn {{ white-space:normal; }}
    /* Six columns is already a lot on a phone: the season score is the one that
       can go, the form number and the table place carry the message. */
    td.season, th.season {{ display:none; }}
    /* Squeezed into a phone the full-width pitch would shrink the jerseys to a
       few pixels, so it keeps a readable width and scrolls sideways instead. */
    .pwrap {{ overflow-x:auto; }}
    .pwrap svg {{ min-width:700px; }}
    /* Handwriting needs more size than a grotesque to stay legible, so the
       badge gives up padding on a phone rather than point size. A narrow cell
       has no spare width beside the record, so the note hangs off the bottom
       left of the name block instead - still out of flow, still over the rule. */
    .badge {{ font-size:14px; padding:1px 10px 2px; left:30px; bottom:-9px;
              transform:rotate(-3.4deg); }}
    /* No free space beside the clubs at this width - the note covered a name -
       so it moves out into the date column, under the kick-off, and reads as
       written in the margin. */
    .fx .badge {{ left:-56px; right:auto; top:auto; bottom:-6px;
                  transform:rotate(-4deg); }}
    /* Less room, so the strokes reach further across the row. */
    tr.marked {{ background-size:82% 58%, 58% 38%; }}
    .tc {{ gap:7px; }}
    .lg {{ width:23px; height:23px; }}
    .tok {{ width:24px; height:24px; font-size:12.5px; }}
    .pw b {{ font-size:20px; }}
    /* The chart scales with the viewport, so its labels need bigger user units. */
    .tick {{ font-size:19px; }}
  }}
</style>
</head>
<body>
<header class="mast">
  <div class="wrap">
    <div class="mhead">
      <div>
        <h1>Power Ranking</h1>
        <p class="tag">Wer gerade gut spielt – gemessen an den letzten fünf Spieltagen und
        daran, gegen wen. Die Tabelle zeigt die Saison, diese Seite den Moment.</p>
      </div>
      <p class="where">{html.escape(STAFFEL_NAME)}<br>Saison {season}<br>
      Nach <b>Spieltag {matchday}</b>, {last_date}</p>
    </div>
    <div class="lede">
      <div class="claim">
        <p class="pre">{lede_pre}</p>
        <p class="who">{lede_who}</p>
        <p class="post">{lede_post}</p>
      </div>
      <div class="facts">
        <p class="fact"><span class="k">Beste Form</span>
        <span class="t">{html.escape(top_team["team"])}</span>
        <span class="n pos">{num(top_team["form"])}</span></p>
        <p class="fact"><span class="k">Schwächste Form</span>
        <span class="t">{html.escape(bottom_team["team"])}</span>
        <span class="n neg">{num(bottom_team["form"])}</span></p>
      </div>
    </div>
  </div>
</header>
<main class="wrap">
  <section class="pitchsec">
    <h2>Aufstellung</h2>
    <p class="sub">Ein Trikot je Team, aufgestellt nach der aktuellen Form.
    <strong>Die Nummer am Trikot ist der Platz in der Formtabelle.</strong></p>
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

  <div class="dash">
    <div class="col">
      <section>
        <h2>Formtabelle</h2>
        <p class="sub">Sortiert nach den letzten fünf Spieltagen, nicht nach der Saison.
        Fünf Spiele beschreiben, was war – vorhersagen können sie nichts.</p>
        <div class="card rank">
          <table>
            <thead>
              <tr>
                <th>#</th><th class="l">Team</th><th class="l">Form</th>
                <th>+/&minus;</th><th class="l season">Saison</th>
                <th class="s-hide">Sp</th><th class="s-hide">Tore</th>
                <th class="s-hide">Diff</th><th>Tabelle</th>
              </tr>
            </thead>
            <tbody>
              {chr(10).join("              " + r for r in body_rows).strip()}
            </tbody>
          </table>
        </div>
      </section>
    </div>

    <div class="col chartcol">
      <section>
        <h2>Formverlauf</h2>
        <p class="sub">Die Form an jedem Spieltag, also immer das Fenster der fünf davor.
        Diese Linien springen – das ist gewollt, sie zeigen Phasen und keine Bilanz.</p>
        <div class="card chart">{svg_chart(table, matchdays, "fseries", "form")}</div>
        <p class="hint">X-Achse: Spieltag, Y-Achse: Form. Eine Zeile in der Tabelle antippen
        hebt das Team hier und in der Aufstellung hervor.</p>
        <p class="hint legend"><strong>Form</strong> rechnet nur die letzten fünf Spieltage, dafür
        mit Gegnerstärke und Torverhältnis: 50 ist Ligadurchschnitt, darüber heißt besser als der
        Schnitt. <strong>Saison</strong> daneben ist der Wert über alle bisherigen Spiele – wer
        dort hoch steht und in der Form tief, hat eine gute Saison, aber gerade eine schwache
        Phase. <strong>Tabelle</strong> ist der offizielle Platz; der Wert dahinter ist die
        Differenz zum Platz in dieser Formtabelle. <span class="chip up">+2</span> heißt: hier
        zwei Plätze besser als in der Tabelle, das Team spielt gerade also über seinem
        Saisonstand. <strong>+/&minus;</strong> ist die Veränderung der Form gegenüber dem
        letzten Spieltag. Die Punkte unter dem Teamnamen sind dieselben fünf Spiele, chronologisch
        von links nach rechts: grün Sieg, grau Unentschieden, rot Niederlage.
        Zwei Marker, beide nach fester Regel: <strong>Mannschaft der Stunde</strong> steht beim
        Team mit dem größten Vorsprung dieser Formtabelle auf den eigenen Tabellenplatz,
        <strong>Formsprung</strong> beim größten Zugewinn gegenüber dem letzten Spieltag. Sind
        die Abstände zu klein, um etwas zu bedeuten, bleiben sie weg.</p>
      </section>
    </div>
  </div>
{outlook}
  <footer>
    <p>Datenquelle: <a href="{SOURCE_URL}">fussball.de</a> (DFB) – dort stehen die offizielle
    Tabelle und alle Ergebnisse. Diese Seite zeigt nur daraus berechnete Werte.
    Privates, nicht-kommerzielles Projekt. Stand der Berechnung: {generated}</p>
  </footer>
</main>
<script>
  const rows = [...document.querySelectorAll('tbody tr[data-rank]')];
  const tokens = [...document.querySelectorAll('.ptok')];
  const selected = new Set(['0', '1', '2']);

  function apply() {{
    rows.forEach(tr => {{
      const on = selected.has(tr.dataset.rank);
      tr.classList.toggle('sel', on);
      const line = document.getElementById('form' + tr.dataset.rank);
      line.classList.toggle('sel', on);
      if (on) line.parentNode.appendChild(line);     // draw highlighted lines on top
    }});
    tokens.forEach(g => g.classList.toggle('sel', selected.has(g.dataset.rank)));
  }}

  function toggle(rank) {{
    selected.has(rank) ? selected.delete(rank) : selected.add(rank);
    apply();
  }}

  // On a phone the pitch is wider than the screen and scrolls. Left-aligned it
  // opens on the weakest teams with the halfway line off-screen, so it starts
  // centred on the league average instead.
  const pw = document.querySelector('.pwrap');
  if (pw) pw.scrollLeft = (pw.scrollWidth - pw.clientWidth) / 2;

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


def check_css(html):
    """The stylesheet is one long f-string, and a stray `*/` while editing a
    comment silently kills every rule after it - the page still renders, just
    wrong. Twice now. The committed HTML is the deployment, so refuse to write
    one rather than notice it in a screenshot."""
    css = html.split("<style>", 1)[1].split("</style>", 1)[0]
    depth = i = 0
    while i < len(css):
        if css.startswith("/*", i):
            depth += 1
        elif css.startswith("*/", i):
            depth -= 1
            if depth < 0:
                raise ValueError(f"unopened CSS comment near: {css[max(0, i - 90):i + 2]!r}")
        else:
            i += 1
            continue
        i += 2
    if depth:
        raise ValueError("unclosed CSS comment")


def write_report(season, rows, path=OUT_HTML):
    html_out = render(season, rows)
    check_css(html_out)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html_out)
    return path


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Rebuild docs/index.html from matches.csv")
    parser.add_argument("--season", default=SEASON_CURRENT)
    args = parser.parse_args()
    print(write_report(args.season, load(args.season)))

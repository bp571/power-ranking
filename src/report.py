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


WEEKDAYS = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")


def short_date(iso):
    d = date.fromisoformat(iso)
    return f"{WEEKDAYS[d.weekday()]} {d.day:02d}.{d.month:02d}."


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
    w, h = 1600, 340
    pad, cy = 12, 170
    half = hero_half_span(table)
    lo, hi = 50 - half, 50 + half
    inner_l, inner_r = pad + 120, w - pad - 120

    def x(p):
        return inner_l + (inner_r - inner_l) * (p - lo) / (hi - lo)

    cx = x(50)
    p = [
        f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Alle {len(table)} Teams nach Form '
        f'von {lo} links bis {hi} rechts; die Nummer am Trikot ist der Rang">'
    ]
    # Pitch markings - the frame the scale is read against, nothing else.
    p.append(
        f'<g class="pg">'
        f'<rect x="{pad}" y="{pad}" width="{w - 2 * pad}" height="{h - 2 * pad}" rx="12"/>'
        f'<rect x="{pad}" y="{cy - 100}" width="104" height="200"/>'
        f'<rect x="{w - pad - 104}" y="{cy - 100}" width="104" height="200"/>'
        f'<rect x="{pad}" y="{cy - 46}" width="36" height="92"/>'
        f'<rect x="{w - pad - 36}" y="{cy - 46}" width="36" height="92"/>'
        f'<circle cx="{cx:.1f}" cy="{cy}" r="62"/>'
        f"</g>"
    )
    p.append(
        f'<line class="axis" x1="{inner_l - 40}" y1="{cy}" x2="{inner_r + 40}" y2="{cy}"/>'
    )

    at = [x(t["form"]) for t in table]
    lane = lanes(at, TOKEN_GAP)
    for i, t in enumerate(table):
        dy = (0, -56, 56, -112, 112)[lane[i]]
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
        mark = '<div class="badge">Topspiel</div>' if i == top else ""
        body.append(
            f'<tr class="{"hl" if i == top else ""}">'
            f'<td class="l dt">{short_date(t["date"])}</td>'
            f'<td class="l fx">{mark}{sides}<div class="bar">{bar}</div></td>'
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

    return f"""
  <section class="fcast">
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
  </section>
"""


def predictor_section():
    """The measured table of things that ought to predict better. None does, and
    showing that is the honest way to publish a forecast at all."""
    body = []
    for r in compare():
        lead = ("<span class=\"flat\">Referenz</span>" if r["baseline"] else
                f'{num(r["lead"] * 1000, 1)} <span class="pm">± {num(r["se"] * 1000, 1)}</span>')
        body.append(f'<tr><td class="l">{r["name"]}</td>'
                    f'<td>{num(r["rps"], 4)}</td><td class="lead">{lead}</td></tr>')

    return f"""
  <section class="fcast">
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
    Zufall – rechnet man die Eichung strenger, tauschen die Zeilen die Plätze. Deshalb steht die
    Prognose oben als Blickwinkel da und nicht als Tipp, und deshalb bleibt die Form auf dieser
    Seite eine Beschreibung.</p>
  </section>
"""


def render(season, rows):
    table, matchdays = build_table(rows)
    matchday = matchdays[-1]
    last_date = max(r["date"] for r in rows)
    last_date = ".".join(reversed(last_date.split("-")))
    generated = date.today().strftime("%d.%m.%Y")

    top_team, bottom_team = table[0], table[-1]
    range_pts = top_team["form"] - bottom_team["form"]
    # The team the table is most wrong about right now - the whole reason the
    # page exists, so it gets named in the header.
    out_rank, out = max(enumerate(table), key=lambda p: abs(p[1]["position"] - (p[0] + 1)))
    out_diff = out["position"] - (out_rank + 1)
    plural = "Platz" if abs(out_diff) == 1 else "Plätze"
    if out_diff > 0:
        out_note = (f"Tabellenplatz {out['position']}, in Form aber {out_rank + 1}. – "
                    f"{abs(out_diff)} {plural} besser als die Tabelle vermuten lässt")
    elif out_diff < 0:
        out_note = (f"Tabellenplatz {out['position']}, in Form aber nur {out_rank + 1}. – "
                    f"{abs(out_diff)} {plural} schlechter als die Tabelle vermuten lässt")
    else:
        out_note = "Form und Tabelle sind überall deckungsgleich"
    out_cls = "up" if out_diff > 0 else ("down" if out_diff < 0 else "flat")

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
            badge = '<span class="badge">Mannschaft der Stunde</span>'
        elif rank == jump_rank:
            badge = '<span class="badge">Formsprung</span>'
        else:
            badge = ""

        body_rows.append(
            f'<tr data-rank="{rank}" tabindex="0" '
            f'style="--c:{PALETTE[rank % len(PALETTE)]};--i:{rank}">'
            f'<td class="rank">{tok}</td>'
            f'<td class="team"><div class="tc">{crest}<div>'
            f'<span class="tn">{html.escape(t["team"])}{badge}</span>'
            f'<span class="form"><span class="seg" aria-hidden="true">{segs}</span>'
            f'<span class="rt">{w}-{d}-{l}</span></span></div></div></td>'
            f'<td class="power"><div class="pw"><b>{num(t["form"])}</b></div></td>'
            f"<td>{delta}</td>"
            f'<td class="power season">{num(t["power"])}</td>'
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
    --up:#1c7a58; --down:#9c2f4a; --grey:#d7dde2; --mark:#9a5316;
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

  /* The pitch runs across the full page width - it is the one view that shows
     the whole league at once, and the tokens need the room. */
  .pitchsec {{ margin:0 0 30px; }}

  /* The dashboard below it: table on the left, the form chart on the right, so
     that clicking a team is visible in all three at once. */
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

  /* Editorial marker, deliberately in a colour no data uses: green and wine
     mean above and below average everywhere else on the page, and a badge is
     not a measurement. */
  .badge {{ display:inline-block; margin-left:7px; padding:1px 7px; border-radius:99px;
            font-size:10.5px; font-weight:700; letter-spacing:.05em; text-transform:uppercase;
            white-space:nowrap; vertical-align:1px;
            background:#fbeedd; color:var(--mark); border:1px solid #f0dcc2; }}

  /* ---- Forecast and the predictor comparison ------------------------------ */
  /* Capped rather than full-width: a six-column table stretched to 1760px reads
     as sparse, and the prose below keeps its own measure the same way. */
  .fcast {{ margin-top:34px; max-width:1080px; }}
  .fcast td.l {{ text-align:left; }}
  .fcast td.dt {{ color:var(--muted); font-size:13px; white-space:nowrap; }}
  .fcast td.p {{ width:64px; }}
  .fcast td.best {{ font-weight:700; }}
  .fcast td.xg, .fcast td.lead {{ color:var(--muted); white-space:nowrap; }}
  .fcast .pm {{ font-size:12.5px; }}
  .fcast tr.hl {{ background:#fdf7ef; }}
  .fcast tr.hl td:first-child {{ box-shadow:inset 3px 0 0 var(--mark); }}
  .fx .badge {{ margin:0 0 5px; }}
  .fxt {{ display:flex; align-items:center; gap:8px; line-height:1.3; }}
  .fxt + .fxt {{ margin-top:3px; }}
  .lg.sm {{ width:20px; height:20px; }}
  /* Same three colours as the result dots, and the same meaning: seen from the
     home side, win / draw / loss. */
  .bar {{ display:flex; height:6px; max-width:280px; margin-top:7px;
          border-radius:99px; overflow:hidden; background:var(--track); }}
  .bar i.w {{ background:var(--up); }}
  .bar i.d {{ background:#aeb9c2; }}
  .bar i.l {{ background:var(--down); }}

  /* ---- The pitch panel ---------------------------------------------------- */
  .pitchcol {{ background:var(--slate); border-radius:12px; padding:12px 14px 11px;
               background-image:repeating-linear-gradient(90deg,
                 rgba(255,255,255,.022) 0 40px, rgba(255,255,255,0) 40px 80px); }}
  .pwrap {{ position:relative; }}
  .pwrap svg {{ width:100%; height:auto; display:block; }}
  .pg rect, .pg circle {{ fill:none; stroke:rgba(255,255,255,.15); stroke-width:1.4; }}
  .axis {{ stroke:rgba(255,255,255,.17); stroke-width:1; stroke-dasharray:3 4; }}
  .ptok {{ cursor:pointer; }}
  .ptok path {{ stroke:var(--slate); stroke-width:2.5; stroke-linejoin:round; }}
  .ptok path.pos {{ fill:var(--up-l); }}
  .ptok path.neg {{ fill:var(--down-l); }}
  /* The crest sits on a white chest patch, so the shirt colour still reads as
     the above/below-average marker the legend explains. */
  .ptok circle.in {{ fill:#fff; }}
  .ptok circle.rkb {{ fill:var(--slate); stroke:#fff; stroke-width:1.5; }}
  .ptok text {{ fill:var(--slate); font-size:23px; font-weight:700; text-anchor:middle; }}
  .ptok text.rk {{ fill:#fff; font-size:12px; }}
  .ptok.sel path {{ stroke:#fff; stroke-width:3.5; }}
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
  /* The season score is context, not the headline: same column width, quieter. */
  td.season {{ color:var(--muted); font-weight:600; }}

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
    .dash {{ grid-template-columns:minmax(0,44fr) minmax(0,56fr); gap:28px; }}
    .cells {{ flex:2 1 640px; }}
  }}
  @media (max-width:1399px) {{
    /* Stacked: keep one comfortable measure instead of stretching to 1760px. */
    .dash, .pitchsec, .fcast {{ max-width:1080px; margin-left:auto; margin-right:auto; }}
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
    /* Six columns is already a lot on a phone: the season score is the one that
       can go, the form number and the table place carry the message. */
    td.season, th.season {{ display:none; }}
    /* Squeezed into a phone the full-width pitch would shrink the jerseys to a
       few pixels, so it keeps a readable width and scrolls sideways instead. */
    .pwrap {{ overflow-x:auto; }}
    .pwrap svg {{ min-width:700px; }}
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
        <p class="tag">Wer gerade gut spielt – gemessen an den letzten fünf Spieltagen und
        daran, gegen wen. Die Tabelle zeigt die Saison, diese Seite den Moment.</p>
      </div>
      <div class="md">
        <span class="k">Spieltag</span>
        <span class="n">{matchday}</span>
        <span class="dt">{last_date}</span>
      </div>
      <div class="cells">
        <div class="cell">
          <span class="k">Beste Form</span>
          <span class="v sm">{html.escape(top_team["team"])}</span>
          <span class="s">Form {num(top_team["form"])} · Tabellenplatz
          {top_team["position"]}</span>
        </div>
        <div class="cell">
          <span class="k">Die Tabelle täuscht am meisten bei</span>
          <span class="v sm {out_cls}">{html.escape(out["team"])}</span>
          <span class="s">{out_note}</span>
        </div>
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
        <p class="sub">Sortiert nach den letzten fünf Spieltagen, nicht nach der Saison.</p>
        <div class="card rank">
          <table>
            <thead>
              <tr>
                <th>#</th><th class="l">Team · S-U-N</th><th class="l">Form</th>
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
        <p class="hint"><strong>Form</strong> rechnet nur die letzten fünf Spieltage, dafür mit
        Gegnerstärke und Torverhältnis: 50 ist Ligadurchschnitt, darüber heißt besser als der
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

    <div class="col">
      <section>
        <h2>Formverlauf</h2>
        <p class="sub">Die Form an jedem Spieltag, also immer das Fenster der fünf davor.
        Diese Linien springen – das ist gewollt, sie zeigen Phasen und keine Bilanz.</p>
        <div class="card chart">{svg_chart(table, matchdays, "fseries", "form")}</div>
        <p class="hint">X-Achse: Spieltag, Y-Achse: Form. Eine Zeile in der Tabelle antippen
        hebt das Team hier und in der Aufstellung hervor.</p>
      </section>
    </div>
  </div>
{forecast_section(season, rows, logos, {t["team"]: t["form"] for t in table})}{predictor_section()}
  <div class="below">
  <h2>Was die Zahl kann – und was nicht</h2>
  <div class="note"><p><strong>Die Form beschreibt, sie sagt nichts vorher.</strong> Fünf Spiele
  sind viel zu wenig, um zu messen, wie stark ein Team wirklich ist – nachgerechnet an
  simulierten Saisons trifft ein Fenster dieser Länge die tatsächliche Stärke nur schwach. Wer
  hier oben steht, hat die letzten fünf Spieltage gut gespielt. Ob er sie auch nächste Woche gut
  spielt, steht hier nicht, und aus diesen Daten lässt es sich auch nicht sagen.</p></div>

  <div class="prose">
  <p>Drei Dinge, die man beim Lesen wissen sollte:</p>
  <p><strong>Die Form schwankt stark – das ist Absicht.</strong> Ein Team kann binnen zwei
  Spieltagen zehn Punkte gewinnen oder verlieren. Genau dafür ist die Spalte da: Sie soll zeigen,
  wer <em>gerade</em> läuft, und nicht den Saisonschnitt wiederholen. Wer den ruhigen Blick will,
  liest die Spalte <strong>Saison</strong> daneben oder gleich die offizielle Tabelle.</p>
  <p><strong>Ein großer Teil davon ist Zufall.</strong> Bei fünf Spielen entscheidet ein
  abgefälschter Ball über mehrere Punkte in dieser Wertung. Zwei Teams, die eng beieinander
  liegen, sind praktisch nicht zu unterscheiden – erst deutliche Abstände über mehrere Spieltage
  bedeuten etwas.</p>
  </div>

  <figure class="scale">
    <div class="track"><span class="occ" style="left:{bottom_team["form"]:.1f}%; width:{max(range_pts, 0.8):.1f}%"></span><span class="mid"></span></div>
    <div class="ends">
      <span>0</span>
      <span>Die ganze Liga in Form: <b>{num(bottom_team["form"])} – {num(top_team["form"])}</b></span>
      <span>100</span>
    </div>
  </figure>

  <div class="prose">
  <p><strong>Warum die Liga in dieser Wertung so weit auseinanderzieht.</strong> In dieser Liga
  enden 28,6 % der Spiele mit drei oder mehr Toren Unterschied – eine Simulation mit 14 exakt
  gleich starken Teams erzeugt allein durch Glück rund die Hälfte der Streuung, die real zu
  sehen ist. Über eine ganze Saison mittelt sich das weitgehend heraus, und die Spalte
  <em>Saison</em> dämpft zusätzlich zur Mitte hin. Über fünf Spiele passiert beides nicht: Die
  Form zeigt die Ausschläge, wie sie sind.</p>

  <h2>Wie gerechnet wird</h2>
  <p>Ein Elo-System, wie man es vom Schach kennt: Jedes Team startet bei 1500 Punkten, nach
  jedem Spiel gibt der Verlierer Punkte an den Sieger ab. Wie viele, hängt davon ab, wie
  überraschend das Ergebnis war und wie hoch gewonnen wurde. Heimvorteil ist eingerechnet
  (er ist in dieser Liga rund 100 Elo-Punkte wert, gemessen an der eigenen Saison, nicht
  geschätzt). Am Ende wird das Rating auf eine Skala von 0 bis 100 umgelegt, mit
  {num(POWER_SCALE_DIVISOR)} Elo-Punkten je Power-Punkt. Nur ausgetragene Spiele zählen;
  ungleiche Spielanzahl ist deshalb kein Problem.</p>

  <p><strong>Die Form rechnet genauso – nur mit kurzem Gedächtnis.</strong> Für die Spalte
  <em>Form</em> läuft dieselbe Elo-Rechnung, aber jeder Spieltag beginnt wieder bei 1500 und es
  zählen nur die letzten fünf Spieltage. Alles davor wird nicht schwächer gewichtet, sondern
  fällt ganz heraus. Genau das macht den Unterschied zur Spalte <em>Saison</em>: Dort hängt einem
  Team eine schwache Hinrunde bis zum letzten Spieltag an, hier ist sie nach fünf Spieltagen
  weg. Weil das Fenster so kurz ist, wird der Wert auch nicht zur Mitte hin gedämpft – sonst
  stünde die ganze Liga wieder bei 50 und die Spalte wäre sinnlos.</p>

  <p>Warum überhaupt rechnen und nicht einfach Punkte aus fünf Spielen zählen? Weil es einen
  Unterschied macht, gegen wen. Zwei Teams können beide zwölf von fünfzehn Punkten geholt
  haben – wer sie gegen die Spitze geholt hat, steht hier vorn.</p>
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
    print(write_report(args.season, load(args.season)))

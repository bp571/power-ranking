import os
import re

STAFFEL_NAME = "Kreisliga A Hunsrück-Mosel"

SEASON_CURRENT = "2026/27"
SEASON_PREVIOUS = "2025/26"

FUSSBALL_DE_BASE_URL = "https://www.fussball.de"

# The Staffel id, not the URL slug, decides which season a page shows. Ids come
# from the season dropdown on any Staffel page (select[name=saison]).
STAFFEL_IDS = {
    SEASON_CURRENT: "0316269OMO000004VS5489BTVU7GTVLE-G",
    SEASON_PREVIOUS: "02TI5U49B8000004VS5489BTVV9SFN07-G",
}

CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "raw")
# Club crests, unlike the match rows, are committed: the page embeds them, so
# they have to survive a fresh clone. Filled by src/logos.py.
LOGO_DIR = os.path.join(os.path.dirname(__file__), "..", "assets", "logos")
# fussball.de's getLogo endpoint sizes: 3=44px, 1=50px, 0=80px, 2=99px. 50px is
# twice the display size, so the crest stays sharp without bloating the page.
LOGO_FORMAT = 1
MATCHES_CSV = os.path.join(os.path.dirname(__file__), "..", "data", "matches.csv")
MANUAL_OVERRIDES_CSV = os.path.join(os.path.dirname(__file__), "..", "data", "manual_overrides.csv")

REQUEST_DELAY_SECONDS = 4
REQUEST_TIMEOUT_SECONDS = 20
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 (Power Ranking; contact: bene.peiter@gmail.com)"

# Model parameters. This is the only place they are defined; rating.py and
# score.py take them as defaults, so a backtest can still override per call.
#
# Backtested on 2025/26 (182 matches, scored from matchday 6 on, n=147):
#   RPS 0.2170 | table-position baseline 0.2178 | plain Elo 0.2178 | base rate 0.2209
# The model is NOT significantly better than the table baseline: the paired
# advantage is +0.00078 +/- 0.00171 (se), t = 0.46. K and the floor sit in a flat
# region where RPS cannot separate them - do not re-tune them on noise.
# Re-run with: python src/backtest.py
R0 = 1500  # starting rating, all teams, every season
K_BASE = 20  # RPS-flat over 10-25; also the peak of split-half reliability
HFA = 100  # backtest.estimate_hfa(): home score rate 0.6401 over 2025/26
SCALE = 400
MARGIN_MULTIPLIER_FLOOR = 0.8  # shallow optimum at 0.8-1.0

# Shrinkage, set from measured reliability rather than a rule of thumb. In this
# league roughly half the spread in end-of-season ratings is luck: simulating 14
# IDENTICAL teams over a full season produces an Elo SD of 35.6 against the 51.6
# actually observed, and split-half reliability is 0.58-0.61. Both imply N0 in
# the high teens to mid twenties, not the 5 assumed before measuring.
N0 = 20

# With N0=20 the shrunk ratings need a wider display scale to stay legible:
# 2025/26 finishes at 38.7-70.8 power points, while five matchdays into a season
# the whole league sits between 47.5 and 53.7 - correctly, because that early
# nothing is known yet. Fixed, never min-max, so scores compare across matchdays.
POWER_SCALE_DIVISOR = 3.0

# Matchdays behind the form rating, the page's headline number. Five is short
# enough to still be describing the present and long enough that one lucky
# afternoon does not own the column. It is a display choice, not a fitted one:
# no window this short can measure strength - against known true ratings the last
# five matchdays correlate r = 0.25 with the truth, the last thirteen r = 0.36.
# The form column describes what happened; the page says so in as many words.
FORM_WINDOW = 5

# Source spelling -> canonical name. fussball.de is consistent within a season,
# so this only needs entries for spellings that differ between seasons.
TEAM_ALIASES = {}

# Validation set per season. A season that is not listed is only checked
# structurally, since promotion and relegation change the roster every year.
KNOWN_TEAMS = {
    SEASON_CURRENT: {
        "SC Weiler",
        "SG Braunshorn",
        "SG Bremm",
        "SG Dickenschied",
        "SG Rheinblick Oberwesel",
        "SG Vorderhunsrück Sabershausen",
        "SG Werlau",
        "SG Zell",
        "SSV Boppard",
        "SV Blankenrath",
        "SV Strimmig",
        "TuS Kirchberg II",
        "TuS Rheinböllen",
        "VfB Baybachhöhe",
    },
}


def matchday_url(season: str, matchday: int) -> str:
    """Schedule page for one matchday. The slug is optional and omitted."""
    return f"{FUSSBALL_DE_BASE_URL}/spieltag/-/spieltag/{matchday}/staffel/{STAFFEL_IDS[season]}"


def canonical_team(name: str) -> str:
    return TEAM_ALIASES.get(" ".join(name.split()), " ".join(name.split()))


UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def team_slug(name: str) -> str:
    """Logo file name for a team. ASCII only, so the repo stays portable."""
    slug = canonical_team(name).lower().translate(UMLAUTS)
    return re.sub(r"[^a-z0-9]+", "-", slug).strip("-")

"""Download the club crests into assets/logos/. Network only, run by hand.

Not part of run.py: a club's crest does not change, so this only has anything to
do once the league gains a team. The URLs are already in the cached matchday
pages, so a rerun costs one request per missing crest and nothing else.
"""

import html
import logging
import re
import sys
import time
from pathlib import Path

from config import (
    CACHE_DIR,
    KNOWN_TEAMS,
    LOGO_DIR,
    LOGO_FORMAT,
    REQUEST_DELAY_SECONDS,
    SEASON_CURRENT,
    canonical_team,
    team_slug,
)
from scrape import _get_with_retry

logger = logging.getLogger(__name__)

# Club crests carry a /verband/ suffix; the association and region logos in the
# page header use the same endpoint without one and are filtered out by it.
RE_LOGO = re.compile(
    r'data-alt="([^"]+)"\s+data-responsive-image="(//[^"]+/getLogo/[^"]+/verband/[^"]+)"'
)
RE_FORMAT = re.compile(r"/format/\d+/")


def logo_urls(season: str) -> dict:
    """Team -> crest URL, read off the cached matchday pages of that season."""
    pattern = f"staffel_{season.replace('/', '-')}_*.html"
    urls = {}
    for page in sorted(Path(CACHE_DIR).glob(pattern)):
        for alt, url in RE_LOGO.findall(page.read_text(encoding="utf-8")):
            team = canonical_team(html.unescape(alt))
            urls[team] = "https:" + RE_FORMAT.sub(f"/format/{LOGO_FORMAT}/", url)
    if not urls:
        raise ValueError(f"No crest URLs in {CACHE_DIR} for {season} - scrape it first")
    return urls


def download(season: str = SEASON_CURRENT) -> None:
    directory = Path(LOGO_DIR)
    directory.mkdir(parents=True, exist_ok=True)

    urls = logo_urls(season)
    missing = KNOWN_TEAMS.get(season, set()) - urls.keys()
    if missing:
        logger.warning(f"No crest URL for: {sorted(missing)}")

    fetched = 0
    for team, url in sorted(urls.items()):
        path = directory / f"{team_slug(team)}.png"
        if path.exists():
            continue
        response = _get_with_retry(url)
        if not response.content.startswith(b"\x89PNG"):
            logger.warning(f"{team}: response is not a PNG, skipping")
            continue
        path.write_bytes(response.content)
        fetched += 1
        logger.info(f"{team} -> {path.name} ({len(response.content) // 1024} KB)")
        time.sleep(REQUEST_DELAY_SECONDS)

    logger.info(f"{fetched} crests fetched, {len(urls) - fetched} already present")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    download(sys.argv[1] if len(sys.argv) > 1 else SEASON_CURRENT)

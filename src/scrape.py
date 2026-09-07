"""Fetch the Staffel matchday pages. Network only, no parsing."""

import logging
import re
import time
from pathlib import Path

import requests

from config import (
    CACHE_DIR,
    REQUEST_DELAY_SECONDS,
    REQUEST_TIMEOUT_SECONDS,
    USER_AGENT,
    matchday_url,
)

logger = logging.getLogger(__name__)

RE_MATCHDAY_OPTION = re.compile(r"/spieltag/(\d+)/staffel/")

# Only used to give up if not even one page can be fetched; the real matchday
# count is read off the page itself.
MAX_MATCHDAYS = 40

RETRY_PASS_DELAY_SECONDS = 60


def _get_with_retry(url: str, attempts: int = 3):
    """fussball.de returns a sporadic 503 during a season walk; one retry after
    a pause is enough, and losing the whole run to it is not acceptable."""
    for attempt in range(1, attempts + 1):
        response = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if response.ok:
            return response
        if attempt == attempts:
            response.raise_for_status()
        wait = REQUEST_DELAY_SECONDS * 2**attempt
        logger.warning(f"HTTP {response.status_code}, retrying in {wait}s")
        time.sleep(wait)


def _cache_path(season: str, matchday: int) -> Path:
    return Path(CACHE_DIR) / f"staffel_{season.replace('/', '-')}_md{matchday:02d}.html"


def fetch_matchday(season: str, matchday: int, use_cache: bool = False) -> str:
    """Fetch one matchday page. Every response is written to data/raw/ so the
    parser can be re-run offline and layout breakage stays forensically visible."""
    cache_file = _cache_path(season, matchday)
    if use_cache and cache_file.exists():
        return cache_file.read_text(encoding="utf-8")

    logger.info(f"GET {season} matchday {matchday}")
    response = _get_with_retry(matchday_url(season, matchday))
    response.encoding = "utf-8"

    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(response.text, encoding="utf-8")
    time.sleep(REQUEST_DELAY_SECONDS)
    return response.text


def _try_fetch(season: str, matchday: int, use_cache: bool):
    try:
        return fetch_matchday(season, matchday, use_cache)
    except requests.HTTPError as e:
        logger.warning(f"{season} matchday {matchday} refused: {e}")
        return None


def count_matchdays(html: str) -> int:
    """Number of matchdays, read off the matchday dropdown of any page."""
    numbers = {int(n) for n in RE_MATCHDAY_OPTION.findall(html)}
    if not numbers:
        raise ValueError("No matchday options found - page layout changed?")
    return max(numbers)


def fetch_season(season: str, use_cache: bool = False):
    """Yield (matchday, html) for every matchday of the season. A matchday the
    server refuses to serve yields None so the rest of the season still runs."""
    total = None
    failed = []
    matchday = 1
    while total is None or matchday <= total:
        html = _try_fetch(season, matchday, use_cache)
        if html is None:
            failed.append(matchday)
            if matchday >= MAX_MATCHDAYS:
                raise RuntimeError(f"{season}: no matchday page could be fetched")
        else:
            if total is None:
                total = count_matchdays(html)
                logger.info(f"Season {season}: {total} matchdays")
            yield matchday, html
        matchday += 1

    # fussball.de sheds load during a season walk. Give the backend a breather
    # and come back for the pages it refused, rather than reporting a false gap.
    if failed:
        logger.info(f"Retrying {len(failed)} refused matchdays after {RETRY_PASS_DELAY_SECONDS}s")
        time.sleep(RETRY_PASS_DELAY_SECONDS)
        for matchday in failed:
            html = _try_fetch(season, matchday, use_cache)
            yield matchday, html
            if html is None:
                logger.error(f"{season} matchday {matchday} still unavailable")

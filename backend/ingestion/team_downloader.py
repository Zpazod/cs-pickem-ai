from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable
import re
from urllib.parse import quote, urlparse

import requests
from requests import RequestException
from bs4 import BeautifulSoup
from sqlalchemy import select

from backend.config import RAW_DATA_DIR, ensure_project_dirs
from backend.database.models import Match
from backend.database.session import SessionLocal, init_db
from backend.ingestion.hltv_downloader import DEFAULT_HEADERS, _filename_for_match_url, download_match_page
from backend.ingestion.hltv_parser import parse_match_html_file
from backend.models.recent_form import WINDOWS


def _absolute_match_url(href: str) -> str:
    if href.startswith("/"):
        return f"https://www.hltv.org{href}"
    return href


def _collect_match_links_from_team_page(html: str, team_name: str | None = None, days: int | None = None) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    normalized_team = team_name.strip().lower() if team_name else None

    seen = set()
    urls: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].split("#")[0]
        if not re.search(r"/matches/\d+", href) or href.endswith("/stats"):
            continue
        
        # Skip if marked as future/upcoming/TBD
        if _is_future_or_upcoming_match(a):
            continue

        url = _absolute_match_url(href)
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)

    return urls


def _extract_match_link_date(node) -> datetime | None:
    for candidate in [node] + list(node.parents):
        unix_value = candidate.get("data-unix")
        if not unix_value:
            continue
        try:
            return datetime.fromtimestamp(int(unix_value) / 1000)
        except (TypeError, ValueError):
            continue
    return None


def _extract_hltv_match_id(url: str) -> int | None:
    match = re.search(r"/matches/(\d+)/", url)
    return int(match.group(1)) if match else None


def _is_match_already_downloaded(url: str, output_dir: Path) -> bool:
    raw_path = output_dir / _filename_for_match_url(url)
    if raw_path.exists():
        return True

    match_id = _extract_hltv_match_id(url)
    if match_id is None:
        return False

    try:
        init_db()
        with SessionLocal() as session:
            existing = session.scalar(select(Match).where(Match.hltv_match_id == match_id))
            if existing:
                return True
    except Exception:
        pass

    return False


def _is_future_or_upcoming_match(node) -> bool:
    text = node.get_text(" ", strip=True)
    # Check for TBD, upcoming, future, next, scheduled, preview, live (without result/finished)
    if re.search(r"\b(tbd|upcoming|future|next|scheduled|preview)\b", text, re.I):
        return True
    if re.search(r"\blive\b", text, re.I) and not re.search(r"\b(result|finished|past)\b", text, re.I):
        return True

    for candidate in [node] + list(node.parents):
        classes = candidate.get("class", []) or []
        class_text = " ".join(classes)
        if re.search(r"\b(tbd|upcoming|future|next|scheduled|preview)\b", class_text, re.I):
            return True
        if re.search(r"\blive\b", class_text, re.I) and not re.search(r"\b(result|finished|past)\b", class_text, re.I):
            return True

    return False


def _slugify_name(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name.strip().lower())
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or name.strip().lower()


def _resolve_team_id(team: str) -> str:
    if team.isdigit():
        return team
    if team.startswith("http"):
        parsed = urlparse(team)
        match = re.search(r"/team/(\d+)", parsed.path)
        if match:
            return match.group(1)
    team_url = _resolve_team_url_from_search(team)
    if team_url is None:
        raise ValueError(
            f"Could not resolve HLTV team name '{team}'. Use the team ID or a full HLTV team URL."
        )
    match = re.search(r"/team/(\d+)", team_url)
    if not match:
        raise ValueError(
            f"Could not resolve HLTV team name '{team}'. Use the team ID or a full HLTV team URL."
        )
    return match.group(1)


def _resolve_team_url(team: str) -> str:
    if team.startswith("http"):
        parsed = urlparse(team)
        match = re.match(r"^/team/(\d+)/([^/?#]+)/?$", parsed.path)
        if not match:
            raise ValueError(
                "Team URL must be a canonical HLTV team page in the form: "
                "https://www.hltv.org/team/<id>/<slug>"
            )
        team_id = match.group(1)
        slug = match.group(2)
        return f"https://www.hltv.org/team/{team_id}/{slug}"

    if team.isdigit():
        return f"https://www.hltv.org/team/{team}"

    team_url = _resolve_team_url_from_search(team)
    if team_url is None:
        raise ValueError(
            f"Could not resolve HLTV team name '{team}'. Use the team ID or a full HLTV team page URL."
        )
    return team_url


def _resolve_team_results_url(team: str, window: str | int | None) -> str:
    return _resolve_team_url(team)


def _resolve_team_url_from_search(team: str) -> str | None:
    search_url = f"https://www.hltv.org/search?query={quote(team)}"
    try:
        resp = requests.get(search_url, headers=DEFAULT_HEADERS, timeout=30)
        if resp.status_code in {403, 429}:
            html = _download_with_playwright_for_listing(search_url, timeout_seconds=30)
        else:
            resp.raise_for_status()
            html = resp.text
    except RequestException:
        html = _download_with_playwright_for_listing(search_url, timeout_seconds=30)

    soup = BeautifulSoup(html, "html.parser")
    candidates: list[tuple[str, str]] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        match = re.match(r"^/team/(\d+)(?:/([^/?#]+))?", href)
        if match:
            team_id = match.group(1)
            text = a.get_text(" ", strip=True)
            candidates.append((team_id, text))

    if not candidates:
        html = _download_with_playwright_for_listing(search_url, timeout_seconds=30)
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            match = re.match(r"^/team/(\d+)(?:/([^/?#]+))?", href)
            if match:
                team_id = match.group(1)
                text = a.get_text(" ", strip=True)
                candidates.append((team_id, text))

    if not candidates:
        return None

    normalized = team.strip().lower()
    for team_id, text in candidates:
        if text.strip().lower() == normalized:
            return f"https://www.hltv.org/team/{team_id}/{_slugify_name(text)}"

    team_id, text = candidates[0]
    return f"https://www.hltv.org/team/{team_id}/{_slugify_name(text)}"


def download_team_matches(
    team: str,
    window: str | int | None = "90d",
    max_matches: int | None = None,
    output_dir: Path | None = None,
    progress: Callable[[int, int, int], None] | None = None,
) -> list[Path]:
    """Download recent matches for a team.

    Args:
        team: either an HLTV team id (digits) or a HLTV team/matches page URL.
        window: either a window label like '90d' or an integer number of days, or None for all-time.
        max_matches: optional limit of matches to download.
        output_dir: folder to save raw HTML files (defaults to RAW_DATA_DIR).

    Returns list of downloaded file paths.
    """
    ensure_project_dirs()
    output_dir = output_dir or RAW_DATA_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    # Normalize window
    if isinstance(window, str) and window.isdigit():
        window = int(window)
    if isinstance(window, str) and window in WINDOWS:
        days = WINDOWS[window]
    elif isinstance(window, int):
        days = window
    else:
        days = None

    # Build results listing URL or resolve the team ID from a name/slug.
    list_url = _resolve_team_results_url(team, window)
    
    # Extract team name for match verification (e.g., from https://www.hltv.org/team/9565/vitality -> vitality)
    team_display_name = None
    parsed = urlparse(list_url)
    parts = [p for p in parsed.path.split("/") if p]  # Filter empty strings
    if len(parts) >= 3 and parts[0] == "team":
        team_display_name = parts[2]  # Skip "team" and team_id to get slug

    # Results pages are often rendered or updated by JS, so use browser rendering.
    try:
        resp = requests.get(list_url, headers=DEFAULT_HEADERS, timeout=30)
        if resp.status_code in {403, 429}:
            html = _download_with_playwright_for_listing(list_url, timeout_seconds=30)
        else:
            resp.raise_for_status()
            html = resp.text
    except RequestException:
        html = _download_with_playwright_for_listing(list_url, timeout_seconds=30)

    match_urls = _collect_match_links_from_team_page(html, team_name=team_display_name, days=days)
    if not match_urls:
        html = _download_with_playwright_for_listing(list_url, timeout_seconds=30)
        match_urls = _collect_match_links_from_team_page(html, team_name=team_display_name, days=days)

    downloaded_paths: list[Path] = []
    now = datetime.utcnow()
    total_urls = len(match_urls)
    processed = 0
    success_count = 0
    
    # Normalize team name for verification
    normalized_query_team = (team_display_name.lower() if team_display_name else team.lower())

    for url in match_urls:
        processed += 1
        if max_matches is not None and success_count >= max_matches:
            break

        if _is_match_already_downloaded(url, output_dir):
            expected_path = output_dir / _filename_for_match_url(url)
            if expected_path.exists():
                downloaded_paths.append(expected_path)
                success_count += 1
            if progress:
                progress(processed, total_urls, success_count)
            continue

        try:
            downloaded = download_match_page(url, output_dir=output_dir)
            download_ok = True
        except Exception:
            download_ok = False
            downloaded = None

        if download_ok and downloaded is not None:
            try:
                parsed = parse_match_html_file(downloaded.path, source_url=url)
                played_at = parsed.played_at
                team1 = parsed.team1.lower() if parsed.team1 else ""
                team2 = parsed.team2.lower() if parsed.team2 else ""
                
                # Reject if no date or future match
                if played_at is None or played_at > now:
                    try:
                        downloaded.path.unlink()
                    except Exception:
                        pass
                    download_ok = False
                # Verify team is in the match
                elif normalized_query_team not in team1 and normalized_query_team not in team2:
                    try:
                        downloaded.path.unlink()
                    except Exception:
                        pass
                    download_ok = False
                # Apply window filter (only if days is specified)
                elif days is not None:
                    cutoff = now - timedelta(days=days + 1)
                    if played_at < cutoff:
                        try:
                            downloaded.path.unlink()
                        except Exception:
                            pass
                        download_ok = False
            except Exception:
                pass

        if download_ok and downloaded is not None:
            downloaded_paths.append(downloaded.path)
            success_count += 1

        if progress:
            progress(processed, total_urls, success_count)

    return downloaded_paths


def _download_with_playwright_for_listing(url: str, timeout_seconds: int = 30) -> str:
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "HLTV blocked the simple HTTP downloader. Install the browser downloader with:\n"
            '  .\\.venv\\Scripts\\python.exe -m pip install -e " .[browser]"\n'
            "  .\\.venv\\Scripts\\python.exe -m playwright install chromium"
        ) from exc

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page(user_agent=DEFAULT_HEADERS["User-Agent"], locale="en-US")
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_seconds * 1000)
                try:
                    page.wait_for_load_state("networkidle", timeout=5000)
                except Exception:
                    page.wait_for_timeout(3000)
                try:
                    page.wait_for_selector("a[href*='/matches/']", timeout=5000)
                except Exception:
                    page.wait_for_timeout(2000)
                return page.content()
            finally:
                browser.close()
    except PlaywrightError as exc:
        raise RuntimeError(
            "Could not download HLTV listing with Playwright. If this is the first browser run, install Chromium with:\n"
            "  .\\.venv\\Scripts\\python.exe -m playwright install chromium"
        ) from exc

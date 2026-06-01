from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import requests
from requests import RequestException

from backend.config import RAW_DATA_DIR, ensure_project_dirs


DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


@dataclass(frozen=True)
class DownloadedMatchPage:
    url: str
    path: Path
    html: str


def download_match_page(url: str, output_dir: Path | None = None, timeout_seconds: int = 30) -> DownloadedMatchPage:
    """Download a HLTV match page and persist the raw HTML for reproducible parsing."""
    ensure_project_dirs()
    _validate_hltv_match_url(url)
    output_dir = output_dir or RAW_DATA_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    html = _download_with_requests(url, timeout_seconds)
    if html is None:
        html = _download_with_playwright(url, timeout_seconds)
    if "teamName" not in html and "match-page" not in html and "Match stats" not in html:
        raise ValueError("Downloaded page does not look like a HLTV match page.")

    path = output_dir / _filename_for_match_url(url)
    path.write_text(html, encoding="utf-8")
    return DownloadedMatchPage(url=url, path=path, html=html)


def _download_with_requests(url: str, timeout_seconds: int) -> str | None:
    try:
        response = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout_seconds)
        if response.status_code in {403, 429}:
            return None
        response.raise_for_status()
        return response.text
    except RequestException:
        return None


def _download_with_playwright(url: str, timeout_seconds: int) -> str:
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "HLTV blocked the simple HTTP downloader. Install the browser downloader with:\n"
            '  .\\.venv\\Scripts\\python.exe -m pip install -e ".[browser]"\n'
            "  .\\.venv\\Scripts\\python.exe -m playwright install chromium"
        ) from exc

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page(user_agent=DEFAULT_HEADERS["User-Agent"], locale="en-US")
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_seconds * 1000)
                page.wait_for_selector(".teamName, .match-page, #match-stats", timeout=timeout_seconds * 1000)
                return page.content()
            finally:
                browser.close()
    except PlaywrightError as exc:
        raise RuntimeError(
            "Could not download HLTV with Playwright. If this is the first browser run, install Chromium with:\n"
            "  .\\.venv\\Scripts\\python.exe -m playwright install chromium"
        ) from exc


def _validate_hltv_match_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("HLTV match URL must start with http:// or https://.")
    if parsed.netloc.lower() not in {"www.hltv.org", "hltv.org"}:
        raise ValueError("Only hltv.org match URLs are supported.")
    if not re.search(r"^/matches/\d+/", parsed.path):
        raise ValueError("URL must look like https://www.hltv.org/matches/{id}/{slug}.")


def _filename_for_match_url(url: str) -> str:
    parsed = urlparse(url)
    match = re.search(r"/matches/(\d+)/([^/?#]+)", parsed.path)
    if not match:
        return "hltv_match.html"
    match_id, slug = match.groups()
    safe_slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", slug).strip("-").lower()
    return f"hltv_match_{match_id}_{safe_slug}.html"

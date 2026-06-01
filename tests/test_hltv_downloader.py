from pathlib import Path

import pytest

from backend.ingestion import hltv_downloader


class FakeResponse:
    status_code = 200
    text = '<html><div class="teamName">A</div><div class="teamName">B</div><div>Match stats</div></html>'

    def raise_for_status(self):
        return None


def test_download_match_page_saves_raw_html(monkeypatch, tmp_path: Path):
    calls = {}

    def fake_get(url, headers, timeout):
        calls["url"] = url
        calls["headers"] = headers
        calls["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(hltv_downloader.requests, "get", fake_get)

    downloaded = hltv_downloader.download_match_page(
        "https://www.hltv.org/matches/2394174/natus-vincere-vs-vitality-iem-atlanta-2026",
        output_dir=tmp_path,
    )

    assert calls["url"].endswith("/matches/2394174/natus-vincere-vs-vitality-iem-atlanta-2026")
    assert downloaded.path.exists()
    assert downloaded.path.name == "hltv_match_2394174_natus-vincere-vs-vitality-iem-atlanta-2026.html"
    assert downloaded.path.read_text(encoding="utf-8") == FakeResponse.text


def test_download_match_page_falls_back_to_playwright(monkeypatch, tmp_path: Path):
    class ForbiddenResponse:
        status_code = 403
        text = ""

        def raise_for_status(self):
            return None

    monkeypatch.setattr(hltv_downloader.requests, "get", lambda *args, **kwargs: ForbiddenResponse())
    monkeypatch.setattr(
        hltv_downloader,
        "_download_with_playwright",
        lambda url, timeout_seconds: FakeResponse.text,
    )

    downloaded = hltv_downloader.download_match_page(
        "https://www.hltv.org/matches/2394174/natus-vincere-vs-vitality-iem-atlanta-2026",
        output_dir=tmp_path,
    )

    assert downloaded.path.exists()
    assert "teamName" in downloaded.html


def test_download_match_page_rejects_non_hltv_urls(tmp_path: Path):
    with pytest.raises(ValueError, match="Only hltv.org"):
        hltv_downloader.download_match_page("https://example.com/matches/1/test", output_dir=tmp_path)

# CS2 Pick'Em AI MVP

Local-first MVP for CS2 Major Pick'Ems:

- store teams, players, events, matches, maps, and per-map player stats in SQLite;
- ingest HLTV match pages from a URL while keeping the raw HTML locally;
- calculate baseline Elo ratings;
- predict match win probability;
- run Swiss Monte Carlo simulations;
- optimize Pick'Em choices for `P(score >= 5)`.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

HLTV may block simple HTTP downloads. Install the optional browser downloader once:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[browser]"
.\.venv\Scripts\python.exe -m playwright install chromium
```

## CLI

```powershell
cs2-pickem init-db
cs2-pickem import-match-url "https://www.hltv.org/matches/2394174/natus-vincere-vs-vitality-iem-atlanta-2026"
cs2-pickem import-match .\data\raw\sample_match.html --source-url "https://www.hltv.org/matches/example"
cs2-pickem team-history "Natus Vincere"
cs2-pickem predict "Natus Vincere" "Vitality"
cs2-pickem simulate-swiss .\data\processed\stage_teams.json --sims 10000
```

## API

```powershell
uvicorn backend.main:app --reload --port 8000
```

Then open `http://127.0.0.1:8000/docs`.

## Notes

`import-match-url` first tries a simple HTTP download. If HLTV blocks it, the downloader falls back to Playwright/Chromium. The raw HTML is always saved in `data/raw/`, then parsed and imported. Keep every raw source so parser improvements can reprocess old matches without scraping again.

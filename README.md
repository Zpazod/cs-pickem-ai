# CS2 Pick'Em AI MVP

Local-first MVP for CS2 Major Pick'Ems:

- store teams, players, events, matches, maps, and per-map player stats in SQLite;
- ingest HLTV match pages from a URL while keeping the raw HTML locally;
- calculate baseline Elo ratings;
- import HLTV/VRS ranking snapshots and blend them with Elo;
- keep roster/status history for active, stand-in, loan, bench, free-agent, and retired states;
- persist advanced player/map/side stat fields for future modeling;
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
cs2-pickem import-rankings .\data\processed\cologne_2026_stage1_rankings.example.json
cs2-pickem strengths
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

Ranking imports are local JSON snapshots. The example Cologne 2026 file uses illustrative values only; replace them with real HLTV/VRS values before trusting recommendations.

Prediction strength now heavily favors official sources when available:

- Elo: 10%
- HLTV: 45%
- VRS: 45%

If HLTV or VRS is missing, the available weights are renormalized. If both official sources are missing, the system falls back to Elo only.

The database already has fields/tables for future data work:

- player status history: `active`, `standin`, `loan`, `bench`, `free_agent`, `retired`;
- roster memberships with start/end dates and event-specific status;
- player map stats by side (`both`, later `t`/`ct`) with swing, DPR, KPR, multi-kill, firepower, entrying, trading, clutching, utility, sniping, opening, clutch and utility fields;
- team map snapshots for win rate, T/CT round win rate, pick rate, and ban rate.

Roster changes are not yet scored in predictions, but the schema now preserves the history needed for later model features such as stand-in penalty, roster stability, and recent-form adjustment.

# CS2 Pick'Em AI MVP

Local-first MVP for CS2 Major Pick'Ems:

- store teams, players, events, matches, maps, and per-map player stats in SQLite;
- ingest saved HLTV-like match HTML files;
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

## CLI

```powershell
cs2-pickem init-db
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

The HLTV parser starts deliberately small and expects saved HTML files first. Keep every raw source in `data/raw/` so parser improvements can reprocess old matches without scraping again.


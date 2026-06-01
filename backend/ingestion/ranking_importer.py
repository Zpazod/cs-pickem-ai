from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.database.models import Team, TeamRanking


VALID_RANKING_SOURCES = {"hltv", "vrs"}


@dataclass(frozen=True)
class RankingImportResult:
    inserted: int
    updated: int


def import_rankings_file(session: Session, path: str | Path) -> RankingImportResult:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Ranking file must contain a JSON list.")

    inserted = 0
    updated = 0
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("Each ranking entry must be a JSON object.")
        was_inserted = import_ranking_entry(session, item)
        if was_inserted:
            inserted += 1
        else:
            updated += 1

    session.commit()
    return RankingImportResult(inserted=inserted, updated=updated)


def import_ranking_entry(session: Session, item: dict) -> bool:
    team_name = _required_str(item, "team")
    source = _required_str(item, "source").lower()
    if source not in VALID_RANKING_SOURCES:
        raise ValueError(f"Unsupported ranking source: {source}")
    ranking_date = _parse_date(_required_str(item, "ranking_date"))

    team = _get_or_create_team(session, team_name)
    ranking = session.scalar(
        select(TeamRanking).where(
            TeamRanking.team_id == team.id,
            TeamRanking.source == source,
            TeamRanking.ranking_date == ranking_date,
        )
    )

    if ranking is None:
        ranking = TeamRanking(team=team, source=source, ranking_date=ranking_date)
        session.add(ranking)
        inserted = True
    else:
        inserted = False

    ranking.rank = _optional_int(item.get("rank"))
    ranking.points = _optional_float(item.get("points"))
    ranking.region = item.get("region")
    ranking.updated_at = datetime.utcnow()
    session.flush()
    return inserted


def _get_or_create_team(session: Session, name: str) -> Team:
    team = session.scalar(select(Team).where(Team.name == name))
    if team:
        return team
    team = Team(name=name)
    session.add(team)
    session.flush()
    return team


def _required_str(item: dict, key: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Ranking entry is missing required string field: {key}")
    return value.strip()


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def _optional_int(value) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _optional_float(value) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


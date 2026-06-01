from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.database.models import Match, Team
from backend.database.session import SessionLocal, init_db
from backend.models.elo import EloSystem
from backend.models.team_strength import TeamStrengthModel

app = FastAPI(title="CS2 Pick'Em AI MVP")


def get_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/teams")
def list_teams(session: Session = Depends(get_session)) -> list[dict[str, object]]:
    teams = session.scalars(select(Team).order_by(Team.name)).all()
    return [{"id": team.id, "name": team.name} for team in teams]


@app.get("/teams/{team_name}/history")
def team_history(team_name: str, session: Session = Depends(get_session)) -> list[dict[str, object]]:
    team = session.scalar(select(Team).where(Team.name == team_name))
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    matches = session.scalars(
        select(Match)
        .where((Match.team1_id == team.id) | (Match.team2_id == team.id))
        .order_by(Match.played_at.desc().nulls_last(), Match.id.desc())
    ).all()
    return [
        {
            "id": match.id,
            "opponent": match.team2.name if match.team1_id == team.id else match.team1.name,
            "winner": match.winner.name if match.winner else None,
            "format": match.format,
            "maps": [m.map_name for m in match.maps],
        }
        for match in matches
    ]


@app.get("/predictions/match")
def predict_match(team1: str, team2: str, bo3: bool = False, session: Session = Depends(get_session)) -> dict[str, object]:
    try:
        EloSystem().rebuild_from_matches(session)
        prediction = TeamStrengthModel().predict(session, team1, team2, bo3=bo3)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return prediction.__dict__

from __future__ import annotations

import json
from pathlib import Path

import typer
from sqlalchemy import select

from backend.database.models import MapPlayed, Match, Player, PlayerMapStat, Team, TeamRating
from backend.database.session import SessionLocal, init_db as create_tables
from backend.ingestion.hltv_parser import parse_match_html_file
from backend.ingestion.importer import import_parsed_match
from backend.models.elo import EloSystem
from backend.pickem.optimizer import DiamondCoinOptimizer
from backend.simulation.swiss import SwissMonteCarlo

app = typer.Typer(help="CS2 Pick'Em AI MVP CLI")


@app.command("init-db")
def init_db() -> None:
    create_tables()
    typer.echo("SQLite database initialized.")


@app.command("import-match")
def import_match(path: Path, source_url: str | None = None) -> None:
    create_tables()
    parsed = parse_match_html_file(path, source_url=source_url)
    with SessionLocal() as session:
        match = import_parsed_match(session, parsed)
    typer.echo(f"Imported match #{match.id}: {parsed.team1} vs {parsed.team2}")


@app.command("rebuild-elo")
def rebuild_elo() -> None:
    create_tables()
    with SessionLocal() as session:
        ratings = EloSystem().rebuild_from_matches(session)
    typer.echo(f"Rebuilt Elo for {len(ratings)} teams.")


@app.command("predict")
def predict(team1: str, team2: str, bo3: bool = False) -> None:
    create_tables()
    with SessionLocal() as session:
        EloSystem().rebuild_from_matches(session)
        prediction = EloSystem().predict(session, team1, team2, bo3=bo3)
    typer.echo(json.dumps(prediction.__dict__, indent=2))


@app.command("team-history")
def team_history(team: str) -> None:
    create_tables()
    with SessionLocal() as session:
        team_row = session.scalar(select(Team).where(Team.name == team))
        if not team_row:
            raise typer.BadParameter(f"Unknown team: {team}")
        matches = session.scalars(
            select(Match)
            .where((Match.team1_id == team_row.id) | (Match.team2_id == team_row.id))
            .order_by(Match.played_at.desc().nulls_last(), Match.id.desc())
        ).all()
        payload = [
            {
                "id": match.id,
                "opponent": match.team2.name if match.team1_id == team_row.id else match.team1.name,
                "winner": match.winner.name if match.winner else None,
                "format": match.format,
                "maps": [m.map_name for m in match.maps],
            }
            for match in matches
        ]
    typer.echo(json.dumps(payload, indent=2))


@app.command("simulate-swiss")
def simulate_swiss(teams_json: Path, sims: int = 10000) -> None:
    create_tables()
    teams = json.loads(teams_json.read_text(encoding="utf-8"))
    if not isinstance(teams, list):
        raise typer.BadParameter("teams_json must contain a JSON list of team names.")
    with SessionLocal() as session:
        EloSystem().rebuild_from_matches(session)
        rating_rows = session.execute(select(Team, TeamRating).join(TeamRating, Team.id == TeamRating.team_id)).all()
        ratings_by_team = {team.name: rating.rating for team, rating in rating_rows}
        elo = EloSystem()

        def win_probability(team_a: str, team_b: str, is_bo3: bool) -> float:
            rating_a = ratings_by_team.get(team_a, elo.base_rating)
            rating_b = ratings_by_team.get(team_b, elo.base_rating)
            probability = elo.expected_score(rating_a, rating_b)
            return elo.bo3_probability(probability) if is_bo3 else probability

        probs = SwissMonteCarlo(teams, win_probability, n_sims=sims).simulate_all()
        picks = DiamondCoinOptimizer(probs, teams).optimize()
    typer.echo(json.dumps({"probabilities": probs, "recommended_picks": picks}, indent=2))


@app.command("player-history")
def player_history(player: str) -> None:
    create_tables()
    with SessionLocal() as session:
        player_row = session.scalar(select(Player).where(Player.nickname == player))
        if not player_row:
            raise typer.BadParameter(f"Unknown player: {player}")
        rows = session.execute(
            select(PlayerMapStat, MapPlayed, Match)
            .join(MapPlayed, PlayerMapStat.map_id == MapPlayed.id)
            .join(Match, MapPlayed.match_id == Match.id)
            .where(PlayerMapStat.player_id == player_row.id)
        ).all()
        payload = [
            {
                "match_id": match.id,
                "map": map_played.map_name,
                "team": stat.team.name,
                "kills": stat.kills,
                "deaths": stat.deaths,
                "assists": stat.assists,
                "adr": stat.adr,
                "kast": stat.kast,
                "rating_2": stat.rating_2,
            }
            for stat, map_played, match in rows
        ]
    typer.echo(json.dumps(payload, indent=2))


@app.command("ratings")
def ratings() -> None:
    create_tables()
    with SessionLocal() as session:
        EloSystem().rebuild_from_matches(session)
        rows = session.execute(select(Team, TeamRating).join(TeamRating, Team.id == TeamRating.team_id)).all()
        payload = [{"team": team.name, "rating": rating.rating, "context": rating.context} for team, rating in rows]
    typer.echo(json.dumps(sorted(payload, key=lambda row: row["rating"], reverse=True), indent=2))

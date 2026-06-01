from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.database.models import Event, MapPlayed, Match, Player, PlayerMapStat, Team
from backend.ingestion.schemas import ParsedMatch


def import_parsed_match(session: Session, parsed: ParsedMatch) -> Match:
    if parsed.hltv_match_id is not None:
        existing = session.scalar(select(Match).where(Match.hltv_match_id == parsed.hltv_match_id))
        if existing:
            return existing

    team1 = _get_or_create_team(session, parsed.team1)
    team2 = _get_or_create_team(session, parsed.team2)
    winner = _team_by_name(session, parsed.winner) if parsed.winner else None
    event = _get_or_create_event(session, parsed.event) if parsed.event else None

    match = Match(
        event=event,
        team1=team1,
        team2=team2,
        winner=winner,
        format=parsed.format,
        team1_score=parsed.team1_score,
        team2_score=parsed.team2_score,
        played_at=parsed.played_at,
        hltv_match_id=parsed.hltv_match_id,
        source_url=parsed.source_url,
    )
    session.add(match)
    session.flush()

    for parsed_map in parsed.maps:
        map_winner = _team_by_name(session, parsed_map.winner) if parsed_map.winner else None
        map_played = MapPlayed(
            match=match,
            map_name=parsed_map.name,
            map_number=parsed_map.map_number,
            team1_score=parsed_map.team1_score,
            team2_score=parsed_map.team2_score,
            winner=map_winner,
        )
        session.add(map_played)
        session.flush()
        imported_players: set[int] = set()
        for stat in parsed_map.player_stats:
            team = _get_or_create_team(session, stat.team)
            player = _get_or_create_player(session, stat.player, team)
            if player.id in imported_players:
                continue
            imported_players.add(player.id)
            session.add(
                PlayerMapStat(
                    map_played=map_played,
                    player=player,
                    team=team,
                    side=stat.side,
                    kills=stat.kills,
                    deaths=stat.deaths,
                    assists=stat.assists,
                    adr=stat.adr,
                    kast=stat.kast,
                    rating_2=stat.rating_2,
                    swing=stat.swing,
                    dpr=stat.dpr,
                    kpr=stat.kpr,
                    multikill_rounds=stat.multikill_rounds,
                    firepower=stat.firepower,
                    entrying=stat.entrying,
                    trading=stat.trading,
                    clutching=stat.clutching,
                    utility=stat.utility,
                    sniping=stat.sniping,
                    opening=stat.opening,
                    opening_kills=stat.opening_kills,
                    opening_deaths=stat.opening_deaths,
                    clutches_won=stat.clutches_won,
                    clutches_attempted=stat.clutches_attempted,
                    utility_damage=stat.utility_damage,
                    flash_assists=stat.flash_assists,
                )
            )

    session.commit()
    session.refresh(match)
    return match


def _get_or_create_team(session: Session, name: str) -> Team:
    team = session.scalar(select(Team).where(Team.name == name))
    if team:
        return team
    team = Team(name=name)
    session.add(team)
    session.flush()
    return team


def _get_or_create_player(session: Session, nickname: str, team: Team) -> Player:
    player = session.scalar(select(Player).where(Player.nickname == nickname))
    if player:
        if player.current_team_id is None:
            player.current_team = team
        return player
    player = Player(nickname=nickname, current_team=team)
    session.add(player)
    session.flush()
    return player


def _get_or_create_event(session: Session, name: str) -> Event:
    event = session.scalar(select(Event).where(Event.name == name))
    if event:
        return event
    event = Event(name=name)
    session.add(event)
    session.flush()
    return event


def _team_by_name(session: Session, name: str | None) -> Team | None:
    if not name:
        return None
    return session.scalar(select(Team).where(Team.name == name))

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database.session import Base


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    hltv_id: Mapped[int | None] = mapped_column(Integer, unique=True, nullable=True)
    region: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Player(Base):
    __tablename__ = "players"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nickname: Mapped[str] = mapped_column(String(120), index=True)
    hltv_id: Mapped[int | None] = mapped_column(Integer, unique=True, nullable=True)
    current_team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), nullable=True)
    current_status: Mapped[str] = mapped_column(String(32), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    current_team: Mapped[Team | None] = relationship()


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(180), unique=True, index=True)
    hltv_id: Mapped[int | None] = mapped_column(Integer, unique=True, nullable=True)
    tier: Mapped[str | None] = mapped_column(String(16), nullable=True)
    is_major: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Match(Base):
    __tablename__ = "matches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int | None] = mapped_column(ForeignKey("events.id"), nullable=True)
    team1_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    team2_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    winner_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), nullable=True)
    format: Mapped[str] = mapped_column(String(8), default="bo1")
    team1_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    team2_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    played_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    hltv_match_id: Mapped[int | None] = mapped_column(Integer, unique=True, nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    event: Mapped[Event | None] = relationship()
    team1: Mapped[Team] = relationship(foreign_keys=[team1_id])
    team2: Mapped[Team] = relationship(foreign_keys=[team2_id])
    winner: Mapped[Team | None] = relationship(foreign_keys=[winner_id])
    maps: Mapped[list[MapPlayed]] = relationship(back_populates="match", cascade="all, delete-orphan")


class MapPlayed(Base):
    __tablename__ = "maps_played"
    __table_args__ = (UniqueConstraint("match_id", "map_number", name="uq_map_match_number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"))
    map_name: Mapped[str] = mapped_column(String(64))
    map_number: Mapped[int] = mapped_column(Integer)
    team1_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    team2_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    winner_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), nullable=True)

    match: Mapped[Match] = relationship(back_populates="maps")
    winner: Mapped[Team | None] = relationship()
    player_stats: Mapped[list[PlayerMapStat]] = relationship(back_populates="map_played", cascade="all, delete-orphan")


class PlayerMapStat(Base):
    __tablename__ = "player_map_stats"
    __table_args__ = (UniqueConstraint("map_id", "player_id", "side", name="uq_player_map_side_stat"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    map_id: Mapped[int] = mapped_column(ForeignKey("maps_played.id"))
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    side: Mapped[str] = mapped_column(String(8), default="both")
    kills: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deaths: Mapped[int | None] = mapped_column(Integer, nullable=True)
    assists: Mapped[int | None] = mapped_column(Integer, nullable=True)
    adr: Mapped[float | None] = mapped_column(Float, nullable=True)
    kast: Mapped[float | None] = mapped_column(Float, nullable=True)
    rating_2: Mapped[float | None] = mapped_column(Float, nullable=True)
    swing: Mapped[float | None] = mapped_column(Float, nullable=True)
    dpr: Mapped[float | None] = mapped_column(Float, nullable=True)
    kpr: Mapped[float | None] = mapped_column(Float, nullable=True)
    multikill_rounds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    firepower: Mapped[float | None] = mapped_column(Float, nullable=True)
    entrying: Mapped[float | None] = mapped_column(Float, nullable=True)
    trading: Mapped[float | None] = mapped_column(Float, nullable=True)
    clutching: Mapped[float | None] = mapped_column(Float, nullable=True)
    utility: Mapped[float | None] = mapped_column(Float, nullable=True)
    sniping: Mapped[float | None] = mapped_column(Float, nullable=True)
    opening: Mapped[float | None] = mapped_column(Float, nullable=True)
    opening_kills: Mapped[int | None] = mapped_column(Integer, nullable=True)
    opening_deaths: Mapped[int | None] = mapped_column(Integer, nullable=True)
    clutches_won: Mapped[int | None] = mapped_column(Integer, nullable=True)
    clutches_attempted: Mapped[int | None] = mapped_column(Integer, nullable=True)
    utility_damage: Mapped[float | None] = mapped_column(Float, nullable=True)
    flash_assists: Mapped[float | None] = mapped_column(Float, nullable=True)

    map_played: Mapped[MapPlayed] = relationship(back_populates="player_stats")
    player: Mapped[Player] = relationship()
    team: Mapped[Team] = relationship()


class TeamRating(Base):
    __tablename__ = "team_ratings"
    __table_args__ = (UniqueConstraint("team_id", "rating_type", "context", name="uq_team_rating_context"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    rating_type: Mapped[str] = mapped_column(String(32), default="elo")
    context: Mapped[str] = mapped_column(String(32), default="global")
    rating: Mapped[float] = mapped_column(Float, default=1500.0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    team: Mapped[Team] = relationship()


class PlayerTeamMembership(Base):
    __tablename__ = "player_team_memberships"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active")
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    event_id: Mapped[int | None] = mapped_column(ForeignKey("events.id"), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    player: Mapped[Player] = relationship()
    team: Mapped[Team | None] = relationship()
    event: Mapped[Event | None] = relationship()


class PlayerStatusHistory(Base):
    __tablename__ = "player_status_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    status: Mapped[str] = mapped_column(String(32))
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    player: Mapped[Player] = relationship()
    team: Mapped[Team | None] = relationship()


class TeamMapStat(Base):
    __tablename__ = "team_map_stats"
    __table_args__ = (UniqueConstraint("team_id", "map_name", "side", "window_days", "computed_at", name="uq_team_map_stat_snapshot"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    map_name: Mapped[str] = mapped_column(String(64))
    side: Mapped[str] = mapped_column(String(8), default="both")
    window_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    matches_played: Mapped[int] = mapped_column(Integer, default=0)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    losses: Mapped[int] = mapped_column(Integer, default=0)
    win_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    t_round_win_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    ct_round_win_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    pick_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    ban_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    team: Mapped[Team] = relationship()


class TeamRanking(Base):
    __tablename__ = "team_rankings"
    __table_args__ = (UniqueConstraint("team_id", "source", "ranking_date", name="uq_team_ranking_source_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    source: Mapped[str] = mapped_column(String(16), index=True)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    points: Mapped[float | None] = mapped_column(Float, nullable=True)
    ranking_date: Mapped[date] = mapped_column(Date, index=True)
    region: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    team: Mapped[Team] = relationship()


class Prediction(Base):
    __tablename__ = "predictions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int | None] = mapped_column(ForeignKey("events.id"), nullable=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    stage: Mapped[str] = mapped_column(String(32), default="stage1")
    predicted_outcome: Mapped[str] = mapped_column(String(32))
    probability: Mapped[float] = mapped_column(Float)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    team: Mapped[Team] = relationship()

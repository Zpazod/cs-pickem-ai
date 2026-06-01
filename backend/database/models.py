from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
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
    __table_args__ = (UniqueConstraint("map_id", "player_id", name="uq_player_map_stat"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    map_id: Mapped[int] = mapped_column(ForeignKey("maps_played.id"))
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    kills: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deaths: Mapped[int | None] = mapped_column(Integer, nullable=True)
    assists: Mapped[int | None] = mapped_column(Integer, nullable=True)
    adr: Mapped[float | None] = mapped_column(Float, nullable=True)
    kast: Mapped[float | None] = mapped_column(Float, nullable=True)
    rating_2: Mapped[float | None] = mapped_column(Float, nullable=True)

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


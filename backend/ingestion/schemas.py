from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ParsedPlayerMapStat(BaseModel):
    player: str
    team: str
    side: str = "both"
    kills: int | None = None
    deaths: int | None = None
    assists: int | None = None
    adr: float | None = None
    kast: float | None = None
    rating_2: float | None = None
    swing: float | None = None
    dpr: float | None = None
    kpr: float | None = None
    multikill_rounds: int | None = None
    firepower: float | None = None
    entrying: float | None = None
    trading: float | None = None
    clutching: float | None = None
    utility: float | None = None
    sniping: float | None = None
    opening: float | None = None
    opening_kills: int | None = None
    opening_deaths: int | None = None
    clutches_won: int | None = None
    clutches_attempted: int | None = None
    utility_damage: float | None = None
    flash_assists: float | None = None


class ParsedMap(BaseModel):
    name: str
    map_number: int
    team1_score: int | None = None
    team2_score: int | None = None
    winner: str | None = None
    player_stats: list[ParsedPlayerMapStat] = Field(default_factory=list)


class ParsedMatch(BaseModel):
    team1: str
    team2: str
    winner: str | None = None
    format: str = "bo1"
    team1_score: int | None = None
    team2_score: int | None = None
    event: str | None = None
    played_at: datetime | None = None
    hltv_match_id: int | None = None
    source_url: str | None = None
    maps: list[ParsedMap] = Field(default_factory=list)

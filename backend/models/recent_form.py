"""Recent form computation for teams and players.

Computes rolling statistics over configurable time windows (30 / 90 / 180 days
and all-time) directly from the match/map data already stored in SQLite.

Nothing is deleted.  Results are returned as plain dataclasses so callers can
use them however they like (feed into predictions, cache in DB, expose via API).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Sequence

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from backend.database.models import (
    Match,
    MapPlayed,
    Player,
    PlayerMapStat,
    Team,
    TeamRanking,
)

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

WINDOWS = {
    "30d": 30,
    "90d": 90,
    "180d": 180,
    "all": None,  # None = no cutoff
}


# ---------------------------------------------------------------------------
# Team form
# ---------------------------------------------------------------------------


@dataclass
class TeamForm:
    team: str
    window: str

    matches_played: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0

    bo1_played: int = 0
    bo1_wins: int = 0
    bo1_win_rate: float = 0.0

    bo3_played: int = 0
    bo3_wins: int = 0
    bo3_win_rate: float = 0.0

    # average Elo / strength of opponents beaten or faced
    avg_opponent_strength: float = 0.0

    # current streak: positive = win streak, negative = loss streak
    streak: int = 0

    # map-level win rate across all maps in this window
    map_win_rate: float = 0.0

    # weighted momentum: recent matches count more
    momentum: float = 0.0

    # dict: map_name -> win_rate
    map_breakdown: dict[str, float] = field(default_factory=dict)


def compute_team_form(
    session: Session,
    team: Team,
    window_days: int | None,
    opponent_ratings: dict[int, float] | None = None,
) -> TeamForm:
    """Return a TeamForm for *team* over the last *window_days* days.

    Args:
        session: active SQLAlchemy session.
        team: Team ORM object.
        window_days: number of days to look back, or None for all-time.
        opponent_ratings: optional {team_id: elo_rating} used for opponent
            strength computation.  If absent, strength defaults to 1500.
    """
    cutoff = _cutoff(window_days)
    window_label = _label(window_days)

    matches = _team_matches(session, team.id, cutoff)
    if not matches:
        return TeamForm(team=team.name, window=window_label)

    # Basic counts
    wins = losses = 0
    bo1_w = bo1_l = bo3_w = bo3_l = 0
    opponent_strengths: list[float] = []
    match_results: list[bool] = []  # True = win, ordered oldest→newest

    for match in matches:
        won = match.winner_id == team.id
        is_bo3 = match.format in {"bo3", "bo5"}
        opp_id = match.team2_id if match.team1_id == team.id else match.team1_id
        opp_strength = (opponent_ratings or {}).get(opp_id, 1500.0)

        if won:
            wins += 1
        else:
            losses += 1

        if is_bo3:
            if won:
                bo3_w += 1
            else:
                bo3_l += 1
        else:
            if won:
                bo1_w += 1
            else:
                bo1_l += 1

        opponent_strengths.append(opp_strength)
        match_results.append(won)

    total = wins + losses
    form = TeamForm(
        team=team.name,
        window=window_label,
        matches_played=total,
        wins=wins,
        losses=losses,
        win_rate=wins / total if total else 0.0,
        bo1_played=bo1_w + bo1_l,
        bo1_wins=bo1_w,
        bo1_win_rate=bo1_w / (bo1_w + bo1_l) if (bo1_w + bo1_l) else 0.0,
        bo3_played=bo3_w + bo3_l,
        bo3_wins=bo3_w,
        bo3_win_rate=bo3_w / (bo3_w + bo3_l) if (bo3_w + bo3_l) else 0.0,
        avg_opponent_strength=sum(opponent_strengths) / len(opponent_strengths) if opponent_strengths else 1500.0,
        streak=_streak(match_results),
        momentum=_momentum(match_results),
    )

    # Map-level stats
    maps_played = _team_maps(session, team.id, matches)
    form.map_win_rate, form.map_breakdown = _map_stats(maps_played, team.id)

    return form


# ---------------------------------------------------------------------------
# Player form
# ---------------------------------------------------------------------------


@dataclass
class PlayerForm:
    player: str
    team: str
    window: str

    maps_played: int = 0

    # Core stats (averages across maps in window)
    avg_rating_2: float | None = None
    avg_kast: float | None = None
    avg_adr: float | None = None
    avg_kpr: float | None = None
    avg_dpr: float | None = None

    # Impact stats
    avg_opening: float | None = None
    avg_entrying: float | None = None
    avg_trading: float | None = None
    avg_clutching: float | None = None
    avg_utility: float | None = None
    avg_sniping: float | None = None
    avg_firepower: float | None = None

    # Opening duel raw counts
    opening_kills: int = 0
    opening_deaths: int = 0
    opening_duel_rate: float | None = None  # kills / (kills+deaths)

    # Clutch raw counts
    clutches_won: int = 0
    clutches_attempted: int = 0
    clutch_success_rate: float | None = None

    # Rating trend: slope of rating over time (positive = improving)
    rating_trend: float | None = None


def compute_player_form(
    session: Session,
    player: Player,
    window_days: int | None,
) -> PlayerForm:
    """Return a PlayerForm for *player* over the last *window_days* days."""
    cutoff = _cutoff(window_days)
    window_label = _label(window_days)

    stats_rows = _player_stats_in_window(session, player.id, cutoff)
    if not stats_rows:
        team_name = player.current_team.name if player.current_team else "Unknown"
        return PlayerForm(player=player.nickname, team=team_name, window=window_label)

    team_name = stats_rows[0].team.name if stats_rows[0].team else "Unknown"

    # Aggregate
    def _avg(attr: str) -> float | None:
        vals = [getattr(r, attr) for r in stats_rows if getattr(r, attr) is not None]
        return sum(vals) / len(vals) if vals else None

    ok_kills = sum(r.opening_kills or 0 for r in stats_rows)
    ok_deaths = sum(r.opening_deaths or 0 for r in stats_rows)
    c_won = sum(r.clutches_won or 0 for r in stats_rows)
    c_att = sum(r.clutches_attempted or 0 for r in stats_rows)

    ratings_ordered = [
        r.rating_2 for r in stats_rows if r.rating_2 is not None
    ]

    return PlayerForm(
        player=player.nickname,
        team=team_name,
        window=window_label,
        maps_played=len(stats_rows),
        avg_rating_2=_avg("rating_2"),
        avg_kast=_avg("kast"),
        avg_adr=_avg("adr"),
        avg_kpr=_avg("kpr"),
        avg_dpr=_avg("dpr"),
        avg_opening=_avg("opening"),
        avg_entrying=_avg("entrying"),
        avg_trading=_avg("trading"),
        avg_clutching=_avg("clutching"),
        avg_utility=_avg("utility"),
        avg_sniping=_avg("sniping"),
        avg_firepower=_avg("firepower"),
        opening_kills=ok_kills,
        opening_deaths=ok_deaths,
        opening_duel_rate=ok_kills / (ok_kills + ok_deaths) if (ok_kills + ok_deaths) else None,
        clutches_won=c_won,
        clutches_attempted=c_att,
        clutch_success_rate=c_won / c_att if c_att else None,
        rating_trend=_linear_trend(ratings_ordered),
    )


# ---------------------------------------------------------------------------
# Bulk helpers used by team_strength.py
# ---------------------------------------------------------------------------


def compute_all_team_forms(
    session: Session,
    teams: Sequence[Team],
    window_days: int | None = 90,
    opponent_ratings: dict[int, float] | None = None,
) -> dict[str, TeamForm]:
    """Return {team_name: TeamForm} for every team in *teams*."""
    return {
        team.name: compute_team_form(session, team, window_days, opponent_ratings)
        for team in teams
    }


def form_to_strength_bonus(form: TeamForm) -> float:
    """Convert a TeamForm to a ±rating bonus to add on top of Elo/ranking.

    Scale: roughly ±50 points for dominant vs poor recent form.
    """
    if form.matches_played < 3:
        return 0.0  # not enough data to adjust

    # Base: win-rate centred on 0.5
    wr_bonus = (form.win_rate - 0.5) * 60.0

    # Momentum (recent trend) amplifies or dampens
    momentum_bonus = form.momentum * 30.0

    # Streak bonus (capped at ±3 matches worth)
    streak_bonus = max(-3, min(3, form.streak)) * 5.0

    return wr_bonus + momentum_bonus + streak_bonus


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _cutoff(window_days: int | None) -> datetime | None:
    if window_days is None:
        return None
    return datetime.utcnow() - timedelta(days=window_days)


def _label(window_days: int | None) -> str:
    if window_days is None:
        return "all"
    return f"{window_days}d"


def _team_matches(
    session: Session, team_id: int, cutoff: datetime | None
) -> list[Match]:
    stmt = (
        select(Match)
        .where(
            Match.winner_id.is_not(None),
            or_(Match.team1_id == team_id, Match.team2_id == team_id),
        )
        .order_by(Match.played_at.asc().nulls_last(), Match.id.asc())
    )
    if cutoff:
        stmt = stmt.where(
            or_(Match.played_at.is_(None), Match.played_at >= cutoff)
        )
    return list(session.scalars(stmt).all())


def _team_maps(
    session: Session,
    team_id: int,
    matches: list[Match],
) -> list[MapPlayed]:
    if not matches:
        return []
    match_ids = [m.id for m in matches]
    stmt = select(MapPlayed).where(MapPlayed.match_id.in_(match_ids))
    return list(session.scalars(stmt).all())


def _map_stats(
    maps: list[MapPlayed], team_id: int
) -> tuple[float, dict[str, float]]:
    """Return (overall_map_win_rate, {map_name: win_rate})."""
    by_map: dict[str, list[bool]] = {}
    for mp in maps:
        if mp.winner_id is None:
            continue
        won = mp.winner_id == team_id
        by_map.setdefault(mp.map_name, []).append(won)

    breakdown: dict[str, float] = {}
    total_w = total_l = 0
    for map_name, results in by_map.items():
        w = sum(results)
        l = len(results) - w
        breakdown[map_name] = w / len(results)
        total_w += w
        total_l += l

    overall = total_w / (total_w + total_l) if (total_w + total_l) else 0.0
    return overall, breakdown


def _player_stats_in_window(
    session: Session, player_id: int, cutoff: datetime | None
) -> list[PlayerMapStat]:
    stmt = (
        select(PlayerMapStat)
        .join(MapPlayed, PlayerMapStat.map_id == MapPlayed.id)
        .join(Match, MapPlayed.match_id == Match.id)
        .where(PlayerMapStat.player_id == player_id)
        .order_by(Match.played_at.asc().nulls_last(), Match.id.asc())
    )
    if cutoff:
        stmt = stmt.where(
            or_(Match.played_at.is_(None), Match.played_at >= cutoff)
        )
    return list(session.scalars(stmt).all())


def _streak(results: list[bool]) -> int:
    """Positive int = current win streak, negative = loss streak."""
    if not results:
        return 0
    streak = 1 if results[-1] else -1
    for result in reversed(results[:-1]):
        if result == results[-1]:
            streak += 1 if result else -1
        else:
            break
    return streak


def _momentum(results: list[bool], half_life_matches: int = 5) -> float:
    """Exponentially-weighted win rate.  Range [-1, 1], positive = good form."""
    if not results:
        return 0.0
    weight_sum = weighted_wins = 0.0
    decay = math.exp(-math.log(2) / half_life_matches)
    w = 1.0
    for result in reversed(results):
        weighted_wins += w * (1.0 if result else 0.0)
        weight_sum += w
        w *= decay
    if weight_sum == 0:
        return 0.0
    return (weighted_wins / weight_sum) * 2 - 1  # centre on 0


def _linear_trend(values: list[float]) -> float | None:
    """Slope of a simple linear regression on *values* (returns None if < 3 pts)."""
    n = len(values)
    if n < 3:
        return None
    x_mean = (n - 1) / 2
    y_mean = sum(values) / n
    numerator = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
    denominator = sum((i - x_mean) ** 2 for i in range(n))
    return numerator / denominator if denominator else 0.0
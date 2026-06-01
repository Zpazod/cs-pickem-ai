"""Recent form computation for teams and players.

Computes rolling statistics over configurable time windows (30 / 90 / 180 days
and all-time) directly from the match/map data already stored in SQLite.

Nothing is deleted. Results are returned as plain dataclasses so callers can
use them however they like (feed into predictions, cache in DB, expose via API).

v2 — compute_team_form now calls compute_player_form internally for every
active roster member. This lets us detect:

  - "overperforming" : players are individually strong but results are bad
                       → likely bad luck / poor tactics, not a skill problem
                       → we soften the form penalty

  - "underperforming" : players are individually weak but results are good
                        → probably winning against weak opponents
                        → we soften the form bonus

  - "consistent"      : individual perf and results agree → full weight

The confidence multiplier is applied in form_to_strength_bonus().
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Sequence

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from backend.database.models import (
    MapPlayed,
    Match,
    Player,
    PlayerMapStat,
    Team,
    TeamRanking,
)

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

WINDOWS: dict[str, int | None] = {
    "30d": 30,
    "90d": 90,
    "180d": 180,
    "all": None,
}

# Rating thresholds for over/underperforming detection
_RATING_HIGH = 1.08   # above this = players are playing well individually
_RATING_LOW  = 0.97   # below this = players are playing poorly individually
_WINRATE_HIGH = 0.55  # above this = team is winning a lot
_WINRATE_LOW  = 0.45  # below this = team is losing a lot

# Minimum maps played by a player to be included in team rating average
_MIN_PLAYER_MAPS = 3

# Confidence multipliers applied to the raw form bonus
_CONFIDENCE: dict[str, float] = {
    "consistent":     1.0,
    "overperforming": 0.5,   # results worse than individual perf → soften penalty
    "underperforming": 0.5,  # results better than individual perf → soften bonus
}


# ---------------------------------------------------------------------------
# TeamForm dataclass
# ---------------------------------------------------------------------------


@dataclass
class TeamForm:
    team: str
    window: str

    # --- Results ---
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

    avg_opponent_strength: float = 0.0
    streak: int = 0
    momentum: float = 0.0

    map_win_rate: float = 0.0
    map_breakdown: dict[str, float] = field(default_factory=dict)

    # --- Individual player signal (NEW) ---
    # Average rating_2 across all players who played for this team in the window
    avg_player_rating: float | None = None
    # Per-player breakdown: {nickname: avg_rating_2}
    player_ratings: dict[str, float] = field(default_factory=dict)
    # Players dragging the team down (rating < _RATING_LOW)
    weak_players: list[str] = field(default_factory=list)
    # Players performing above expectations (rating > _RATING_HIGH)
    strong_players: list[str] = field(default_factory=list)

    # --- Diagnosis ---
    # "consistent" | "overperforming" | "underperforming" | "unknown"
    performance_vs_results: str = "unknown"
    # Human-readable explanation of the diagnosis
    diagnosis: str = ""
    # Confidence multiplier [0.5 – 1.0] applied to the form bonus
    confidence: float = 1.0


# ---------------------------------------------------------------------------
# compute_team_form  (main public function)
# ---------------------------------------------------------------------------


def compute_team_form(
    session: Session,
    team: Team,
    window_days: int | None,
    opponent_ratings: dict[int, float] | None = None,
) -> TeamForm:
    """Return a TeamForm for *team* over the last *window_days* days.

    Now enriched with individual player form so we can distinguish
    bad luck from genuine underperformance.

    Args:
        session: active SQLAlchemy session.
        team: Team ORM object.
        window_days: number of days to look back, or None for all-time.
        opponent_ratings: optional {team_id: elo_rating} used for opponent
            strength computation. If absent, defaults to 1500.
    """
    cutoff = _cutoff(window_days)
    window_label = _label(window_days)

    matches = _team_matches(session, team.id, cutoff)
    if not matches:
        return TeamForm(team=team.name, window=window_label)

    # ------------------------------------------------------------------
    # 1. Results aggregation (unchanged logic)
    # ------------------------------------------------------------------
    wins = losses = 0
    bo1_w = bo1_l = bo3_w = bo3_l = 0
    opponent_strengths: list[float] = []
    match_results: list[bool] = []

    for match in matches:
        won = match.winner_id == team.id
        is_bo3 = match.format in {"bo3", "bo5"}
        opp_id = match.team2_id if match.team1_id == team.id else match.team1_id
        opp_strength = (opponent_ratings or {}).get(opp_id, 1500.0)

        wins += int(won)
        losses += int(not won)

        if is_bo3:
            bo3_w += int(won); bo3_l += int(not won)
        else:
            bo1_w += int(won); bo1_l += int(not won)

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
        avg_opponent_strength=(
            sum(opponent_strengths) / len(opponent_strengths)
            if opponent_strengths else 1500.0
        ),
        streak=_streak(match_results),
        momentum=_momentum(match_results),
    )

    # Map-level stats
    maps_played = _team_maps(session, team.id, matches)
    form.map_win_rate, form.map_breakdown = _map_stats(maps_played, team.id)

    # ------------------------------------------------------------------
    # 2. Player form — new section
    # ------------------------------------------------------------------
    _enrich_with_player_form(session, form, team, window_days)

    return form


# ---------------------------------------------------------------------------
# PlayerForm dataclass
# ---------------------------------------------------------------------------


@dataclass
class PlayerForm:
    player: str
    team: str
    window: str

    maps_played: int = 0

    avg_rating_2: float | None = None
    avg_kast: float | None = None
    avg_adr: float | None = None
    avg_kpr: float | None = None
    avg_dpr: float | None = None

    avg_opening: float | None = None
    avg_entrying: float | None = None
    avg_trading: float | None = None
    avg_clutching: float | None = None
    avg_utility: float | None = None
    avg_sniping: float | None = None
    avg_firepower: float | None = None

    opening_kills: int = 0
    opening_deaths: int = 0
    opening_duel_rate: float | None = None

    clutches_won: int = 0
    clutches_attempted: int = 0
    clutch_success_rate: float | None = None

    rating_trend: float | None = None


# ---------------------------------------------------------------------------
# compute_player_form
# ---------------------------------------------------------------------------


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

    def _avg(attr: str) -> float | None:
        vals = [getattr(r, attr) for r in stats_rows if getattr(r, attr) is not None]
        return sum(vals) / len(vals) if vals else None

    ok_kills  = sum(r.opening_kills or 0 for r in stats_rows)
    ok_deaths = sum(r.opening_deaths or 0 for r in stats_rows)
    c_won     = sum(r.clutches_won or 0 for r in stats_rows)
    c_att     = sum(r.clutches_attempted or 0 for r in stats_rows)
    ratings_ordered = [r.rating_2 for r in stats_rows if r.rating_2 is not None]

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
        opening_duel_rate=(
            ok_kills / (ok_kills + ok_deaths) if (ok_kills + ok_deaths) else None
        ),
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
    """Convert a TeamForm to a ±strength bonus.

    Scale: roughly ±50 pts for dominant vs poor recent form.

    The raw bonus is multiplied by form.confidence so that:
    - "overperforming"  (good players, bad results) → penalty is softened
    - "underperforming" (bad players, good results) → bonus is softened
    - "consistent"                                  → full weight
    """
    if form.matches_played < 3:
        return 0.0

    wr_bonus       = (form.win_rate - 0.5) * 60.0
    momentum_bonus = form.momentum * 30.0
    streak_bonus   = max(-3, min(3, form.streak)) * 5.0

    raw = wr_bonus + momentum_bonus + streak_bonus
    return raw * form.confidence


# ---------------------------------------------------------------------------
# Private — player enrichment of TeamForm
# ---------------------------------------------------------------------------


def _enrich_with_player_form(
    session: Session,
    form: TeamForm,
    team: Team,
    window_days: int | None,
) -> None:
    """Compute per-player ratings and inject diagnosis into *form* in-place."""

    # Find all players who appeared in stats for this team in the window
    players = _players_for_team_in_window(session, team.id, window_days)

    if not players:
        form.performance_vs_results = "unknown"
        form.diagnosis = "No individual player stats available."
        form.confidence = 1.0
        return

    player_ratings: dict[str, float] = {}
    for player in players:
        pf = compute_player_form(session, player, window_days)
        # Only include players with enough maps to be meaningful
        if pf.maps_played >= _MIN_PLAYER_MAPS and pf.avg_rating_2 is not None:
            player_ratings[player.nickname] = pf.avg_rating_2

    if not player_ratings:
        form.performance_vs_results = "unknown"
        form.diagnosis = "Not enough individual player data."
        form.confidence = 1.0
        return

    avg_rating = sum(player_ratings.values()) / len(player_ratings)
    strong = [n for n, r in player_ratings.items() if r >= _RATING_HIGH]
    weak   = [n for n, r in player_ratings.items() if r <= _RATING_LOW]

    form.avg_player_rating = avg_rating
    form.player_ratings    = player_ratings
    form.strong_players    = strong
    form.weak_players      = weak

    # ------------------------------------------------------------------
    # Diagnosis logic
    # ------------------------------------------------------------------
    win_rate   = form.win_rate
    sufficient = form.matches_played >= 3

    if not sufficient:
        diagnosis  = "overperforming"
        label      = "unknown"
        confidence = 1.0
        explanation = "Not enough matches for a reliable diagnosis."

    elif avg_rating >= _RATING_HIGH and win_rate <= _WINRATE_LOW:
        # Players perform well individually but team keeps losing
        label      = "overperforming"
        confidence = _CONFIDENCE["overperforming"]
        explanation = (
            f"Players avg rating {avg_rating:.2f} (good) but win rate "
            f"{win_rate:.0%} (poor). Losses likely due to tactical/IGL issues "
            f"or bad luck, not individual skill. Form penalty softened "
            f"(confidence {confidence:.0%})."
        )
        if weak:
            explanation += f" Weak links: {', '.join(weak)}."

    elif avg_rating <= _RATING_LOW and win_rate >= _WINRATE_HIGH:
        # Players perform poorly individually but team keeps winning
        label      = "underperforming"
        confidence = _CONFIDENCE["underperforming"]
        explanation = (
            f"Players avg rating {avg_rating:.2f} (weak) but win rate "
            f"{win_rate:.0%} (high). Wins likely against weak opponents. "
            f"Form bonus softened (confidence {confidence:.0%})."
        )
        if strong:
            explanation += f" Carrying players: {', '.join(strong)}."

    else:
        label      = "consistent"
        confidence = _CONFIDENCE["consistent"]
        explanation = (
            f"Players avg rating {avg_rating:.2f} aligns with win rate "
            f"{win_rate:.0%}. Signal is reliable (confidence {confidence:.0%})."
        )
        if strong:
            explanation += f" Top performers: {', '.join(strong)}."
        if weak:
            explanation += f" Weak links: {', '.join(weak)}."

    form.performance_vs_results = label
    form.diagnosis               = explanation
    form.confidence              = confidence


def _players_for_team_in_window(
    session: Session,
    team_id: int,
    window_days: int | None,
) -> list[Player]:
    """Return Player objects who played for *team_id* inside the time window."""
    cutoff = _cutoff(window_days)

    stmt = (
        select(Player)
        .join(PlayerMapStat, PlayerMapStat.player_id == Player.id)
        .join(MapPlayed, PlayerMapStat.map_id == MapPlayed.id)
        .join(Match, MapPlayed.match_id == Match.id)
        .where(PlayerMapStat.team_id == team_id)
        .distinct()
    )
    if cutoff:
        stmt = stmt.where(
            or_(Match.played_at.is_(None), Match.played_at >= cutoff)
        )
    return list(session.scalars(stmt).all())


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _cutoff(window_days: int | None) -> datetime | None:
    if window_days is None:
        return None
    return datetime.utcnow() - timedelta(days=window_days)


def _label(window_days: int | None) -> str:
    return "all" if window_days is None else f"{window_days}d"


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
    session: Session, team_id: int, matches: list[Match]
) -> list[MapPlayed]:
    if not matches:
        return []
    match_ids = [m.id for m in matches]
    return list(session.scalars(
        select(MapPlayed).where(MapPlayed.match_id.in_(match_ids))
    ).all())


def _map_stats(
    maps: list[MapPlayed], team_id: int
) -> tuple[float, dict[str, float]]:
    by_map: dict[str, list[bool]] = {}
    for mp in maps:
        if mp.winner_id is None:
            continue
        by_map.setdefault(mp.map_name, []).append(mp.winner_id == team_id)

    breakdown: dict[str, float] = {}
    total_w = total_l = 0
    for map_name, results in by_map.items():
        w = sum(results)
        breakdown[map_name] = w / len(results)
        total_w += w
        total_l += len(results) - w

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
    if not results:
        return 0
    val = results[-1]
    count = 0
    for r in reversed(results):
        if r == val:
            count += 1
        else:
            break
    return count if val else -count


def _momentum(results: list[bool], half_life_matches: int = 5) -> float:
    if not results:
        return 0.0
    decay = math.exp(-math.log(2) / half_life_matches)
    w = weight_sum = weighted_wins = 0.0
    w = 1.0
    for result in reversed(results):
        weighted_wins += w * float(result)
        weight_sum    += w
        w             *= decay
    return (weighted_wins / weight_sum) * 2 - 1 if weight_sum else 0.0


def _linear_trend(values: list[float]) -> float | None:
    n = len(values)
    if n < 3:
        return None
    x_mean = (n - 1) / 2
    y_mean = sum(values) / n
    num = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
    den = sum((i - x_mean) ** 2 for i in range(n))
    return num / den if den else 0.0
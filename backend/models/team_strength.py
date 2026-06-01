"""Team strength model — v2.

Combines three signal sources:

1. Elo (rebuilt from all imported matches, time-decayed).
2. HLTV / VRS ranking points (most recent snapshot).
3. Recent form bonus (±50 pts based on win-rate, momentum, streak).

The final composite strength feeds the Swiss Monte Carlo simulator via
``win_probability()``.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.database.models import Team, TeamRanking, TeamRating
from backend.models.elo import EloSystem
from backend.models.recent_form import (
    TeamForm,
    compute_team_form,
    form_to_strength_bonus,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_WEIGHTS = {
    "elo": 0.10,
    "hltv": 0.45,
    "vrs": 0.45,
}

# How many days of recent form to use for the bonus signal.
# 90 days is a good balance: captures recent form without being too noisy.
FORM_WINDOW_DAYS = 90


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TeamStrength:
    team: str
    final_strength: float
    elo_strength: float | None
    hltv_strength: float | None
    vrs_strength: float | None
    form_bonus: float
    form_summary: str
    explanation: str


@dataclass(frozen=True)
class StrengthPrediction:
    team1: str
    team2: str
    team1_strength: float
    team2_strength: float
    team1_win_probability: float
    explanation: str


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


class TeamStrengthModel:
    def __init__(self, base_rating: float = 1500.0, form_window_days: int = FORM_WINDOW_DAYS):
        self.base_rating = base_rating
        self.form_window_days = form_window_days
        self.elo = EloSystem(base_rating=base_rating)

    # ------------------------------------------------------------------
    # Per-team strength
    # ------------------------------------------------------------------

    def strength_for_team_name(self, session: Session, team_name: str) -> TeamStrength:
        team = session.scalar(select(Team).where(Team.name == team_name))
        if not team:
            raise ValueError(f"Unknown team: {team_name}")
        return self.strength_for_team(session, team)

    def strength_for_team(self, session: Session, team: Team) -> TeamStrength:
        elo_strength = _latest_elo(session, team.id, self.base_rating)
        hltv = _latest_ranking(session, team.id, "hltv")
        vrs = _latest_ranking(session, team.id, "vrs")

        components: dict[str, float] = {"elo": elo_strength}
        if hltv:
            components["hltv"] = _ranking_to_strength(hltv, self.base_rating)
        if vrs:
            components["vrs"] = _ranking_to_strength(vrs, self.base_rating)

        weight_sum = sum(DEFAULT_WEIGHTS[key] for key in components)
        base_strength = sum(
            components[key] * DEFAULT_WEIGHTS[key] / weight_sum for key in components
        )

        # Recent form bonus
        form = compute_team_form(session, team, self.form_window_days)
        bonus = form_to_strength_bonus(form)
        final_strength = base_strength + bonus

        explanation = _explain_strength(
            team.name, final_strength, base_strength, bonus, components, hltv, vrs, form
        )

        return TeamStrength(
            team=team.name,
            final_strength=final_strength,
            elo_strength=components.get("elo"),
            hltv_strength=components.get("hltv"),
            vrs_strength=components.get("vrs"),
            form_bonus=bonus,
            form_summary=_form_summary(form),
            explanation=explanation,
        )

    # ------------------------------------------------------------------
    # Head-to-head prediction
    # ------------------------------------------------------------------

    def predict(
        self,
        session: Session,
        team1_name: str,
        team2_name: str,
        bo3: bool = False,
    ) -> StrengthPrediction:
        t1 = self.strength_for_team_name(session, team1_name)
        t2 = self.strength_for_team_name(session, team2_name)
        prob = self.elo.expected_score(t1.final_strength, t2.final_strength)
        if bo3:
            prob = self.elo.bo3_probability(prob)
        explanation = (
            f"{t1.team} strength {t1.final_strength:.1f} (form bonus {t1.form_bonus:+.1f})"
            f" vs {t2.team} strength {t2.final_strength:.1f} (form bonus {t2.form_bonus:+.1f}). "
            f"Estimated {'BO3' if bo3 else 'BO1'} win probability: {prob:.1%}. "
            f"{t1.explanation} || {t2.explanation}"
        )
        return StrengthPrediction(
            t1.team, t2.team,
            t1.final_strength, t2.final_strength,
            prob, explanation,
        )

    # ------------------------------------------------------------------
    # Callable suitable for SwissMonteCarlo.win_probability
    # ------------------------------------------------------------------

    def make_win_probability_fn(
        self, session: Session, teams: list[str]
    ):
        """Return a (team_a, team_b, is_bo3) -> float callable.

        Pre-computes all strengths once so the Monte Carlo inner loop stays fast.
        """
        strengths = {
            name: self.strength_for_team_name(session, name).final_strength
            for name in teams
        }

        def win_probability(team_a: str, team_b: str, is_bo3: bool) -> float:
            prob = self.elo.expected_score(strengths[team_a], strengths[team_b])
            return self.elo.bo3_probability(prob) if is_bo3 else prob

        return win_probability


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _latest_elo(session: Session, team_id: int, default: float) -> float:
    rating = session.scalar(
        select(TeamRating).where(
            TeamRating.team_id == team_id,
            TeamRating.rating_type == "elo",
            TeamRating.context == "global",
        )
    )
    return rating.rating if rating else default


def _latest_ranking(
    session: Session, team_id: int, source: str
) -> TeamRanking | None:
    return session.scalar(
        select(TeamRanking)
        .where(TeamRanking.team_id == team_id, TeamRanking.source == source)
        .order_by(TeamRanking.ranking_date.desc(), TeamRanking.id.desc())
        .limit(1)
    )


def _ranking_to_strength(ranking: TeamRanking, base_rating: float) -> float:
    if ranking.points is not None:
        return base_rating + (ranking.points - 1000.0) / 4.0
    if ranking.rank is not None:
        return base_rating + max(0, 31 - ranking.rank) * 8.0
    return base_rating


def _form_summary(form: TeamForm) -> str:
    if form.matches_played < 3:
        return "insufficient data"
    streak_str = (
        f"{abs(form.streak)}-win streak" if form.streak > 0
        else f"{abs(form.streak)}-loss streak" if form.streak < 0
        else "no streak"
    )
    return (
        f"{form.win_rate:.0%} WR/{form.matches_played}m, "
        f"BO1 {form.bo1_win_rate:.0%}, BO3 {form.bo3_win_rate:.0%}, "
        f"{streak_str}"
    )


def _explain_strength(
    team_name: str,
    final_strength: float,
    base_strength: float,
    bonus: float,
    components: dict[str, float],
    hltv: TeamRanking | None,
    vrs: TeamRanking | None,
    form: TeamForm,
) -> str:
    source_bits = [f"Elo {components['elo']:.1f}"]
    if hltv and "hltv" in components:
        source_bits.append(_ranking_bit("HLTV", hltv, components["hltv"]))
    if vrs and "vrs" in components:
        source_bits.append(_ranking_bit("VRS", vrs, components["vrs"]))
    form_str = f"form bonus {bonus:+.1f} ({_form_summary(form)})"
    return (
        f"{team_name}: base {base_strength:.1f} from "
        + ", ".join(source_bits)
        + f"; {form_str} → final {final_strength:.1f}."
    )


def _ranking_bit(label: str, ranking: TeamRanking, strength: float) -> str:
    rank = f"rank #{ranking.rank}" if ranking.rank is not None else "rank n/a"
    points = f"{ranking.points:.0f} pts" if ranking.points is not None else "points n/a"
    return f"{label} {rank}, {points} → {strength:.1f}"
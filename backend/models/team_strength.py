from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.database.models import Team, TeamRanking, TeamRating
from backend.models.elo import EloSystem


@dataclass(frozen=True)
class TeamStrength:
    team: str
    final_strength: float
    elo_strength: float | None
    hltv_strength: float | None
    vrs_strength: float | None
    explanation: str


@dataclass(frozen=True)
class StrengthPrediction:
    team1: str
    team2: str
    team1_strength: float
    team2_strength: float
    team1_win_probability: float
    explanation: str


DEFAULT_WEIGHTS = {
    "elo": 0.10,
    "hltv": 0.45,
    "vrs": 0.45,
}


class TeamStrengthModel:
    def __init__(self, base_rating: float = 1500.0):
        self.base_rating = base_rating
        self.elo = EloSystem(base_rating=base_rating)

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
        final_strength = sum(components[key] * DEFAULT_WEIGHTS[key] / weight_sum for key in components)
        explanation = _explain_strength(team.name, final_strength, components, hltv, vrs)

        return TeamStrength(
            team=team.name,
            final_strength=final_strength,
            elo_strength=components.get("elo"),
            hltv_strength=components.get("hltv"),
            vrs_strength=components.get("vrs"),
            explanation=explanation,
        )

    def predict(self, session: Session, team1_name: str, team2_name: str, bo3: bool = False) -> StrengthPrediction:
        team1 = self.strength_for_team_name(session, team1_name)
        team2 = self.strength_for_team_name(session, team2_name)
        probability = self.elo.expected_score(team1.final_strength, team2.final_strength)
        if bo3:
            probability = self.elo.bo3_probability(probability)
        explanation = (
            f"{team1.team} strength {team1.final_strength:.1f} vs {team2.team} strength {team2.final_strength:.1f}. "
            f"Estimated {'BO3' if bo3 else 'BO1'} win probability: {probability:.1%}. "
            f"{team1.explanation} {team2.explanation}"
        )
        return StrengthPrediction(team1.team, team2.team, team1.final_strength, team2.final_strength, probability, explanation)


def _latest_elo(session: Session, team_id: int, default: float) -> float:
    rating = session.scalar(
        select(TeamRating).where(
            TeamRating.team_id == team_id,
            TeamRating.rating_type == "elo",
            TeamRating.context == "global",
        )
    )
    return rating.rating if rating else default


def _latest_ranking(session: Session, team_id: int, source: str) -> TeamRanking | None:
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


def _explain_strength(
    team_name: str,
    final_strength: float,
    components: dict[str, float],
    hltv: TeamRanking | None,
    vrs: TeamRanking | None,
) -> str:
    source_bits = [f"Elo {components['elo']:.1f}"]
    if hltv and "hltv" in components:
        source_bits.append(_ranking_bit("HLTV", hltv, components["hltv"]))
    if vrs and "vrs" in components:
        source_bits.append(_ranking_bit("VRS", vrs, components["vrs"]))
    return f"{team_name}: combined {final_strength:.1f} from " + ", ".join(source_bits) + "."


def _ranking_bit(label: str, ranking: TeamRanking, strength: float) -> str:
    rank = f"rank #{ranking.rank}" if ranking.rank is not None else "rank n/a"
    points = f"{ranking.points:.0f} pts" if ranking.points is not None else "points n/a"
    return f"{label} {rank}, {points} -> {strength:.1f}"

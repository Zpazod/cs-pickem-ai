from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.database.models import Match, Team, TeamRating


@dataclass(frozen=True)
class EloPrediction:
    team1: str
    team2: str
    team1_rating: float
    team2_rating: float
    team1_win_probability: float
    explanation: str


class EloSystem:
    def __init__(self, base_rating: float = 1500.0, k_factor: float = 32.0):
        self.base_rating = base_rating
        self.k_factor = k_factor

    def expected_score(self, rating_a: float, rating_b: float) -> float:
        return 1 / (1 + 10 ** ((rating_b - rating_a) / 400))

    def update_pair(self, rating_a: float, rating_b: float, score_a: float, weight: float = 1.0) -> tuple[float, float]:
        expected_a = self.expected_score(rating_a, rating_b)
        expected_b = 1 - expected_a
        k = self.k_factor * weight
        return rating_a + k * (score_a - expected_a), rating_b + k * ((1 - score_a) - expected_b)

    def bo3_probability(self, bo1_probability: float) -> float:
        p = bo1_probability
        return p * p * (3 - 2 * p)

    def rebuild_from_matches(self, session: Session, context: str = "global") -> dict[int, float]:
        ratings: dict[int, float] = {}
        matches = session.scalars(
            select(Match).where(Match.winner_id.is_not(None)).order_by(Match.played_at.asc().nulls_last(), Match.id.asc())
        ).all()
        for match in matches:
            rating1 = ratings.get(match.team1_id, self.base_rating)
            rating2 = ratings.get(match.team2_id, self.base_rating)
            score1 = 1.0 if match.winner_id == match.team1_id else 0.0
            weight = self._match_weight(match.played_at)
            ratings[match.team1_id], ratings[match.team2_id] = self.update_pair(rating1, rating2, score1, weight)

        for team_id, rating in ratings.items():
            row = session.scalar(
                select(TeamRating).where(
                    TeamRating.team_id == team_id,
                    TeamRating.rating_type == "elo",
                    TeamRating.context == context,
                )
            )
            if not row:
                row = TeamRating(team_id=team_id, rating_type="elo", context=context, rating=rating)
                session.add(row)
            else:
                row.rating = rating
                row.updated_at = datetime.utcnow()
        session.commit()
        return ratings

    def predict(self, session: Session, team1_name: str, team2_name: str, context: str = "global", bo3: bool = False) -> EloPrediction:
        team1 = session.scalar(select(Team).where(Team.name == team1_name))
        team2 = session.scalar(select(Team).where(Team.name == team2_name))
        if not team1 or not team2:
            missing = team1_name if not team1 else team2_name
            raise ValueError(f"Unknown team: {missing}")
        rating1 = _rating_for(session, team1.id, context, self.base_rating)
        rating2 = _rating_for(session, team2.id, context, self.base_rating)
        prob = self.expected_score(rating1, rating2)
        if bo3:
            prob = self.bo3_probability(prob)
        explanation = (
            f"{team1.name} Elo {rating1:.1f} vs {team2.name} Elo {rating2:.1f}. "
            f"Estimated {'BO3' if bo3 else 'BO1'} win probability: {prob:.1%}."
        )
        return EloPrediction(team1.name, team2.name, rating1, rating2, prob, explanation)

    def _match_weight(self, played_at: datetime | None) -> float:
        if played_at is None:
            return 1.0
        age_days = max((datetime.utcnow() - played_at).days, 0)
        return max(0.35, math.exp(-age_days / 365))


def _rating_for(session: Session, team_id: int, context: str, default: float) -> float:
    rating = session.scalar(
        select(TeamRating).where(
            TeamRating.team_id == team_id,
            TeamRating.rating_type == "elo",
            TeamRating.context == context,
        )
    )
    return rating.rating if rating else default


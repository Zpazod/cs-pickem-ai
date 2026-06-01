from datetime import date

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.database.models import Team, TeamRanking, TeamRating
from backend.database.session import Base
from backend.models.team_strength import TeamStrengthModel


def test_strength_uses_elo_only_when_rankings_missing():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    with Session() as session:
        team = Team(name="Vitality")
        session.add(team)
        session.flush()
        session.add(TeamRating(team=team, rating_type="elo", context="global", rating=1525))
        session.commit()

        strength = TeamStrengthModel().strength_for_team_name(session, "Vitality")

    assert strength.final_strength == 1525
    assert strength.hltv_strength is None
    assert strength.vrs_strength is None


def test_strength_combines_elo_hltv_and_vrs():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    with Session() as session:
        team = Team(name="Vitality")
        session.add(team)
        session.flush()
        session.add(TeamRating(team=team, rating_type="elo", context="global", rating=1500))
        session.add(TeamRanking(team=team, source="hltv", rank=2, points=1400, ranking_date=date(2026, 6, 1)))
        session.add(TeamRanking(team=team, source="vrs", rank=1, points=1600, ranking_date=date(2026, 6, 1)))
        session.commit()

        strength = TeamStrengthModel().strength_for_team_name(session, "Vitality")

    assert strength.final_strength > 1500
    assert strength.hltv_strength == 1600
    assert strength.vrs_strength == 1650


def test_prediction_is_influenced_by_rankings():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    with Session() as session:
        strong = Team(name="Strong")
        weak = Team(name="Weak")
        session.add_all([strong, weak])
        session.flush()
        session.add_all(
            [
                TeamRanking(team=strong, source="hltv", rank=1, points=1600, ranking_date=date(2026, 6, 1)),
                TeamRanking(team=strong, source="vrs", rank=1, points=1700, ranking_date=date(2026, 6, 1)),
                TeamRanking(team=weak, source="hltv", rank=40, points=500, ranking_date=date(2026, 6, 1)),
                TeamRanking(team=weak, source="vrs", rank=40, points=700, ranking_date=date(2026, 6, 1)),
            ]
        )
        session.commit()

        prediction = TeamStrengthModel().predict(session, "Strong", "Weak")

    assert prediction.team1_win_probability > 0.65


def test_official_rankings_dominate_elo_when_available():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    with Session() as session:
        team = Team(name="Officially Strong")
        session.add(team)
        session.flush()
        session.add(TeamRating(team=team, rating_type="elo", context="global", rating=1300))
        session.add(TeamRanking(team=team, source="hltv", rank=1, points=1600, ranking_date=date(2026, 6, 1)))
        session.add(TeamRanking(team=team, source="vrs", rank=1, points=1700, ranking_date=date(2026, 6, 1)))
        session.commit()

        strength = TeamStrengthModel().strength_for_team_name(session, "Officially Strong")

    assert strength.final_strength > 1550

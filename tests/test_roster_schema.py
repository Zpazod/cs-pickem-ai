from datetime import date

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.database.models import Player, PlayerStatusHistory, PlayerTeamMembership, Team, TeamMapStat
from backend.database.session import Base


def test_player_status_and_membership_history_can_be_stored():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    with Session() as session:
        team = Team(name="Vitality")
        player = Player(nickname="ZywOo", current_team=team, current_status="active")
        session.add_all([team, player])
        session.flush()
        session.add(
            PlayerTeamMembership(
                player=player,
                team=team,
                status="active",
                start_date=date(2026, 1, 1),
                reason="roster snapshot",
            )
        )
        session.add(
            PlayerStatusHistory(
                player=player,
                team=team,
                status="standin",
                effective_date=date(2026, 5, 1),
                reason="event stand-in",
            )
        )
        session.commit()

        memberships = session.scalars(select(PlayerTeamMembership)).all()
        statuses = session.scalars(select(PlayerStatusHistory)).all()

    assert memberships[0].status == "active"
    assert statuses[0].status == "standin"


def test_team_map_stats_can_store_side_specific_snapshot():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    with Session() as session:
        team = Team(name="Vitality")
        session.add(team)
        session.flush()
        session.add(
            TeamMapStat(
                team=team,
                map_name="Inferno",
                side="ct",
                window_days=90,
                matches_played=12,
                wins=8,
                losses=4,
                win_rate=0.667,
                ct_round_win_rate=0.58,
            )
        )
        session.commit()

        row = session.scalar(select(TeamMapStat).where(TeamMapStat.team_id == team.id))

    assert row.map_name == "Inferno"
    assert row.side == "ct"
    assert row.window_days == 90

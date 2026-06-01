import json

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.database.models import Team, TeamRanking
from backend.database.session import Base
from backend.ingestion.ranking_importer import import_rankings_file


def test_import_rankings_inserts_and_updates_without_duplicates(tmp_path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    path = tmp_path / "rankings.json"
    path.write_text(
        json.dumps(
            [
                {"team": "Vitality", "source": "hltv", "rank": 2, "points": 1450, "ranking_date": "2026-06-01"},
                {"team": "Vitality", "source": "vrs", "rank": 1, "points": 1660, "ranking_date": "2026-06-01"},
            ]
        ),
        encoding="utf-8",
    )

    with Session() as session:
        first = import_rankings_file(session, path)
        second = import_rankings_file(session, path)
        rankings = session.scalars(select(TeamRanking)).all()
        team = session.scalar(select(Team).where(Team.name == "Vitality"))

    assert first.inserted == 2
    assert first.updated == 0
    assert second.inserted == 0
    assert second.updated == 2
    assert len(rankings) == 2
    assert team is not None


from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.database.models import Match, PlayerMapStat
from backend.database.session import Base
from backend.ingestion.hltv_parser import parse_match_html_file
from backend.ingestion.importer import import_parsed_match


def test_import_match_without_duplicates():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    parsed = parse_match_html_file(
        Path(__file__).parent / "fixtures" / "sample_hltv_match.html",
        source_url="https://www.hltv.org/matches/123456/test",
    )

    with Session() as session:
        first = import_parsed_match(session, parsed)
        second = import_parsed_match(session, parsed)
        assert first.id == second.id
        assert len(session.scalars(select(Match)).all()) == 1
        assert len(session.scalars(select(PlayerMapStat)).all()) == 5


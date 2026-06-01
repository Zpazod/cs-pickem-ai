from pathlib import Path

from backend.ingestion.hltv_parser import parse_match_html_file


def test_parse_saved_hltv_match_fixture():
    parsed = parse_match_html_file(
        Path(__file__).parent / "fixtures" / "sample_hltv_match.html",
        source_url="https://www.hltv.org/matches/123456/test",
    )

    assert parsed.team1 == "Natus Vincere"
    assert parsed.team2 == "Vitality"
    assert parsed.winner == "Natus Vincere"
    assert parsed.format == "bo3"
    assert parsed.hltv_match_id == 123456
    assert len(parsed.maps) == 3
    assert parsed.maps[0].name == "Mirage"
    assert parsed.maps[0].team1_score == 13
    assert parsed.maps[0].team2_score == 9
    assert parsed.maps[0].player_stats[0].player == "b1t"
    assert parsed.maps[0].player_stats[0].kills == 20
    assert parsed.maps[0].player_stats[0].rating_2 == 1.31


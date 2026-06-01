from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup

from backend.ingestion.schemas import ParsedMap, ParsedMatch, ParsedPlayerMapStat


def parse_match_html_file(path: str | Path, source_url: str | None = None) -> ParsedMatch:
    html = Path(path).read_text(encoding="utf-8")
    match = parse_match_html(html, source_url=source_url)
    return match


def parse_match_html(html: str, source_url: str | None = None) -> ParsedMatch:
    soup = BeautifulSoup(html, "html.parser")
    team_names = _extract_teams(soup)
    if len(team_names) < 2:
        raise ValueError("Could not find two teams in HLTV match HTML.")

    team1_score, team2_score = _extract_match_score(soup)
    winner = _winner_from_score(team_names[0], team_names[1], team1_score, team2_score)
    maps = _extract_maps(soup, team_names)
    match_format = f"bo{max(len(maps), 1)}" if len(maps) in {1, 3, 5} else "bo1"

    return ParsedMatch(
        team1=team_names[0],
        team2=team_names[1],
        winner=winner,
        format=match_format,
        team1_score=team1_score,
        team2_score=team2_score,
        event=_first_text(soup, [".event a", ".event", "[data-testid='match-event']"]),
        played_at=_extract_date(soup),
        hltv_match_id=_extract_hltv_id(source_url),
        source_url=source_url,
        maps=maps,
    )


def _extract_teams(soup: BeautifulSoup) -> list[str]:
    selectors = [
        ".team1-gradient .teamName",
        ".team2-gradient .teamName",
        ".team1 .teamName",
        ".team2 .teamName",
        "[data-testid='team-name']",
        ".teamName",
    ]
    names: list[str] = []
    for selector in selectors:
        for node in soup.select(selector):
            text = node.get_text(" ", strip=True)
            if text and text not in names:
                names.append(text)
        if len(names) >= 2:
            return names[:2]
    return names


def _extract_match_score(soup: BeautifulSoup) -> tuple[int | None, int | None]:
    score_nodes = soup.select(".team1-gradient .won, .team1-gradient .lost, .team2-gradient .won, .team2-gradient .lost")
    if len(score_nodes) >= 2:
        return _to_int(score_nodes[0].get_text()), _to_int(score_nodes[1].get_text())

    text = soup.get_text(" ", strip=True)
    match = re.search(r"\b(\d+)\s*[-:]\s*(\d+)\b", text)
    if match:
        return int(match.group(1)), int(match.group(2))
    return None, None


def _extract_maps(soup: BeautifulSoup, teams: list[str]) -> list[ParsedMap]:
    maps: list[ParsedMap] = []
    map_nodes = [node for node in soup.select(".mapholder, [data-map-name]") if "stats-content" not in node.get("class", [])]
    for index, node in enumerate(map_nodes, start=1):
        map_name = node.get("data-map-name") or _first_text(node, [".mapname", ".map-name", ".dynamic-map-name"])
        if not map_name:
            text = node.get_text(" ", strip=True)
            found = re.search(r"\b(Ancient|Anubis|Dust2|Inferno|Mirage|Nuke|Overpass|Train|Vertigo)\b", text, re.I)
            map_name = found.group(1) if found else f"Map {index}"

        scores = [_to_int(x.get_text()) for x in node.select(".results-team-score, .map-score, .score")]
        scores = [score for score in scores if score is not None]
        team1_score = scores[0] if len(scores) >= 1 else None
        team2_score = scores[1] if len(scores) >= 2 else None
        winner = _winner_from_score(teams[0], teams[1], team1_score, team2_score)
        stats_node = _stats_content_for_map(soup, node)

        maps.append(
            ParsedMap(
                name=map_name,
                map_number=index,
                team1_score=team1_score,
                team2_score=team2_score,
                winner=winner,
                player_stats=_extract_player_stats(stats_node or node, teams),
            )
        )

    if not maps:
        maps.append(ParsedMap(name="Unknown", map_number=1, player_stats=_extract_player_stats(soup, teams)))
    return maps


def _stats_content_for_map(soup: BeautifulSoup, map_node) -> object | None:
    stats_link = map_node.select_one("a.results-stats[href*='mapstatsid']")
    if not stats_link:
        return None
    match = re.search(r"/mapstatsid/(\d+)/", stats_link.get("href", ""))
    if not match:
        return None
    content_id = f"{match.group(1)}-content"
    return soup.find(class_="stats-content", id=content_id)


def _extract_player_stats(node, teams: list[str]) -> list[ParsedPlayerMapStat]:
    hltv_stats = _extract_hltv_total_stats(node, teams)
    if hltv_stats:
        return hltv_stats

    stats: list[ParsedPlayerMapStat] = []
    current_team = teams[0] if teams else "Unknown"
    for table in node.select("table"):
        caption = table.find_previous(["div", "h2", "h3"])
        if caption:
            caption_text = caption.get_text(" ", strip=True)
            for team in teams:
                if team.lower() in caption_text.lower():
                    current_team = team
                    break

        for row in table.select("tr"):
            cells = [cell.get_text(" ", strip=True) for cell in row.select("td")]
            if len(cells) < 2:
                continue
            player = _player_name_from_row(row) or cells[0].split()[0]
            if not player or player.lower() in {"player", "team", current_team.lower()} or player in teams:
                continue
            kills, deaths, assists = _extract_kda(cells)
            stats.append(
                ParsedPlayerMapStat(
                    player=player,
                    team=_team_for_row(row, teams, current_team),
                    kills=kills,
                    deaths=deaths,
                    assists=assists,
                    adr=_first_float(cells, after_labels=("adr",), default_index=-3),
                    kast=_first_percent(cells),
                    rating_2=_first_float(cells, after_labels=("rating",), default_index=-1),
                )
            )
    return stats


def _extract_hltv_total_stats(node, teams: list[str]) -> list[ParsedPlayerMapStat]:
    stats: list[ParsedPlayerMapStat] = []
    seen: set[tuple[str, str]] = set()
    for table in node.select("table.totalstats"):
        team = _first_text(table, [".header-row .teamName.team", ".header-row .teamName"]) or (teams[0] if teams else "Unknown")
        for row in table.select("tr"):
            if "header-row" in row.get("class", []):
                continue
            player = _player_name_from_row(row)
            if not player:
                continue
            key = (team, player)
            if key in seen:
                continue
            seen.add(key)
            kills, deaths = _extract_kd_from_row(row)
            stats.append(
                ParsedPlayerMapStat(
                    player=player,
                    team=team,
                    kills=kills,
                    deaths=deaths,
                    assists=None,
                    adr=_cell_float(row, ".adr.traditional-data"),
                    kast=_cell_float(row, ".kast.traditional-data"),
                    rating_2=_cell_float(row, ".rating"),
                )
            )
    return stats


def _player_name_from_row(row) -> str | None:
    nick = row.select_one(".player-nick")
    if nick:
        text = nick.get_text(" ", strip=True)
        if text:
            return text
    smartphone_name = row.select_one(".smartphone-only.statsPlayerName")
    if smartphone_name:
        text = smartphone_name.get_text(" ", strip=True)
        if text:
            return text
    return None


def _extract_kd_from_row(row) -> tuple[int | None, int | None]:
    kd_cell = row.select_one(".kd.traditional-data")
    if not kd_cell:
        return None, None
    match = re.search(r"\b(\d+)\s*[-/]\s*(\d+)\b", kd_cell.get_text(" ", strip=True))
    if not match:
        return None, None
    return int(match.group(1)), int(match.group(2))


def _cell_float(row, selector: str) -> float | None:
    cell = row.select_one(selector)
    return _to_float(cell.get_text(" ", strip=True)) if cell else None


def _extract_kda(cells: list[str]) -> tuple[int | None, int | None, int | None]:
    joined = " ".join(cells)
    match = re.search(r"\b(\d+)\s*[-/]\s*(\d+)\s*[-/]\s*(\d+)\b", joined)
    if match:
        return int(match.group(1)), int(match.group(2)), int(match.group(3))

    ints = [_to_int(cell) for cell in cells[1:5]]
    ints = [value for value in ints if value is not None]
    while len(ints) < 3:
        ints.append(None)
    return ints[0], ints[1], ints[2]


def _team_for_row(row, teams: list[str], fallback: str) -> str:
    text = " ".join(row.get("class", [])) + " " + row.get_text(" ", strip=True)
    for team in teams:
        if team.lower() in text.lower():
            return team
    return fallback


def _first_text(node, selectors: list[str]) -> str | None:
    for selector in selectors:
        found = node.select_one(selector)
        if found:
            text = found.get_text(" ", strip=True)
            if text:
                return text
    return None


def _extract_date(soup: BeautifulSoup) -> datetime | None:
    date_node = soup.select_one("[data-unix]")
    if date_node and date_node.get("data-unix"):
        return datetime.fromtimestamp(int(date_node["data-unix"]) / 1000, tz=timezone.utc).replace(tzinfo=None)
    return None


def _extract_hltv_id(source_url: str | None) -> int | None:
    if not source_url:
        return None
    match = re.search(r"/matches/(\d+)/", source_url)
    return int(match.group(1)) if match else None


def _winner_from_score(team1: str, team2: str, score1: int | None, score2: int | None) -> str | None:
    if score1 is None or score2 is None or score1 == score2:
        return None
    return team1 if score1 > score2 else team2


def _to_int(text: str | None) -> int | None:
    if text is None:
        return None
    match = re.search(r"-?\d+", str(text))
    return int(match.group(0)) if match else None


def _first_float(cells: list[str], after_labels: tuple[str, ...], default_index: int) -> float | None:
    for index, cell in enumerate(cells):
        if any(label in cell.lower() for label in after_labels) and index + 1 < len(cells):
            value = _to_float(cells[index + 1])
            if value is not None:
                return value
    if cells:
        return _to_float(cells[default_index])
    return None


def _first_percent(cells: list[str]) -> float | None:
    for cell in cells:
        if "%" in cell:
            value = _to_float(cell)
            return value
    return None


def _to_float(text: str | None) -> float | None:
    if text is None:
        return None
    match = re.search(r"-?\d+(?:[.,]\d+)?", str(text))
    return float(match.group(0).replace(",", ".")) if match else None

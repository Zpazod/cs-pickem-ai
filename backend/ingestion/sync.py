"""cs2-pickem sync — startup synchronisation command.

Orchestrates the full refresh pipeline:

1. Scan ``data/raw/`` for new HTML match files not yet imported.
2. Import each new match (parse → importer).
3. Rebuild Elo ratings from all matches.
4. Detect roster changes from imported match data and write
   PlayerTeamMembership rows when a player's team changes.
5. Log every change.

Usage (CLI):
    cs2-pickem sync
    cs2-pickem sync --raw-dir path/to/html/files
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.config import RAW_DATA_DIR
from backend.database.models import (
    Match,
    Player,
    PlayerTeamMembership,
    Team,
)
from backend.database.session import SessionLocal, init_db
from backend.ingestion.hltv_parser import parse_match_html_file
from backend.ingestion.importer import import_parsed_match
from backend.models.elo import EloSystem

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class SyncResult:
    new_matches: int = 0
    skipped: int = 0
    roster_changes: int = 0
    errors: list[str] = field(default_factory=list)
    started_at: datetime = field(default_factory=datetime.utcnow)
    finished_at: datetime | None = None

    def summary(self) -> str:
        duration = (
            (self.finished_at - self.started_at).total_seconds()
            if self.finished_at
            else 0
        )
        return (
            f"Sync done in {duration:.1f}s — "
            f"{self.new_matches} new matches, "
            f"{self.skipped} skipped, "
            f"{self.roster_changes} roster changes detected, "
            f"{len(self.errors)} errors."
        )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_sync(raw_dir: Path | None = None) -> SyncResult:
    """Run the full sync pipeline.  Returns a SyncResult summary."""
    init_db()
    raw_dir = raw_dir or RAW_DATA_DIR
    result = SyncResult()

    html_files = sorted(raw_dir.glob("hltv_match_*.html"))
    logger.info("Found %d HTML files in %s", len(html_files), raw_dir)

    with SessionLocal() as session:
        # ---- Step 1 & 2: Import new match HTML files -----------------------
        for html_path in html_files:
            try:
                imported = _import_if_new(session, html_path)
                if imported:
                    result.new_matches += 1
                    logger.info("Imported: %s", html_path.name)
                else:
                    result.skipped += 1
            except Exception as exc:  # noqa: BLE001
                msg = f"Error importing {html_path.name}: {exc}"
                logger.warning(msg)
                result.errors.append(msg)

        # ---- Step 3: Rebuild Elo -------------------------------------------
        if result.new_matches > 0:
            logger.info("Rebuilding Elo for all matches…")
            EloSystem().rebuild_from_matches(session)
            logger.info("Elo rebuild complete.")

        # ---- Step 4: Roster change detection --------------------------------
        changes = detect_roster_changes(session)
        result.roster_changes = changes
        if changes:
            logger.info("%d roster change(s) recorded.", changes)

    result.finished_at = datetime.utcnow()
    logger.info(result.summary())
    return result


# ---------------------------------------------------------------------------
# Import helper
# ---------------------------------------------------------------------------


def _import_if_new(session: Session, html_path: Path) -> bool:
    """Parse the file, skip if already in DB (by hltv_match_id), else import.

    Returns True if a new match was imported.
    """
    # Quick heuristic: extract match id from filename before parsing
    hltv_id = _match_id_from_filename(html_path.name)
    if hltv_id is not None:
        existing = session.scalar(
            select(Match).where(Match.hltv_match_id == hltv_id)
        )
        if existing:
            return False

    source_url = _url_from_filename(html_path.name)
    parsed = parse_match_html_file(html_path, source_url=source_url)

    # Double-check by hltv_match_id from parsed data
    if parsed.hltv_match_id is not None:
        existing = session.scalar(
            select(Match).where(Match.hltv_match_id == parsed.hltv_match_id)
        )
        if existing:
            return False

    import_parsed_match(session, parsed)
    return True


def _match_id_from_filename(name: str) -> int | None:
    import re
    m = re.search(r"hltv_match_(\d+)", name)
    return int(m.group(1)) if m else None


def _url_from_filename(name: str) -> str | None:
    import re
    m = re.match(r"hltv_match_(\d+)_(.+)\.html$", name)
    if not m:
        return None
    match_id, slug = m.groups()
    return f"https://www.hltv.org/matches/{match_id}/{slug}"


# ---------------------------------------------------------------------------
# Roster change detection
# ---------------------------------------------------------------------------


def detect_roster_changes(session: Session) -> int:
    """Scan player_map_stats to find players appearing for a new team.

    Creates a PlayerTeamMembership row whenever a player is seen with a
    different team than their most recent membership.

    Returns the number of new membership rows created.
    """
    created = 0

    # Get all players
    players = session.scalars(select(Player)).all()

    for player in players:
        # Find all (team_id, earliest match date) pairs from stats
        from sqlalchemy import func
        from backend.database.models import PlayerMapStat, MapPlayed

        rows = session.execute(
            select(
                PlayerMapStat.team_id,
                func.min(Match.played_at).label("first_seen"),
            )
            .join(MapPlayed, PlayerMapStat.map_id == MapPlayed.id)
            .join(Match, MapPlayed.match_id == Match.id)
            .where(PlayerMapStat.player_id == player.id)
            .group_by(PlayerMapStat.team_id)
            .order_by("first_seen")
        ).all()

        if not rows:
            continue

        for team_id, first_seen in rows:
            # Check if we already have a membership for this (player, team)
            existing = session.scalar(
                select(PlayerTeamMembership).where(
                    PlayerTeamMembership.player_id == player.id,
                    PlayerTeamMembership.team_id == team_id,
                )
            )
            if existing:
                continue

            # Create new membership
            membership_date = first_seen.date() if first_seen else None
            membership = PlayerTeamMembership(
                player_id=player.id,
                team_id=team_id,
                status="active",
                start_date=membership_date,
                reason="auto-detected from match stats",
            )
            session.add(membership)
            created += 1

        # Update player.current_team_id to the most recent team seen
        if rows:
            latest_team_id = rows[-1][0]
            if player.current_team_id != latest_team_id:
                player.current_team_id = latest_team_id

    session.commit()
    return created
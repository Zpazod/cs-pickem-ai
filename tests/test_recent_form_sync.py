"""Tests for recent_form.py and sync.py.

Run with:
    pytest tests/test_recent_form_and_sync.py -v
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database.session import Base
from backend.database import models  # noqa: F401 — registers all ORM models
from backend.database.models import (
    Event,
    MapPlayed,
    Match,
    Player,
    PlayerMapStat,
    Team,
)
from backend.models.recent_form import (
    WINDOWS,
    compute_player_form,
    compute_team_form,
    form_to_strength_bonus,
    _momentum,
    _streak,
    _linear_trend,
)
from backend.ingestion.sync import run_sync, detect_roster_changes


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def session(tmp_path):
    """In-memory SQLite session for each test."""
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with Session() as s:
        yield s


def _team(session, name: str) -> Team:
    t = Team(name=name)
    session.add(t)
    session.flush()
    return t


def _player(session, nick: str, team: Team) -> Player:
    p = Player(nickname=nick, current_team=team)
    session.add(p)
    session.flush()
    return p


def _match(
    session,
    t1: Team,
    t2: Team,
    winner: Team,
    fmt: str = "bo1",
    days_ago: int = 10,
) -> Match:
    played_at = datetime.utcnow() - timedelta(days=days_ago)
    m = Match(
        team1=t1,
        team2=t2,
        winner=winner,
        format=fmt,
        team1_score=2 if winner == t1 else 1,
        team2_score=1 if winner == t1 else 2,
        played_at=played_at,
    )
    session.add(m)
    session.flush()
    return m


def _map_played(
    session,
    match: Match,
    map_name: str,
    winner: Team,
    number: int = 1,
) -> MapPlayed:
    mp = MapPlayed(
        match=match,
        map_name=map_name,
        map_number=number,
        team1_score=13,
        team2_score=10,
        winner=winner,
    )
    session.add(mp)
    session.flush()
    return mp


def _player_stat(
    session,
    map_played: MapPlayed,
    player: Player,
    team: Team,
    rating: float = 1.10,
) -> PlayerMapStat:
    stat = PlayerMapStat(
        map_played=map_played,
        player=player,
        team=team,
        side="both",
        kills=20,
        deaths=15,
        rating_2=rating,
        kast=72.5,
        adr=85.0,
        kpr=0.70,
        dpr=0.50,
        opening_kills=3,
        opening_deaths=2,
        clutches_won=1,
        clutches_attempted=2,
    )
    session.add(stat)
    session.flush()
    return stat


# ---------------------------------------------------------------------------
# Unit tests — pure functions
# ---------------------------------------------------------------------------


class TestStreakAndMomentum:
    def test_win_streak(self):
        assert _streak([True, True, True]) == 3

    def test_loss_streak(self):
        assert _streak([False, False]) == -2

    def test_mixed_ends_win(self):
        assert _streak([False, True, True]) == 2

    def test_empty(self):
        assert _streak([]) == 0

    def test_momentum_all_wins(self):
        m = _momentum([True] * 10)
        assert m > 0.8

    def test_momentum_all_losses(self):
        m = _momentum([False] * 10)
        assert m < -0.8

    def test_momentum_empty(self):
        assert _momentum([]) == 0.0


class TestLinearTrend:
    def test_increasing(self):
        slope = _linear_trend([1.0, 1.1, 1.2, 1.3])
        assert slope > 0

    def test_decreasing(self):
        slope = _linear_trend([1.3, 1.2, 1.1, 1.0])
        assert slope < 0

    def test_flat(self):
        slope = _linear_trend([1.1, 1.1, 1.1])
        assert slope == pytest.approx(0.0)

    def test_too_few_points(self):
        assert _linear_trend([1.0]) is None
        assert _linear_trend([1.0, 1.1]) is None


# ---------------------------------------------------------------------------
# Integration tests — compute_team_form
# ---------------------------------------------------------------------------


class TestComputeTeamForm:
    def test_basic_win_rate(self, session):
        t1 = _team(session, "TeamAlpha")
        t2 = _team(session, "TeamBeta")
        # 3 wins, 1 loss
        _match(session, t1, t2, winner=t1, days_ago=5)
        _match(session, t1, t2, winner=t1, days_ago=10)
        _match(session, t1, t2, winner=t1, days_ago=15)
        _match(session, t1, t2, winner=t2, days_ago=20)
        session.commit()

        form = compute_team_form(session, t1, window_days=90)

        assert form.matches_played == 4
        assert form.wins == 3
        assert form.losses == 1
        assert form.win_rate == pytest.approx(0.75)
        assert form.window == "90d"

    def test_window_filters_old_matches(self, session):
        t1 = _team(session, "TeamC")
        t2 = _team(session, "TeamD")
        _match(session, t1, t2, winner=t1, days_ago=10)   # inside 30d window
        _match(session, t1, t2, winner=t2, days_ago=200)  # outside
        session.commit()

        form_30 = compute_team_form(session, t1, window_days=30)
        form_all = compute_team_form(session, t1, window_days=None)

        assert form_30.matches_played == 1
        assert form_30.wins == 1
        assert form_all.matches_played == 2

    def test_bo1_bo3_split(self, session):
        t1 = _team(session, "TeamE")
        t2 = _team(session, "TeamF")
        _match(session, t1, t2, winner=t1, fmt="bo1", days_ago=5)
        _match(session, t1, t2, winner=t1, fmt="bo3", days_ago=8)
        _match(session, t1, t2, winner=t2, fmt="bo3", days_ago=12)
        session.commit()

        form = compute_team_form(session, t1, window_days=90)

        assert form.bo1_played == 1
        assert form.bo1_wins == 1
        assert form.bo3_played == 2
        assert form.bo3_wins == 1

    def test_streak(self, session):
        t1 = _team(session, "TeamG")
        t2 = _team(session, "TeamH")
        # Oldest→newest: loss, win, win, win
        _match(session, t1, t2, winner=t2, days_ago=40)
        _match(session, t1, t2, winner=t1, days_ago=30)
        _match(session, t1, t2, winner=t1, days_ago=20)
        _match(session, t1, t2, winner=t1, days_ago=10)
        session.commit()

        form = compute_team_form(session, t1, window_days=90)
        assert form.streak == 3

    def test_map_breakdown(self, session):
        t1 = _team(session, "TeamI")
        t2 = _team(session, "TeamJ")
        m1 = _match(session, t1, t2, winner=t1, days_ago=5)
        m2 = _match(session, t1, t2, winner=t2, days_ago=10)
        _map_played(session, m1, "Mirage", t1)
        _map_played(session, m2, "Mirage", t2)
        session.commit()

        form = compute_team_form(session, t1, window_days=90)
        assert "Mirage" in form.map_breakdown
        assert form.map_breakdown["Mirage"] == pytest.approx(0.5)

    def test_no_matches_returns_empty_form(self, session):
        t1 = _team(session, "TeamNew")
        session.commit()

        form = compute_team_form(session, t1, window_days=30)
        assert form.matches_played == 0
        assert form.win_rate == 0.0


class TestFormToStrengthBonus:
    def test_good_form_gives_positive_bonus(self, session):
        t1 = _team(session, "GoodTeam")
        t2 = _team(session, "BadTeam")
        for i in range(5):
            _match(session, t1, t2, winner=t1, days_ago=i * 5 + 1)
        session.commit()

        form = compute_team_form(session, t1, window_days=90)
        bonus = form_to_strength_bonus(form)
        assert bonus > 0

    def test_bad_form_gives_negative_bonus(self, session):
        t1 = _team(session, "SlumpTeam")
        t2 = _team(session, "GoodTeam2")
        for i in range(5):
            _match(session, t1, t2, winner=t2, days_ago=i * 5 + 1)
        session.commit()

        form = compute_team_form(session, t1, window_days=90)
        bonus = form_to_strength_bonus(form)
        assert bonus < 0

    def test_insufficient_data_returns_zero(self, session):
        t1 = _team(session, "TinyTeam")
        t2 = _team(session, "Foe")
        _match(session, t1, t2, winner=t1, days_ago=2)
        session.commit()

        form = compute_team_form(session, t1, window_days=90)
        # Only 1 match → bonus should be 0
        assert form_to_strength_bonus(form) == 0.0


# ---------------------------------------------------------------------------
# Integration tests — compute_player_form
# ---------------------------------------------------------------------------


class TestComputePlayerForm:
    def test_basic_aggregation(self, session):
        t1 = _team(session, "TeamP1")
        t2 = _team(session, "TeamP2")
        p = _player(session, "donk", t1)

        m1 = _match(session, t1, t2, winner=t1, days_ago=5)
        m2 = _match(session, t1, t2, winner=t1, days_ago=10)
        mp1 = _map_played(session, m1, "Mirage", t1)
        mp2 = _map_played(session, m2, "Inferno", t1)
        _player_stat(session, mp1, p, t1, rating=1.40)
        _player_stat(session, mp2, p, t1, rating=1.20)
        session.commit()

        form = compute_player_form(session, p, window_days=90)

        assert form.maps_played == 2
        assert form.avg_rating_2 == pytest.approx(1.30)
        assert form.opening_kills == 6
        assert form.opening_deaths == 4
        assert form.clutches_won == 2
        assert form.clutches_attempted == 4

    def test_window_filters(self, session):
        t1 = _team(session, "TeamQ1")
        t2 = _team(session, "TeamQ2")
        p = _player(session, "sh1ro", t1)

        m1 = _match(session, t1, t2, winner=t1, days_ago=10)
        m2 = _match(session, t1, t2, winner=t1, days_ago=200)
        mp1 = _map_played(session, m1, "Mirage", t1)
        mp2 = _map_played(session, m2, "Nuke", t1)
        _player_stat(session, mp1, p, t1, rating=1.30)
        _player_stat(session, mp2, p, t1, rating=1.10)
        session.commit()

        form_30 = compute_player_form(session, p, window_days=30)
        form_all = compute_player_form(session, p, window_days=None)

        assert form_30.maps_played == 1
        assert form_all.maps_played == 2

    def test_rating_trend_increasing(self, session):
        t1 = _team(session, "TeamR1")
        t2 = _team(session, "TeamR2")
        p = _player(session, "ZywOo", t1)

        ratings = [1.00, 1.10, 1.20, 1.30]
        for i, r in enumerate(ratings):
            m = _match(session, t1, t2, winner=t1, days_ago=(len(ratings) - i) * 5)
            mp = _map_played(session, m, "Mirage", t1, number=1)
            _player_stat(session, mp, p, t1, rating=r)
        session.commit()

        form = compute_player_form(session, p, window_days=None)
        assert form.rating_trend is not None
        assert form.rating_trend > 0

    def test_no_stats_returns_empty(self, session):
        t1 = _team(session, "TeamS")
        p = _player(session, "NewPlayer", t1)
        session.commit()

        form = compute_player_form(session, p, window_days=30)
        assert form.maps_played == 0
        assert form.avg_rating_2 is None


# ---------------------------------------------------------------------------
# Integration tests — detect_roster_changes
# ---------------------------------------------------------------------------


class TestDetectRosterChanges:
    def test_creates_membership_for_new_team(self, session):
        t1 = _team(session, "TeamX")
        t2 = _team(session, "TeamY")
        p = _player(session, "karrigan", t1)

        m = _match(session, t1, t2, winner=t1, days_ago=10)
        mp = _map_played(session, m, "Mirage", t1)
        _player_stat(session, mp, p, t1)
        session.commit()

        changes = detect_roster_changes(session)
        assert changes >= 1

    def test_no_duplicate_membership(self, session):
        t1 = _team(session, "TeamDup")
        t2 = _team(session, "TeamFoe")
        p = _player(session, "NiKo", t1)

        m1 = _match(session, t1, t2, winner=t1, days_ago=5)
        m2 = _match(session, t1, t2, winner=t1, days_ago=10)
        for m in (m1, m2):
            mp = _map_played(session, m, "Mirage", t1)
            _player_stat(session, mp, p, t1)
        session.commit()

        changes_first = detect_roster_changes(session)
        changes_second = detect_roster_changes(session)  # idempotent
        assert changes_second == 0


# ---------------------------------------------------------------------------
# Integration test — run_sync end-to-end (using tmp HTML dir)
# ---------------------------------------------------------------------------


class TestRunSync:
    def test_sync_empty_dir_no_errors(self, tmp_path, monkeypatch):
        """Sync on an empty raw dir should complete without errors."""
        # Redirect DB to tmp
        import backend.database.session as sess_mod
        import backend.config as cfg_mod

        db_url = f"sqlite:///{tmp_path / 'test.sqlite3'}"
        engine_new = create_engine(db_url, connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine_new)
        Session = sessionmaker(bind=engine_new, autoflush=False, autocommit=False)

        monkeypatch.setattr(sess_mod, "engine", engine_new)
        monkeypatch.setattr(sess_mod, "SessionLocal", Session)
        monkeypatch.setattr(cfg_mod, "DEFAULT_DATABASE_URL", db_url)

        result = run_sync(raw_dir=tmp_path)
        assert result.new_matches == 0
        assert result.errors == []
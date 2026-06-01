"""Tests for recent_form.py and sync.py.

Run with:
    pytest tests/test_recent_form_and_sync.py -v
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database.session import Base
from backend.database import models  # noqa: F401
from backend.database.models import (
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
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with Session() as s:
        yield s


def _team(session, name: str) -> Team:
    t = Team(name=name)
    session.add(t); session.flush()
    return t


def _player(session, nick: str, team: Team) -> Player:
    p = Player(nickname=nick, current_team=team)
    session.add(p); session.flush()
    return p


def _match(session, t1, t2, winner, fmt="bo1", days_ago=10) -> Match:
    m = Match(
        team1=t1, team2=t2, winner=winner, format=fmt,
        team1_score=2 if winner == t1 else 1,
        team2_score=1 if winner == t1 else 2,
        played_at=datetime.utcnow() - timedelta(days=days_ago),
    )
    session.add(m); session.flush()
    return m


def _map_played(session, match, map_name, winner, number=1) -> MapPlayed:
    mp = MapPlayed(
        match=match, map_name=map_name, map_number=number,
        team1_score=13, team2_score=10, winner=winner,
    )
    session.add(mp); session.flush()
    return mp


def _player_stat(session, map_played, player, team, rating=1.10) -> PlayerMapStat:
    stat = PlayerMapStat(
        map_played=map_played, player=player, team=team, side="both",
        kills=20, deaths=15, rating_2=rating,
        kast=72.5, adr=85.0, kpr=0.70, dpr=0.50,
        opening_kills=3, opening_deaths=2,
        clutches_won=1, clutches_attempted=2,
    )
    session.add(stat); session.flush()
    return stat


def _build_team_with_players(
    session, team_name, opponent_name, n_matches,
    player_names, player_ratings, win=True, days_start=5
):
    """Helper: create a team, players, N matches with stats."""
    t1 = _team(session, team_name)
    t2 = _team(session, opponent_name)
    players = [_player(session, nick, t1) for nick in player_names]
    for i in range(n_matches):
        winner = t1 if win else t2
        m = _match(session, t1, t2, winner, days_ago=days_start + i * 5)
        mp = _map_played(session, m, "Mirage", winner)
        for p, r in zip(players, player_ratings):
            _player_stat(session, mp, p, t1, rating=r)
    session.commit()
    return t1, t2, players


# ---------------------------------------------------------------------------
# Unit tests — pure helpers
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
        assert _momentum([True] * 10) > 0.8

    def test_momentum_all_losses(self):
        assert _momentum([False] * 10) < -0.8

    def test_momentum_empty(self):
        assert _momentum([]) == 0.0


class TestLinearTrend:
    def test_increasing(self):
        assert _linear_trend([1.0, 1.1, 1.2, 1.3]) > 0

    def test_decreasing(self):
        assert _linear_trend([1.3, 1.2, 1.1, 1.0]) < 0

    def test_flat(self):
        assert _linear_trend([1.1, 1.1, 1.1]) == pytest.approx(0.0)

    def test_too_few_points(self):
        assert _linear_trend([1.0]) is None
        assert _linear_trend([1.0, 1.1]) is None


# ---------------------------------------------------------------------------
# TeamForm — basic results
# ---------------------------------------------------------------------------


class TestComputeTeamFormBasic:
    def test_win_rate(self, session):
        t1 = _team(session, "Alpha"); t2 = _team(session, "Beta")
        _match(session, t1, t2, t1, days_ago=5)
        _match(session, t1, t2, t1, days_ago=10)
        _match(session, t1, t2, t1, days_ago=15)
        _match(session, t1, t2, t2, days_ago=20)
        session.commit()
        form = compute_team_form(session, t1, 90)
        assert form.matches_played == 4
        assert form.win_rate == pytest.approx(0.75)

    def test_window_filter(self, session):
        t1 = _team(session, "C"); t2 = _team(session, "D")
        _match(session, t1, t2, t1, days_ago=10)
        _match(session, t1, t2, t2, days_ago=200)
        session.commit()
        assert compute_team_form(session, t1, 30).matches_played == 1
        assert compute_team_form(session, t1, None).matches_played == 2

    def test_bo1_bo3_split(self, session):
        t1 = _team(session, "E"); t2 = _team(session, "F")
        _match(session, t1, t2, t1, fmt="bo1", days_ago=5)
        _match(session, t1, t2, t1, fmt="bo3", days_ago=8)
        _match(session, t1, t2, t2, fmt="bo3", days_ago=12)
        session.commit()
        form = compute_team_form(session, t1, 90)
        assert form.bo1_played == 1 and form.bo1_wins == 1
        assert form.bo3_played == 2 and form.bo3_wins == 1

    def test_no_matches(self, session):
        t1 = _team(session, "New"); session.commit()
        form = compute_team_form(session, t1, 30)
        assert form.matches_played == 0


# ---------------------------------------------------------------------------
# TeamForm — player enrichment + diagnosis
# ---------------------------------------------------------------------------


class TestPlayerEnrichment:
    def test_consistent_diagnosis(self, session):
        """Good rating + high win rate → consistent."""
        t1, _, _ = _build_team_with_players(
            session, "Consistent", "Foe1", n_matches=5,
            player_names=["p1", "p2", "p3", "p4", "p5"],
            player_ratings=[1.15, 1.10, 1.05, 1.00, 1.12],
            win=True,
        )
        form = compute_team_form(session, t1, 90)
        assert form.performance_vs_results == "consistent"
        assert form.confidence == pytest.approx(1.0)
        assert form.avg_player_rating is not None
        assert form.avg_player_rating > 1.0

    def test_overperforming_diagnosis(self, session):
        """High individual rating but bad win rate → overperforming (bad luck)."""
        t1, _, _ = _build_team_with_players(
            session, "Unlucky", "Foe2", n_matches=5,
            player_names=["q1", "q2", "q3", "q4", "q5"],
            player_ratings=[1.20, 1.15, 1.10, 1.18, 1.12],
            win=False,   # team keeps losing despite good individual stats
        )
        form = compute_team_form(session, t1, 90)
        assert form.performance_vs_results == "overperforming"
        assert form.confidence == pytest.approx(0.5)
        assert "softened" in form.diagnosis.lower()

    def test_underperforming_diagnosis(self, session):
        """Low individual rating but high win rate → underperforming (easy opponents)."""
        t1, _, _ = _build_team_with_players(
            session, "Lucky", "Foe3", n_matches=5,
            player_names=["r1", "r2", "r3", "r4", "r5"],
            player_ratings=[0.90, 0.88, 0.92, 0.85, 0.91],
            win=True,    # team wins despite weak individual stats
        )
        form = compute_team_form(session, t1, 90)
        assert form.performance_vs_results == "underperforming"
        assert form.confidence == pytest.approx(0.5)

    def test_strong_and_weak_players_detected(self, session):
        """strong_players and weak_players lists are populated correctly."""
        t1, _, _ = _build_team_with_players(
            session, "Mixed", "Foe4", n_matches=4,
            player_names=["star", "carry", "avg", "bot1", "bot2"],
            player_ratings=[1.30, 1.20, 1.00, 0.88, 0.85],
            win=True,
        )
        form = compute_team_form(session, t1, 90)
        assert "star" in form.strong_players or "carry" in form.strong_players
        assert "bot1" in form.weak_players or "bot2" in form.weak_players

    def test_no_player_stats_returns_unknown(self, session):
        """Teams with no player stats get 'unknown' diagnosis."""
        t1 = _team(session, "NoStats"); t2 = _team(session, "Foe5")
        _match(session, t1, t2, t1, days_ago=5)
        _match(session, t1, t2, t1, days_ago=10)
        _match(session, t1, t2, t2, days_ago=15)
        session.commit()
        form = compute_team_form(session, t1, 90)
        assert form.performance_vs_results == "unknown"
        assert form.avg_player_rating is None

    def test_player_ratings_dict_populated(self, session):
        t1, _, players = _build_team_with_players(
            session, "DetailTeam", "Foe6", n_matches=4,
            player_names=["x1", "x2", "x3"],
            player_ratings=[1.10, 1.05, 0.95],
            win=True,
        )
        form = compute_team_form(session, t1, 90)
        assert "x1" in form.player_ratings
        assert form.player_ratings["x1"] == pytest.approx(1.10, abs=0.01)


# ---------------------------------------------------------------------------
# form_to_strength_bonus — confidence modulation
# ---------------------------------------------------------------------------


class TestFormBonus:
    def test_overperforming_softens_penalty(self, session):
        """Overperforming team → bonus is halved compared to consistent."""
        # Build consistent team (big win rate + good ratings)
        t_consistent, _, _ = _build_team_with_players(
            session, "ConsistentBonus", "FoeB1", n_matches=6,
            player_names=["a1", "a2", "a3", "a4", "a5"],
            player_ratings=[1.15] * 5, win=True,
        )
        # Build overperforming team (bad results + good ratings)
        t_over, _, _ = _build_team_with_players(
            session, "OverBonus", "FoeB2", n_matches=6,
            player_names=["b1", "b2", "b3", "b4", "b5"],
            player_ratings=[1.20] * 5, win=False,
        )
        form_c = compute_team_form(session, t_consistent, 90)
        form_o = compute_team_form(session, t_over, 90)

        bonus_c = form_to_strength_bonus(form_c)
        bonus_o = form_to_strength_bonus(form_o)

        # Both have good player ratings but different results.
        # The overperforming team's penalty should be smaller in magnitude
        # than if confidence were 1.0 (i.e. abs value is softened)
        assert form_o.confidence == pytest.approx(0.5)
        assert abs(bonus_o) < abs(bonus_c)

    def test_insufficient_matches_zero_bonus(self, session):
        t1 = _team(session, "TinyT"); t2 = _team(session, "TinyF")
        _match(session, t1, t2, t1, days_ago=3)
        session.commit()
        form = compute_team_form(session, t1, 90)
        assert form_to_strength_bonus(form) == 0.0


# ---------------------------------------------------------------------------
# PlayerForm
# ---------------------------------------------------------------------------


class TestComputePlayerForm:
    def test_basic_aggregation(self, session):
        t1 = _team(session, "PTeam1"); t2 = _team(session, "PTeam2")
        p = _player(session, "donk", t1)
        for days, r in [(5, 1.40), (10, 1.20)]:
            m = _match(session, t1, t2, t1, days_ago=days)
            mp = _map_played(session, m, "Mirage", t1)
            _player_stat(session, mp, p, t1, rating=r)
        session.commit()
        form = compute_player_form(session, p, 90)
        assert form.maps_played == 2
        assert form.avg_rating_2 == pytest.approx(1.30)

    def test_rating_trend_increasing(self, session):
        t1 = _team(session, "Trend1"); t2 = _team(session, "Trend2")
        p = _player(session, "ZywOo", t1)
        for i, r in enumerate([1.00, 1.10, 1.20, 1.30]):
            m = _match(session, t1, t2, t1, days_ago=(4 - i) * 5)
            mp = _map_played(session, m, "Mirage", t1)
            _player_stat(session, mp, p, t1, rating=r)
        session.commit()
        form = compute_player_form(session, p, None)
        assert form.rating_trend is not None and form.rating_trend > 0

    def test_no_stats_returns_empty(self, session):
        t1 = _team(session, "Empty"); p = _player(session, "Ghost", t1)
        session.commit()
        form = compute_player_form(session, p, 30)
        assert form.maps_played == 0 and form.avg_rating_2 is None


# ---------------------------------------------------------------------------
# detect_roster_changes
# ---------------------------------------------------------------------------


class TestDetectRosterChanges:
    def test_creates_membership(self, session):
        t1 = _team(session, "RCTeam"); t2 = _team(session, "RCFoe")
        p = _player(session, "karrigan", t1)
        m = _match(session, t1, t2, t1, days_ago=10)
        mp = _map_played(session, m, "Mirage", t1)
        _player_stat(session, mp, p, t1)
        session.commit()
        assert detect_roster_changes(session) >= 1

    def test_idempotent(self, session):
        t1 = _team(session, "Idem1"); t2 = _team(session, "Idem2")
        p = _player(session, "NiKo", t1)
        m = _match(session, t1, t2, t1, days_ago=5)
        mp = _map_played(session, m, "Mirage", t1)
        _player_stat(session, mp, p, t1)
        session.commit()
        detect_roster_changes(session)
        assert detect_roster_changes(session) == 0


# ---------------------------------------------------------------------------
# run_sync — empty dir
# ---------------------------------------------------------------------------


class TestRunSync:
    def test_sync_empty_dir(self, tmp_path, monkeypatch):
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
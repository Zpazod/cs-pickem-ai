from backend.models.elo import EloSystem


def test_elo_update_moves_winner_up():
    elo = EloSystem()
    winner, loser = elo.update_pair(1500, 1500, score_a=1.0)

    assert winner > 1500
    assert loser < 1500


def test_bo3_probability_reduces_underdog_variance():
    elo = EloSystem()

    assert elo.bo3_probability(0.7) > 0.7
    assert elo.bo3_probability(0.3) < 0.3
    assert elo.bo3_probability(0.5) == 0.5


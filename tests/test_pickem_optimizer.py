from backend.pickem.optimizer import DiamondCoinOptimizer


def test_pickem_optimizer_returns_valid_pick_counts():
    teams = [f"Team {i}" for i in range(16)]
    probs = {
        team: {
            "prob_3_0": max(0.01, 0.25 - index * 0.01),
            "prob_qualify": max(0.05, 0.9 - index * 0.05),
            "prob_0_3": max(0.01, index * 0.025),
        }
        for index, team in enumerate(teams)
    }

    picks = DiamondCoinOptimizer(probs, teams).optimize()

    assert len(picks["3_0"]) == 2
    assert len(picks["qualify"]) == 6
    assert len(picks["0_3"]) == 2
    assert len(set(picks["3_0"] + picks["qualify"] + picks["0_3"])) == 10
    assert 0 <= picks["prob_diamond"] <= 1


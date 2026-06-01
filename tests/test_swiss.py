from backend.simulation.swiss import SwissMonteCarlo


def test_swiss_outputs_terminal_records_for_all_teams():
    teams = [f"Team {i}" for i in range(16)]

    def win_probability(team_a: str, team_b: str, is_bo3: bool) -> float:
        return 0.5

    result = SwissMonteCarlo(teams, win_probability, n_sims=10, seed=1).run_stage()

    assert len(result["records"]) == 16
    assert all(wins == 3 or losses == 3 for wins, losses in result["records"].values())
    assert len(result["qualified"]) == 8
    assert len(result["eliminated"]) == 8


def test_swiss_probabilities_sum_by_team():
    teams = [f"Team {i}" for i in range(16)]

    def win_probability(team_a: str, team_b: str, is_bo3: bool) -> float:
        return 0.5

    probs = SwissMonteCarlo(teams, win_probability, n_sims=100, seed=1).simulate_all()

    assert set(probs) == set(teams)
    for team_probs in probs.values():
        total = team_probs["prob_3_0"] + team_probs["prob_3_1"] + team_probs["prob_3_2"]
        total += team_probs["prob_0_3"] + team_probs["prob_1_3"] + team_probs["prob_2_3"]
        assert abs(total - 1.0) < 0.000001


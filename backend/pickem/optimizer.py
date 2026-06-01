from __future__ import annotations

from itertools import combinations


class DiamondCoinOptimizer:
    def __init__(
        self,
        simulation_probs: dict[str, dict[str, float]],
        teams: list[str],
        max_candidates_3_0: int = 8,
        max_candidates_qualify: int = 12,
        max_candidates_0_3: int = 8,
    ):
        self.probs = simulation_probs
        self.teams = teams
        self.max_candidates_3_0 = max_candidates_3_0
        self.max_candidates_qualify = max_candidates_qualify
        self.max_candidates_0_3 = max_candidates_0_3

    def optimize(self) -> dict[str, object]:
        best_probability = -1.0
        best_picks: dict[str, object] | None = None
        candidates_3_0 = self._top("prob_3_0", self.max_candidates_3_0)
        candidates_qualify = self._top("prob_qualify", self.max_candidates_qualify)
        candidates_0_3 = self._top("prob_0_3", self.max_candidates_0_3)

        for picks_3_0 in combinations(candidates_3_0, 2):
            remaining_after_30 = [team for team in self.teams if team not in picks_3_0]
            valid_0_3 = [team for team in candidates_0_3 if team in remaining_after_30]
            for picks_0_3 in combinations(valid_0_3, 2):
                valid_qualify = [team for team in candidates_qualify if team not in picks_3_0 and team not in picks_0_3]
                for picks_qualify in combinations(valid_qualify, 6):
                    diamond_probability = self.prob_at_least_5(picks_3_0, picks_qualify, picks_0_3)
                    if diamond_probability > best_probability:
                        best_probability = diamond_probability
                        best_picks = {
                            "3_0": list(picks_3_0),
                            "qualify": list(picks_qualify),
                            "0_3": list(picks_0_3),
                            "prob_diamond": diamond_probability,
                            "expected_score": self.expected_score(picks_3_0, picks_qualify, picks_0_3),
                            "explanation": self.explain(picks_3_0, picks_qualify, picks_0_3),
                        }
        if best_picks is None:
            raise ValueError("Could not optimize Pick'Em choices.")
        return best_picks

    def _top(self, key: str, limit: int) -> list[str]:
        return sorted(self.teams, key=lambda team: self.probs[team][key], reverse=True)[:limit]

    def expected_score(self, picks_3_0, picks_qualify, picks_0_3) -> float:
        return sum(self.probs[t]["prob_3_0"] for t in picks_3_0) + sum(
            self.probs[t]["prob_qualify"] for t in picks_qualify
        ) + sum(self.probs[t]["prob_0_3"] for t in picks_0_3)

    def prob_at_least_5(self, picks_3_0, picks_qualify, picks_0_3) -> float:
        probabilities = [self.probs[t]["prob_3_0"] for t in picks_3_0]
        probabilities += [self.probs[t]["prob_qualify"] for t in picks_qualify]
        probabilities += [self.probs[t]["prob_0_3"] for t in picks_0_3]
        dp = [0.0] * (len(probabilities) + 1)
        dp[0] = 1.0
        for p in probabilities:
            next_dp = [0.0] * (len(probabilities) + 1)
            for successes, value in enumerate(dp):
                next_dp[successes] += value * (1 - p)
                if successes + 1 < len(next_dp):
                    next_dp[successes + 1] += value * p
            dp = next_dp
        return sum(dp[5:])

    def explain(self, picks_3_0, picks_qualify, picks_0_3) -> str:
        safest = sorted(picks_qualify, key=lambda team: self.probs[team]["prob_qualify"], reverse=True)[:2]
        volatile = sorted(picks_3_0, key=lambda team: self.probs[team]["prob_3_0"], reverse=True)[:2]
        return (
            f"3-0 picks target ceiling ({', '.join(volatile)}), while qualify picks protect score floor "
            f"with high advancement odds ({', '.join(safest)}). 0-3 picks target the lowest survival profiles."
        )

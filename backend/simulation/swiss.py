from __future__ import annotations

import random
from collections import Counter, defaultdict
from collections.abc import Callable


WinProbabilityFn = Callable[[str, str, bool], float]


class SwissMonteCarlo:
    def __init__(
        self,
        teams: list[str],
        win_probability: WinProbabilityFn,
        n_sims: int = 10_000,
        seed: int | None = 42,
    ):
        if len(teams) % 2 != 0:
            raise ValueError("Swiss simulation requires an even number of teams.")
        self.teams = teams
        self.win_probability = win_probability
        self.n_sims = n_sims
        self.random = random.Random(seed)

    def simulate_all(self) -> dict[str, dict[str, float]]:
        counters = {team: Counter() for team in self.teams}
        for _ in range(self.n_sims):
            result = self.run_stage()
            for team, record in result["records"].items():
                key = f"{record[0]}-{record[1]}"
                counters[team][key] += 1

        probabilities: dict[str, dict[str, float]] = {}
        for team, counts in counters.items():
            probabilities[team] = {
                "prob_3_0": counts["3-0"] / self.n_sims,
                "prob_3_1": counts["3-1"] / self.n_sims,
                "prob_3_2": counts["3-2"] / self.n_sims,
                "prob_qualify": (counts["3-0"] + counts["3-1"] + counts["3-2"]) / self.n_sims,
                "prob_0_3": counts["0-3"] / self.n_sims,
                "prob_1_3": counts["1-3"] / self.n_sims,
                "prob_2_3": counts["2-3"] / self.n_sims,
            }
        return probabilities

    def run_stage(self) -> dict[str, object]:
        records = {team: [0, 0] for team in self.teams}
        history = {team: set() for team in self.teams}
        qualified: list[str] = []
        eliminated: list[str] = []

        while len(qualified) + len(eliminated) < len(self.teams):
            active = [team for team in self.teams if team not in qualified and team not in eliminated]
            groups: dict[tuple[int, int], list[str]] = defaultdict(list)
            for team in active:
                groups[tuple(records[team])].append(team)

            for record in sorted(groups):
                group = sorted(groups[record], key=lambda team: self._buchholz(team, history, records), reverse=True)
                for team_a, team_b in self._pair_group(group, history):
                    is_bo3 = records[team_a][0] == 2 or records[team_b][0] == 2 or records[team_a][1] == 2 or records[team_b][1] == 2
                    winner = self._simulate_match(team_a, team_b, is_bo3)
                    loser = team_b if winner == team_a else team_a
                    records[winner][0] += 1
                    records[loser][1] += 1
                    history[winner].add(loser)
                    history[loser].add(winner)
                    if records[winner][0] == 3:
                        qualified.append(winner)
                    if records[loser][1] == 3:
                        eliminated.append(loser)

        return {"qualified": qualified, "eliminated": eliminated, "records": records, "history": history}

    def _simulate_match(self, team_a: str, team_b: str, is_bo3: bool) -> str:
        prob_a = min(max(self.win_probability(team_a, team_b, is_bo3), 0.0), 1.0)
        return team_a if self.random.random() < prob_a else team_b

    def _buchholz(self, team: str, history: dict[str, set[str]], records: dict[str, list[int]]) -> int:
        return sum(records[opponent][0] - records[opponent][1] for opponent in history[team])

    def _pair_group(self, group: list[str], history: dict[str, set[str]]) -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []
        used: set[str] = set()
        for index, team_a in enumerate(group):
            if team_a in used:
                continue
            preferred = [team for team in group[index + 1 :] if team not in used and team not in history[team_a]]
            fallback = [team for team in group[index + 1 :] if team not in used]
            candidates = preferred or fallback
            if not candidates:
                continue
            team_b = candidates[0]
            pairs.append((team_a, team_b))
            used.add(team_a)
            used.add(team_b)
        return pairs


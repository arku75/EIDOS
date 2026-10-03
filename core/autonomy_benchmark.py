"""
EIDOS Autonomy Benchmark
========================

Dependency-free benchmark for the closed loop:
observe -> decide -> act -> verify -> learn -> retry.

It deliberately measures a small synthetic world. Passing this benchmark means
the loop can improve from feedback; it does NOT imply general intelligence or
real-world autonomy.
"""
from __future__ import annotations

from dataclasses import dataclass
import random
from typing import Dict, Tuple

State = Tuple[int, int]
ACTIONS = ("up", "down", "left", "right")


@dataclass(frozen=True)
class BenchmarkResult:
    untrained_success: float
    trained_success: float
    trained_average_steps: float
    episodes: int
    seed: int

    @property
    def passed(self) -> bool:
        return (
            self.untrained_success <= 0.10
            and self.trained_success >= 0.95
            and self.trained_average_steps <= 10.0
        )

    def to_dict(self) -> dict:
        return {
            "untrained_success": round(self.untrained_success, 4),
            "trained_success": round(self.trained_success, 4),
            "trained_average_steps": round(self.trained_average_steps, 3),
            "episodes": self.episodes,
            "seed": self.seed,
            "passed": self.passed,
        }


class GridWorld:
    """Tiny deterministic world with obstacles and effect verification."""

    def __init__(self) -> None:
        self.width = 5
        self.height = 5
        self.start: State = (0, 0)
        self.goal: State = (4, 4)
        self.blocked = {(1, 1), (1, 2), (3, 2)}
        self.state = self.start

    def reset(self) -> State:
        self.state = self.start
        return self.state

    def step(self, action: str) -> Tuple[State, float, bool, dict]:
        x, y = self.state
        candidates = {
            "up": (x, y - 1),
            "down": (x, y + 1),
            "left": (x - 1, y),
            "right": (x + 1, y),
        }
        if action not in candidates:
            raise ValueError(f"unknown action: {action}")

        nx, ny = candidates[action]
        valid = (
            0 <= nx < self.width
            and 0 <= ny < self.height
            and (nx, ny) not in self.blocked
        )
        before = self.state
        if valid:
            self.state = (nx, ny)

        done = self.state == self.goal
        reward = 1.0 if done else (-0.02 if valid else -0.15)
        info = {
            "before": before,
            "after": self.state,
            "effect_verified": self.state != before,
            "valid": valid,
            "goal": done,
        }
        return self.state, reward, done, info


class QAgent:
    """Small tabular learner used only as a measurable plasticity baseline."""

    def __init__(self, seed: int = 317, alpha: float = 0.25, gamma: float = 0.95) -> None:
        self.rng = random.Random(seed)
        self.alpha = alpha
        self.gamma = gamma
        self.q: Dict[Tuple[State, str], float] = {}

    def value(self, state: State, action: str) -> float:
        return self.q.get((state, action), 0.0)

    def choose(self, state: State, epsilon: float) -> str:
        if self.rng.random() < epsilon:
            return self.rng.choice(ACTIONS)
        values = [self.value(state, action) for action in ACTIONS]
        best = max(values)
        # deterministic tie break makes the untrained negative control stable
        return ACTIONS[values.index(best)]

    def learn(self, state: State, action: str, reward: float, next_state: State, done: bool) -> None:
        old = self.value(state, action)
        future = 0.0 if done else max(self.value(next_state, a) for a in ACTIONS)
        target = reward + self.gamma * future
        self.q[(state, action)] = old + self.alpha * (target - old)


def _evaluate(agent: QAgent, episodes: int = 100, max_steps: int = 30) -> Tuple[float, float]:
    env = GridWorld()
    successes = 0
    steps_total = 0
    for _ in range(episodes):
        state = env.reset()
        for step in range(1, max_steps + 1):
            action = agent.choose(state, epsilon=0.0)
            state, _, done, _ = env.step(action)
            if done:
                successes += 1
                steps_total += step
                break
        else:
            steps_total += max_steps
    return successes / episodes, steps_total / episodes


def run_benchmark(seed: int = 317, episodes: int = 800) -> BenchmarkResult:
    untrained = QAgent(seed=seed)
    untrained_success, _ = _evaluate(untrained)

    agent = QAgent(seed=seed)
    env = GridWorld()
    for episode in range(episodes):
        state = env.reset()
        epsilon = max(0.05, 0.55 * (1.0 - episode / episodes))
        for _ in range(40):
            action = agent.choose(state, epsilon)
            next_state, reward, done, info = env.step(action)
            # learning is based on observed effect/reward, not claimed success text
            agent.learn(state, action, reward, next_state, done)
            state = next_state
            if done:
                break

    trained_success, trained_steps = _evaluate(agent)
    return BenchmarkResult(
        untrained_success=untrained_success,
        trained_success=trained_success,
        trained_average_steps=trained_steps,
        episodes=episodes,
        seed=seed,
    )


if __name__ == "__main__":
    import json
    print(json.dumps(run_benchmark().to_dict(), indent=2))

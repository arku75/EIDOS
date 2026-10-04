"""
EIDOS Fly Lab
=============

A small, dependency-light laboratory for insect-inspired learning experiments.

This module deliberately does NOT claim to be a biological brain. It provides:
- sparse connectome loading from CSV/JSON (and optional Feather when pyarrow exists)
- a simple mushroom-body-inspired associative learner
- deterministic synthetic validation with a shuffled-label negative control
- no writes to the live EIDOS graph

Real MaleCNS/FlyWire datasets are optional external inputs and are never bundled
or silently merged into EIDOS.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple
import csv
import json
import math
import random


Edge = Tuple[int, int, float]


@dataclass(frozen=True)
class FlyValidationResult:
    train_samples: int
    test_samples: int
    classes: int
    dimensions: int
    baseline_accuracy: float
    shuffled_accuracy: float
    margin: float
    seed: int

    @property
    def passed(self) -> bool:
        return self.baseline_accuracy >= 0.80 and self.margin >= 0.35

    def to_dict(self) -> dict:
        return {
            "train_samples": self.train_samples,
            "test_samples": self.test_samples,
            "classes": self.classes,
            "dimensions": self.dimensions,
            "baseline_accuracy": round(self.baseline_accuracy, 4),
            "shuffled_accuracy": round(self.shuffled_accuracy, 4),
            "margin": round(self.margin, 4),
            "seed": self.seed,
            "passed": self.passed,
        }


class SparseConnectome:
    """Minimal sparse directed weighted graph used by Fly experiments."""

    def __init__(self, provenance: dict | None = None) -> None:
        self.outgoing: Dict[int, List[Tuple[int, float]]] = {}
        self.edge_count = 0
        self.provenance = dict(provenance or {})

    def add_edge(self, source: int, target: int, weight: float = 1.0) -> None:
        self.outgoing.setdefault(int(source), []).append((int(target), float(weight)))
        self.edge_count += 1

    @property
    def node_count(self) -> int:
        nodes = set(self.outgoing)
        for edges in self.outgoing.values():
            nodes.update(target for target, _ in edges)
        return len(nodes)

    def propagate(self, activation: Dict[int, float], decay: float = 0.65) -> Dict[int, float]:
        """One sparse propagation step; useful for controlled connectome experiments."""
        out: Dict[int, float] = {}
        for source, value in activation.items():
            for target, weight in self.outgoing.get(source, ()):
                out[target] = out.get(target, 0.0) + value * weight * decay
        return out


def _guess_column(columns: Sequence[str], candidates: Sequence[str]) -> str:
    lowered = {c.lower(): c for c in columns}
    for name in candidates:
        if name.lower() in lowered:
            return lowered[name.lower()]
    raise ValueError(f"Could not find any of {list(candidates)} in columns {list(columns)}")


def load_connectome(path: str | Path) -> SparseConnectome:
    """Load a connectome-like edge table without mutating EIDOS.

    Supported:
    - CSV/TSV with source/target/weight-like columns
    - JSON list of edge objects or [source,target,weight]
    - Feather when pyarrow is installed (optional)
    """
    path = Path(path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(path)

    graph = SparseConnectome({
        "source_path": str(path),
        "format": path.suffix.lower().lstrip("."),
        "loader": "core.fly_lab.load_connectome",
        "live_graph_mutated": False,
    })
    suffix = path.suffix.lower()

    if suffix in {".csv", ".tsv"}:
        delimiter = "\t" if suffix == ".tsv" else ","
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle, delimiter=delimiter)
            if not reader.fieldnames:
                raise ValueError("Edge table has no header")
            src = _guess_column(reader.fieldnames, ("source", "src", "pre", "pre_id", "bodyId_pre", "body_id_pre"))
            dst = _guess_column(reader.fieldnames, ("target", "dst", "post", "post_id", "bodyId_post", "body_id_post"))
            try:
                wcol = _guess_column(reader.fieldnames, ("weight", "synapses", "count", "n", "syn_count"))
            except ValueError:
                wcol = ""
            for row in reader:
                graph.add_edge(int(row[src]), int(row[dst]), float(row[wcol]) if wcol and row.get(wcol) else 1.0)
        return graph

    if suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError("JSON connectome must be a list")
        for item in data:
            if isinstance(item, dict):
                source = item.get("source", item.get("src", item.get("pre")))
                target = item.get("target", item.get("dst", item.get("post")))
                weight = item.get("weight", item.get("count", 1.0))
            else:
                source, target, *rest = item
                weight = rest[0] if rest else 1.0
            graph.add_edge(int(source), int(target), float(weight))
        return graph

    if suffix in {".feather", ".arrow"}:
        try:
            import pyarrow.feather as feather  # type: ignore
        except Exception as exc:
            raise RuntimeError("Feather support requires pyarrow; install it in a dedicated lab environment") from exc
        table = feather.read_table(path)
        columns = table.column_names
        src = _guess_column(columns, ("source", "src", "pre", "pre_id", "bodyId_pre", "body_id_pre"))
        dst = _guess_column(columns, ("target", "dst", "post", "post_id", "bodyId_post", "body_id_post"))
        try:
            wcol = _guess_column(columns, ("weight", "synapses", "count", "n", "syn_count"))
        except ValueError:
            wcol = ""
        src_values = table[src].to_pylist()
        dst_values = table[dst].to_pylist()
        weight_values = table[wcol].to_pylist() if wcol else [1.0] * len(src_values)
        for source, target, weight in zip(src_values, dst_values, weight_values):
            graph.add_edge(int(source), int(target), float(weight or 1.0))
        return graph

    raise ValueError(f"Unsupported connectome format: {suffix}")


class MushroomBodyAssociator:
    """Sparse associative learner inspired by mushroom-body coding.

    This is an engineering abstraction: sparse expansion + reward-modulated
    class weights. It is not a biological simulation.
    """

    def __init__(self, input_dim: int, classes: int, expansion: int = 8, seed: int = 317) -> None:
        if input_dim <= 0 or classes <= 1 or expansion <= 0:
            raise ValueError("invalid learner dimensions")
        self.input_dim = input_dim
        self.classes = classes
        self.expansion = expansion
        self.rng = random.Random(seed)
        self.projections: List[Tuple[int, ...]] = [
            tuple(sorted(self.rng.sample(range(input_dim), k=min(3, input_dim))))
            for _ in range(input_dim * expansion)
        ]
        self.weights = [[0.0 for _ in self.projections] for _ in range(classes)]

    def _encode(self, x: Sequence[float], sparsity: float = 0.12) -> List[int]:
        scores = []
        for i, projection in enumerate(self.projections):
            scores.append((sum(float(x[j]) for j in projection) / len(projection), i))
        keep = max(1, int(len(scores) * sparsity))
        scores.sort(reverse=True)
        return [idx for _, idx in scores[:keep]]

    def learn(self, x: Sequence[float], label: int, reward: float = 1.0, lr: float = 0.12) -> None:
        active = self._encode(x)
        for idx in active:
            self.weights[label][idx] += lr * reward

    def predict(self, x: Sequence[float]) -> int:
        active = self._encode(x)
        scores = [sum(self.weights[c][idx] for idx in active) for c in range(self.classes)]
        return max(range(self.classes), key=scores.__getitem__)


def _make_dataset(seed: int, samples_per_class: int, classes: int, dims: int) -> List[Tuple[List[float], int]]:
    rng = random.Random(seed)
    data: List[Tuple[List[float], int]] = []
    block = max(3, dims // classes)
    for label in range(classes):
        anchors = [(label * block + i) % dims for i in range(min(block, 6))]
        for _ in range(samples_per_class):
            x = [rng.uniform(0.0, 0.08) for _ in range(dims)]
            for idx in anchors:
                x[idx] = rng.uniform(0.75, 1.0)
            # independent low-amplitude noise
            for _ in range(max(1, dims // 12)):
                x[rng.randrange(dims)] += rng.uniform(0.0, 0.18)
            data.append((x, label))
    rng.shuffle(data)
    return data


def _accuracy(model: MushroomBodyAssociator, data: Iterable[Tuple[Sequence[float], int]]) -> float:
    rows = list(data)
    if not rows:
        return 0.0
    return sum(model.predict(x) == y for x, y in rows) / len(rows)


def validate_connectome_signal(
    graph: SparseConnectome,
    activation: Dict[int, float],
    *,
    decay: float = 0.65,
    seed: int = 317,
) -> dict:
    """Compare real edge propagation against a degree-preserving target shuffle.

    This validates that the supplied wiring affects the propagated signal. It
    does not claim biological equivalence or write anything into EIDOS memory.
    """
    observed = graph.propagate(activation, decay=decay)
    edges = [
        (source, target, weight)
        for source, outgoing in graph.outgoing.items()
        for target, weight in outgoing
    ]
    targets = [target for _, target, _ in edges]
    random.Random(seed).shuffle(targets)
    shuffled = SparseConnectome({"control": "target-shuffled", "seed": seed})
    for (source, _, weight), target in zip(edges, targets):
        shuffled.add_edge(source, target, weight)
    control = shuffled.propagate(activation, decay=decay)

    keys = set(observed) | set(control)
    l1_distance = sum(abs(observed.get(k, 0.0) - control.get(k, 0.0)) for k in keys)
    return {
        "nodes": graph.node_count,
        "edges": graph.edge_count,
        "active_inputs": len(activation),
        "observed_targets": len(observed),
        "control_targets": len(control),
        "l1_distance_from_shuffled": round(l1_distance, 6),
        "distinct_from_shuffled": l1_distance > 1e-9,
        "seed": seed,
        "provenance": dict(graph.provenance),
        "live_graph_mutated": False,
    }


def validate_synthetic(seed: int = 317) -> FlyValidationResult:
    """Deterministic positive-vs-shuffled control used by CI.

    Passing means the simple associative circuit learned structured data better
    than a label-shuffled negative control. It does NOT prove cognition or
    validate a real insect connectome.
    """
    classes, dims = 4, 32
    train = _make_dataset(seed, 60, classes, dims)
    test = _make_dataset(seed + 1, 25, classes, dims)

    baseline = MushroomBodyAssociator(dims, classes, seed=seed)
    for x, y in train:
        baseline.learn(x, y)

    shuffled = MushroomBodyAssociator(dims, classes, seed=seed)
    labels = [y for _, y in train]
    random.Random(seed + 99).shuffle(labels)
    for (x, _), wrong_y in zip(train, labels):
        shuffled.learn(x, wrong_y)

    baseline_acc = _accuracy(baseline, test)
    shuffled_acc = _accuracy(shuffled, test)
    return FlyValidationResult(
        train_samples=len(train),
        test_samples=len(test),
        classes=classes,
        dimensions=dims,
        baseline_accuracy=baseline_acc,
        shuffled_accuracy=shuffled_acc,
        margin=baseline_acc - shuffled_acc,
        seed=seed,
    )


if __name__ == "__main__":
    print(json.dumps(validate_synthetic().to_dict(), indent=2))

"""`model-offset-v1`, exactly as `PROTOCOL.md` registers it. Nothing here changes the package.

The incumbent's lookup stops at an unseen model under a seen `task_kind x risk` and takes that
cell's mean whatever the model. This challenger adds a ridge-shrunk, crossed model offset there,
and only there, with the offset's own uncertainty added to the cell variance.

Fit, on one fold's training rows, every harness, unweighted:

    g    = (task_kind, risk)
    z_i  = ln duration_i - mean of ln duration over g
    x_i  = e_{model_i} - p_g          (p_g: the model proportions inside g)
    A    = sum x_i x_i^T + 30 I
    beta = A^-1 sum x_i z_i           (Gauss-Jordan)
    s2   = sum (z_i - x_i^T beta)^2 / N
    C    = s2 A^-1

A group with one model has x_i = 0 for every row, so it adds no information on its own.
"""

from __future__ import annotations

import dataclasses
import math
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence

from agentpace import estimate as est
from agentpace.schema import Row

NAME = "model-offset-v1"
RIDGE = 30.0
INCUMBENT = "shrunk"


def inverse(matrix: Sequence[Sequence[float]]) -> list[list[float]]:
    """Gauss-Jordan elimination with partial pivoting."""
    size = len(matrix)
    work = [
        [*(float(value) for value in row), *(1.0 if i == j else 0.0 for j in range(size))]
        for i, row in enumerate(matrix)
    ]
    for column in range(size):
        pivot = max(range(column, size), key=lambda r: abs(work[r][column]))
        if work[pivot][column] == 0.0:
            raise ValueError("the matrix is singular")
        work[column], work[pivot] = work[pivot], work[column]
        scale = work[column][column]
        work[column] = [value / scale for value in work[column]]
        for r in range(size):
            factor = work[r][column]
            if r != column and factor != 0.0:
                work[r] = [a - factor * b for a, b in zip(work[r], work[column], strict=True)]
    return [row[size:] for row in work]


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def _times(matrix: Sequence[Sequence[float]], vector: Sequence[float]) -> list[float]:
    return [_dot(row, vector) for row in matrix]


@dataclasses.dataclass
class OffsetModel(est.Model):
    """The incumbent's fitted model plus the offset. Only `locate` differs."""

    models: tuple[str, ...]  # the K models seen in training, in index order
    beta: dict[str, float]
    covariance: list[list[float]]  # C = s2 A^-1, in index order
    proportions: dict[tuple[str, str], list[float]]  # p_g, in index order
    s2: float
    rows: int  # N

    def incumbent(self, query: Mapping[str, str | None]) -> est.Located:
        """What `base.locate` answers for this query."""
        return super().locate(query)

    def locate(self, query: Mapping[str, str | None]) -> est.Located:
        located = super().locate(query)
        model = query.get("model")
        if located.missing != "model" or model not in self.beta:
            return located
        # The walk reached the `task_kind x risk` node, so its group was in training.
        group = (str(query["task_kind"]), str(query["risk"]))
        q = [-share for share in self.proportions[group]]
        q[self.models.index(model)] += 1.0
        shift = _dot(q, [self.beta[name] for name in self.models])
        extra = _dot(q, _times(self.covariance, q))
        return dataclasses.replace(located, mu=located.mu + shift, v=located.v + extra)


def fit(train: Sequence[Row]) -> OffsetModel:
    """The incumbent's `est.fit(train, "shrunk")` and the offset, from the same training rows."""
    base = est.fit(train, INCUMBENT)
    usable = est._usable(train)
    models = tuple(sorted({row["model"] for row in usable}))
    index = {name: i for i, name in enumerate(models)}
    size = len(models)

    logs: dict[tuple[str, str], list[float]] = defaultdict(list)
    counts: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    for row in usable:
        group = (row["task_kind"], row["risk"])
        logs[group].append(math.log(row["duration_s"]))
        counts[group][row["model"]] += 1
    means = {group: sum(values) / len(values) for group, values in logs.items()}
    proportions = {
        group: [counts[group][name] / len(logs[group]) for name in models] for group in sorted(logs)
    }

    xs: list[list[float]] = []
    zs: list[float] = []
    for row in usable:
        group = (row["task_kind"], row["risk"])
        x = [-share for share in proportions[group]]
        x[index[row["model"]]] += 1.0
        xs.append(x)
        zs.append(math.log(row["duration_s"]) - means[group])

    a = [[RIDGE if i == j else 0.0 for j in range(size)] for i in range(size)]
    xz = [0.0] * size
    for x, z in zip(xs, zs, strict=True):
        for i in range(size):
            if x[i] == 0.0:
                continue
            xz[i] += x[i] * z
            row_a = a[i]
            for j in range(size):
                row_a[j] += x[i] * x[j]
    a_inverse = inverse(a)
    beta = _times(a_inverse, xz)
    s2 = sum((z - _dot(x, beta)) ** 2 for x, z in zip(xs, zs, strict=True)) / len(usable)
    covariance = [[s2 * value for value in row] for row in a_inverse]

    fields = {field.name: getattr(base, field.name) for field in dataclasses.fields(est.Model)}
    return OffsetModel(
        **fields,
        models=models,
        beta={name: beta[i] for i, name in enumerate(models)},
        covariance=covariance,
        proportions=proportions,
        s2=s2,
        rows=len(usable),
    )


def standard_errors(model: OffsetModel) -> dict[str, float]:
    """sqrt(diag C), per model."""
    return {name: math.sqrt(model.covariance[i][i]) for i, name in enumerate(model.models)}

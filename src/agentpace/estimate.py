"""All the math: from past runs to P50/P80/P90 for a plan of tasks.

Only pre-run facts predict a duration. `tool_calls`, `tokens`, `retries` and `outcome` describe
what happened during a run, so using them would leak the answer. Stored `size` is measured after
the run too, so it joins only with `use_size=True`.

A run's log duration is modeled as

    log d = mu(cell) + delta(cell) + b(session) + e

mu      Cell mean, shrunk toward its parent cell. Thin cells lean on their parents.
delta   Uncertainty about mu. Drawn once per cell, per simulation, not per task. If each task drew
        its own, a 100-task plan would average the uncertainty away.
b       Shared by every task in one session. Same repo, same model, same day. Without it, totals
        of many tasks concentrate and ranges come out falsely narrow.
e       Within-session noise, resampled from real residuals so the real tail shape survives.

Rows can be weighted by 1/sqrt(runs in that session), because one automated session can hold
hundreds of near-identical runs. `n_eff` is the Kish effective sample size under those weights, and
it is what pooling thresholds use. Without weights it equals n. The published backtest found no
measurable gain from weighting, so the default is unweighted, and the option stays as a candidate.

A plan total is then simulated:

    T = max(critical path, total work / P)

P is sampled from what real sessions at that concurrency achieved. This is a lower-bound makespan
with a learned throughput term, not a task scheduler.
"""

from __future__ import annotations

import math
import random
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from agentpace.schema import Row

QUANTILES = (0.5, 0.8, 0.9)
POOL_MIN = 30  # cells below this effective size are pooled, widened and warned about
MIN_ROWS = 10
MIN_SESSION_RUNS = 5  # sessions smaller than this are too noisy to measure a session effect
MIN_BUCKET_SESSIONS = 5
DEFAULT_DRAWS = 20_000
DEFAULT_SEED = 20261107


@dataclass(frozen=True)
class Method:
    """How cells are formed. Every backtest candidate is one of these, simplest first."""

    name: str
    depth: int  # 0 global, 1 kind, 3 model x kind x risk
    shrink: bool
    session_weights: bool


METHODS = (
    Method("global", depth=0, shrink=False, session_weights=False),
    Method("kind", depth=1, shrink=False, session_weights=False),
    Method("backoff", depth=3, shrink=False, session_weights=False),
    Method("shrunk", depth=3, shrink=True, session_weights=False),
    Method("weighted", depth=3, shrink=True, session_weights=True),
)
METHOD_BY_NAME = {method.name: method for method in METHODS}
# Set by the pre-registered selection in `agentpace backtest`. A test keeps them in step.
DEFAULT_METHOD = "shrunk"


def quantile(ordered: Sequence[float], q: float) -> float:
    """Linear interpolation between order statistics (Hyndman-Fan type 7)."""
    if not ordered:
        raise ValueError("no values")
    position = q * (len(ordered) - 1)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _kish(weights: Sequence[float]) -> float:
    total = sum(weights)
    return total * total / sum(w * w for w in weights) if total else 0.0


def _weighted_mean(values: Sequence[float], weights: Sequence[float]) -> float:
    return sum(v * w for v, w in zip(values, weights, strict=True)) / sum(weights)


def _weighted_variance(values: Sequence[float], weights: Sequence[float], mean: float) -> float:
    return sum(w * (v - mean) ** 2 for v, w in zip(values, weights, strict=True)) / sum(weights)


@dataclass
class Pool:
    """Residuals to resample from, with cumulative weights for fast draws."""

    values: list[float]
    cumulative: list[float]

    @classmethod
    def build(cls, pairs: Sequence[tuple[float, float]]) -> Pool:
        total = sum(weight for _, weight in pairs)
        center = sum(value * weight for value, weight in pairs) / total if total else 0.0
        values: list[float] = []
        cumulative: list[float] = []
        running = 0.0
        for value, weight in pairs:
            running += weight
            values.append(value - center)  # the location belongs to mu, not to the pool
            cumulative.append(running)
        return cls(values, cumulative)

    def draw(self, rng: random.Random, count: int) -> list[float]:
        if not self.values:
            return [0.0] * count
        return rng.choices(self.values, cum_weights=self.cumulative, k=count)


@dataclass
class Node:
    """One cell at one level of the hierarchy."""

    key: tuple[str, ...]
    n: int
    n_eff: float
    mean: float
    mu: float
    v: float  # variance of our belief about mu
    sigma2: float
    pairs: list[tuple[float, float]] = field(default_factory=list)
    pool: Pool | None = None


@dataclass(frozen=True)
class Located:
    """The answer to "which cell does this task fall in?"."""

    mu: float
    v: float
    pool: Pool
    n: int
    n_eff: float
    label: str
    pooled_from: str
    thin: bool


@dataclass(frozen=True)
class ThroughputBucket:
    low: int
    high: int
    values: tuple[float, ...]

    @property
    def median(self) -> float:
        return statistics.median(self.values)


@dataclass
class Model:
    """A fitted estimator."""

    method: Method
    path: tuple[str, ...]
    root: Node
    nodes: dict[tuple[str, ...], Node]
    tau2: dict[int, float]
    session_effects: list[float]
    buckets: list[ThroughputBucket]
    rows_used: int
    rows_excluded: int

    def locate(self, query: Mapping[str, str | None]) -> Located:
        """Walk down the hierarchy as far as the query and the data allow."""
        node = self.root
        mu, v = node.mu, node.v
        pool_node = node
        prefix: tuple[str, ...] = ()
        missing = False
        for depth, name in enumerate(self.path, start=1):
            value = query.get(name)
            if value is None:
                break
            prefix = (*prefix, value)
            child = self.nodes.get(prefix)
            if child is None:
                # Never seen: the parent's mean, plus the spread between sibling cells.
                v += self.tau2.get(depth, 0.0)
                missing = True
                break
            node, mu, v = child, child.mu, child.v
            if child.n_eff >= POOL_MIN:
                pool_node = child
        assert pool_node.pool is not None
        return Located(
            mu=mu,
            v=v,
            pool=pool_node.pool,
            n=0 if missing else node.n,
            n_eff=0.0 if missing else node.n_eff,
            label=" x ".join(prefix) or "all",
            pooled_from=" x ".join(pool_node.key) or "all",
            thin=missing or node.n_eff < POOL_MIN,
        )

    def throughput(self, cap: int) -> ThroughputBucket | None:
        if not self.buckets:
            return None
        # A cap between observed peaks uses the nearest bucket at or below it.
        reachable = [bucket for bucket in self.buckets if bucket.low <= cap]
        return reachable[-1] if reachable else self.buckets[0]


def _session_weights(rows: Sequence[Row], enabled: bool) -> list[float]:
    if not enabled:
        return [1.0] * len(rows)
    sizes = Counter(row["batch"] for row in rows)
    return [1.0 / math.sqrt(sizes[row["batch"]]) for row in rows]


def _make_node(key: tuple[str, ...], members: Sequence[tuple[float, float]]) -> Node:
    logs = [value for value, _ in members]
    weights = [weight for _, weight in members]
    mean = _weighted_mean(logs, weights)
    return Node(
        key=key,
        n=len(members),
        n_eff=_kish(weights),
        mean=mean,
        mu=mean,
        v=0.0,
        sigma2=_weighted_variance(logs, weights, mean),
    )


def _between_variance(children: Mapping[tuple[str, ...], Sequence[Node]], sigma2: float) -> float:
    """Method-of-moments estimate of the spread between cells, clamped at zero."""
    q = c = 0.0
    df = 0
    for siblings in children.values():
        h = [node.n_eff for node in siblings]
        total = sum(h)
        center = sum(a * node.mean for a, node in zip(h, siblings, strict=True)) / total
        q += sum(a * (node.mean - center) ** 2 for a, node in zip(h, siblings, strict=True))
        c += total - sum(a * a for a in h) / total
        df += len(siblings) - 1
    return max(0.0, (q - df * sigma2) / c) if c > 0 else 0.0


def _fit_means(
    keys: Sequence[tuple[str, ...]],
    logs: Sequence[float],
    weights: Sequence[float],
    depth: int,
    shrink: bool,
) -> tuple[Node, dict[tuple[str, ...], Node], dict[int, float]]:
    root = _make_node((), list(zip(logs, weights, strict=True)))
    root.v = root.sigma2 / root.n_eff
    nodes: dict[tuple[str, ...], Node] = {}
    tau2: dict[int, float] = {}
    for level in range(1, depth + 1):
        groups: dict[tuple[str, ...], list[tuple[float, float]]] = defaultdict(list)
        for key, value, weight in zip(keys, logs, weights, strict=True):
            groups[key[:level]].append((value, weight))
        level_nodes = {key: _make_node(key, members) for key, members in sorted(groups.items())}
        within = sum(n.sigma2 * n.n_eff for n in level_nodes.values())
        sigma2 = within / sum(n.n_eff for n in level_nodes.values())
        children: dict[tuple[str, ...], list[Node]] = defaultdict(list)
        for key, node in level_nodes.items():
            children[key[:-1]].append(node)
        tau2[level] = _between_variance(children, sigma2) if shrink else 0.0
        k = sigma2 / tau2[level] if tau2[level] > 0 else math.inf
        for key, node in level_nodes.items():
            parent = nodes.get(key[:-1], root)
            if shrink:
                w = node.n_eff / (node.n_eff + k)
                belief = sigma2 / (node.n_eff + k) if math.isfinite(k) else 0.0
            else:
                w = 1.0 if node.n_eff >= POOL_MIN else 0.0
                belief = sigma2 / node.n_eff if w else 0.0
            node.mu = w * node.mean + (1 - w) * parent.mu
            node.v = belief + (1 - w) ** 2 * parent.v
        nodes.update(level_nodes)
    return root, nodes, tau2


def _session_split(
    rows: Sequence[Row], residuals: Sequence[float]
) -> tuple[list[float], list[float | None]]:
    """Split residuals into session effects and within-session noise, so neither counts twice.

    Noise in a session mean is not a session effect, so effects are scaled down by the share of
    variance that is real. Centering removes one degree of freedom, so noise is scaled back up.
    """
    groups: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        groups[row["batch"]].append(index)
    means = {batch: statistics.fmean(residuals[i] for i in ids) for batch, ids in groups.items()}
    noise: list[float | None] = [None] * len(rows)
    within = []
    for batch, ids in groups.items():
        size = len(ids)
        if size < 2:
            continue
        scale = math.sqrt(size / (size - 1))
        for i in ids:
            noise[i] = (residuals[i] - means[batch]) * scale
            within.append(noise[i])
    large = [batch for batch, ids in groups.items() if len(ids) >= MIN_SESSION_RUNS]
    if len(large) < MIN_SESSION_RUNS or not within:
        return [], list(residuals)
    observed = [means[batch] for batch in large]
    variance = statistics.pvariance(observed, statistics.fmean(observed))
    sigma2 = statistics.fmean(value * value for value in within if value is not None)
    sampling = statistics.fmean(sigma2 / len(groups[batch]) for batch in large)
    keep = math.sqrt(max(0.0, 1 - sampling / variance)) if variance > 0 else 0.0
    return sorted(value * keep for value in observed), noise


def _throughput(rows: Sequence[Row]) -> list[ThroughputBucket]:
    """What sessions achieved: work divided by span, by peak concurrency, kept non-decreasing."""
    groups: dict[str, list[Row]] = defaultdict(list)
    for row in rows:
        groups[row["batch"]].append(row)
    by_peak: dict[int, list[float]] = defaultdict(list)
    for members in groups.values():
        if len(members) < 2:
            continue
        work = sum(row["duration_s"] for row in members)
        first = min(row["offset_s"] for row in members)
        last = max(row["offset_s"] + row["duration_s"] for row in members)
        peak = max(row["parallel_agents"] for row in members)
        by_peak[peak].append(work / max(last - first, 1))
    merged: list[tuple[int, int, list[float]]] = []
    for peak in sorted(by_peak):
        values = by_peak[peak]
        if merged and len(merged[-1][2]) < MIN_BUCKET_SESSIONS:
            low, _, held = merged.pop()
            merged.append((low, peak, held + values))
        else:
            merged.append((peak, peak, list(values)))
    if len(merged) > 1 and len(merged[-1][2]) < MIN_BUCKET_SESSIONS:
        _, high, tail = merged.pop()
        low, _, held = merged.pop()
        merged.append((low, high, held + tail))
    # Pool adjacent violators: more agents must not look worse than fewer.
    index = 0
    while index + 1 < len(merged):
        left, right = merged[index], merged[index + 1]
        if statistics.median(left[2]) > statistics.median(right[2]):
            merged[index : index + 2] = [(left[0], right[1], left[2] + right[2])]
            index = max(index - 1, 0)
        else:
            index += 1
    return [ThroughputBucket(low, high, tuple(sorted(values))) for low, high, values in merged]


def _usable(rows: Iterable[Row]) -> list[Row]:
    return [row for row in rows if row["outcome"] == "completed" and row["duration_s"] > 0]


def fit(rows: Sequence[Row], method: str = DEFAULT_METHOD, use_size: bool = False) -> Model:
    chosen = METHOD_BY_NAME[method]
    usable = _usable(rows)
    if len(usable) < MIN_ROWS:
        raise ValueError(f"need at least {MIN_ROWS} completed runs, found {len(usable)}")
    path = ("task_kind", "size", "risk", "model") if use_size else ("task_kind", "risk", "model")
    depth = min(chosen.depth + 1, len(path)) if use_size and chosen.depth else chosen.depth
    keys = [tuple(row[name] for name in path) for row in usable]
    logs = [math.log(row["duration_s"]) for row in usable]
    weights = _session_weights(usable, chosen.session_weights)
    root, nodes, tau2 = _fit_means(keys, logs, weights, depth, chosen.shrink)
    finest = [nodes.get(key[:depth], root) if depth else root for key in keys]
    residuals = [value - node.mu for value, node in zip(logs, finest, strict=True)]
    effects, noise = _session_split(usable, residuals)
    for key, value, weight in zip(keys, noise, weights, strict=True):
        if value is None:
            continue
        root.pairs.append((value, weight))
        for level in range(1, depth + 1):
            nodes[key[:level]].pairs.append((value, weight))
    for node in (root, *nodes.values()):
        node.pool = Pool.build(node.pairs)
    return Model(
        method=chosen,
        path=path[:depth],
        root=root,
        nodes=nodes,
        tau2=tau2,
        session_effects=effects,
        buckets=_throughput(usable),
        rows_used=len(usable),
        rows_excluded=len(rows) - len(usable),
    )


@dataclass(frozen=True)
class Task:
    id: str
    kind: str
    risk: str = "normal"
    size: str | None = None
    model: str | None = None
    depends_on: tuple[str, ...] = ()


@dataclass(frozen=True)
class TaskRange:
    id: str
    p50: float
    p80: float
    p90: float
    cell: str
    n: int
    n_eff: float
    criticality: float


@dataclass(frozen=True)
class PlanRange:
    p50: float
    p80: float
    p90: float
    tasks: tuple[TaskRange, ...]
    critical_path: tuple[str, ...]
    critical_path_share: float
    agent_cap: int
    effective_parallelism: float | None
    parallelism_sessions: int
    throughput_bound_share: float
    method: str
    size_used: bool
    rows_used: int
    rows_excluded: int
    draws: int
    seed: int
    warnings: tuple[str, ...]


def topological_order(tasks: Sequence[Task]) -> list[Task]:
    by_id = {task.id: task for task in tasks}
    if len(by_id) != len(tasks):
        raise ValueError("task ids must be unique")
    waiting = {task.id: len(set(task.depends_on)) for task in tasks}
    followers: dict[str, list[str]] = defaultdict(list)
    for task in tasks:
        for dependency in set(task.depends_on):
            if dependency not in by_id:
                raise ValueError(f"task {task.id!r} depends on unknown task {dependency!r}")
            followers[dependency].append(task.id)
    ready = sorted(task_id for task_id, count in waiting.items() if count == 0)
    order: list[Task] = []
    while ready:
        current = ready.pop(0)
        order.append(by_id[current])
        for follower in sorted(followers[current]):
            waiting[follower] -= 1
            if waiting[follower] == 0:
                ready.append(follower)
    if len(order) != len(tasks):
        raise ValueError("the task graph has a cycle")
    return order


def _clamped_exp(value: float) -> float:
    return math.exp(min(value, 30.0))


def _warnings(model: Model, tasks: Sequence[Task], located: Mapping[str, Located]) -> list[str]:
    notes: list[str] = []
    seen: set[str] = set()
    for task in tasks:
        cell = located[task.id]
        if cell.thin and cell.label not in seen:
            seen.add(cell.label)
            notes.append(
                f"cell {cell.label}: n={cell.n}, effective n={cell.n_eff:.1f}, below {POOL_MIN}. "
                f"Pooled with {cell.pooled_from} and interval widened."
            )
    if "size" not in model.path and any(task.size for task in tasks):
        notes.append(
            "size ignored: stored size is measured after a run, so it is left out unless you "
            "pass --use-size."
        )
    return notes


def estimate(
    model: Model,
    tasks: Sequence[Task],
    agent_cap: int,
    draws: int = DEFAULT_DRAWS,
    seed: int = DEFAULT_SEED,
) -> PlanRange:
    """Simulate a plan. Every result is a range, so no single number can be misread as a promise."""
    if agent_cap < 1:
        raise ValueError("agent_cap must be at least 1")
    if not tasks:
        raise ValueError("a plan needs at least one task")
    if draws < 100:
        raise ValueError("draws must be at least 100")
    order = topological_order(tasks)
    rng = random.Random(seed)
    located = {
        task.id: model.locate(
            {"task_kind": task.kind, "risk": task.risk, "model": task.model, "size": task.size}
        )
        for task in order
    }
    notes = _warnings(model, order, located)

    # Shared draws first, so tasks in one cell and one session really do move together.
    effects = model.session_effects
    shared = rng.choices(effects, k=draws) if effects else [0.0] * draws
    cell_shift: dict[str, list[float]] = {}
    for cell in located.values():
        if cell.label not in cell_shift:
            spread = math.sqrt(cell.v)
            cell_shift[cell.label] = [rng.gauss(0.0, spread) for _ in range(draws)]
    bucket = model.throughput(agent_cap)
    if bucket is None:
        notes.append("no multi-run sessions to learn parallelism from: assuming one at a time.")
        rates = [1.0] * draws
    else:
        rates = [min(rate, float(agent_cap)) for rate in rng.choices(bucket.values, k=draws)]

    sampled: dict[str, list[float]] = {}
    for task in order:
        cell = located[task.id]
        noise = cell.pool.draw(rng, draws)
        shift = cell_shift[cell.label]
        sampled[task.id] = [
            _clamped_exp(cell.mu + delta + b + e)
            for delta, b, e in zip(shift, shared, noise, strict=True)
        ]

    totals: list[float] = []
    critical: Counter[str] = Counter()
    paths: Counter[tuple[str, ...]] = Counter()
    bound = 0
    for i in range(draws):
        finish: dict[str, float] = {}
        previous: dict[str, str | None] = {}
        work = 0.0
        for task in order:
            duration = sampled[task.id][i]
            work += duration
            start = 0.0
            link: str | None = None
            for dependency in task.depends_on:
                if finish[dependency] > start:
                    start, link = finish[dependency], dependency
            finish[task.id] = start + duration
            previous[task.id] = link
        tail = max(finish, key=lambda task_id: finish[task_id])
        longest = finish[tail]
        chain: list[str] = []
        cursor: str | None = tail
        while cursor is not None:
            chain.append(cursor)
            critical[cursor] += 1
            cursor = previous[cursor]
        paths[tuple(reversed(chain))] += 1
        throughput = work / rates[i]
        if throughput > longest:
            bound += 1
        totals.append(max(longest, throughput))

    totals.sort()
    task_ranges = []
    for task in tasks:
        ordered = sorted(sampled[task.id])
        cell = located[task.id]
        p50, p80, p90 = (quantile(ordered, q) for q in QUANTILES)
        task_ranges.append(
            TaskRange(
                id=task.id,
                p50=p50,
                p80=p80,
                p90=p90,
                cell=cell.label,
                n=cell.n,
                n_eff=cell.n_eff,
                criticality=critical[task.id] / draws,
            )
        )
    path, path_count = max(paths.items(), key=lambda item: (item[1], item[0]))
    total_p50, total_p80, total_p90 = (quantile(totals, q) for q in QUANTILES)
    return PlanRange(
        p50=total_p50,
        p80=total_p80,
        p90=total_p90,
        tasks=tuple(task_ranges),
        critical_path=path,
        critical_path_share=path_count / draws,
        agent_cap=agent_cap,
        effective_parallelism=None if bucket is None else min(bucket.median, float(agent_cap)),
        parallelism_sessions=0 if bucket is None else len(bucket.values),
        throughput_bound_share=bound / draws,
        method=model.method.name,
        size_used="size" in model.path,
        rows_used=model.rows_used,
        rows_excluded=model.rows_excluded,
        draws=draws,
        seed=seed,
        warnings=tuple(notes),
    )


def cell_quantiles(
    model: Model, query: Mapping[str, str | None], draws: int, rng: random.Random
) -> tuple[float, float, float]:
    """P50/P80/P90 for one task in a new session. Used by the backtest."""
    cell = model.locate(query)
    effects = model.session_effects
    shared = rng.choices(effects, k=draws) if effects else [0.0] * draws
    noise = cell.pool.draw(rng, draws)
    spread = math.sqrt(cell.v)
    values = sorted(
        _clamped_exp(cell.mu + rng.gauss(0.0, spread) + b + e)
        for b, e in zip(shared, noise, strict=True)
    )
    p50, p80, p90 = (quantile(values, q) for q in QUANTILES)
    return p50, p80, p90


def tasks_from_json(document: Any) -> tuple[list[Task], int]:
    """Validate a plan document. Errors name the problem, never the document's text."""
    from agentpace import schema

    if not isinstance(document, dict) or not isinstance(document.get("tasks"), list):
        raise ValueError('a plan is an object with a "tasks" list')
    cap = document.get("agent_cap", 1)
    if not isinstance(cap, int) or isinstance(cap, bool) or cap < 1:
        raise ValueError("agent_cap must be a positive integer")
    plan_model = document.get("model")
    tasks = []
    for position, item in enumerate(document["tasks"]):
        where = f"task #{position + 1}"
        if not isinstance(item, dict) or not isinstance(item.get("id"), (str, int)):
            raise ValueError(f"{where}: needs an id")
        kind = item.get("kind")
        if kind not in schema.enum("task_kind"):
            raise ValueError(f"{where}: kind must be one of {schema.enum('task_kind')}")
        risk = item.get("risk", "normal")
        if risk not in schema.enum("risk"):
            raise ValueError(f"{where}: risk must be one of {schema.enum('risk')}")
        size = item.get("size")
        if size is not None and size not in schema.enum("size"):
            raise ValueError(f"{where}: size must be one of {schema.enum('size')}")
        dependencies = item.get("depends_on", [])
        if not isinstance(dependencies, list):
            raise ValueError(f"{where}: depends_on must be a list")
        tasks.append(
            Task(
                id=str(item["id"]),
                kind=kind,
                risk=risk,
                size=size,
                model=item.get("model", plan_model),
                depends_on=tuple(str(dependency) for dependency in dependencies),
            )
        )
    return tasks, cap

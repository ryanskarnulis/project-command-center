"""The frontier tier's pure parts: scenarios, grading, fingerprints (#408).

The gate (``test_agent_evals.py``) holds floors, and its own comment says what
they are: regression tripwires, not aspirations. A scenario the model cannot
do yet has nowhere to live there: it fails the gate, or it gets an ``xfail``
nobody watches move. This tier is where such scenarios live. It **measures
and never fails on a score**: one sample per wording, a whole-task pass count
with its Wilson interval, and a rubric score for partial credit, written to
the report so a run can be compared with the last
(``scripts/frontier_report.py``, ``docs/agent-frontier.md``).

Ported from chess's ``tests/frontier.py`` (chess#318, chess#339), cut to
what PCC measures: no board, no speech tally (claims are read by
``tests/claims.py`` and are a checkpoint here), and a fixed failure-mode
vocabulary instead of free-text signatures.

The rules that keep the number honest:

- **Partial credit is deterministic.** A scenario names checkpoints, each a
  predicate over what the sample did (the DB before and after each turn, the
  tool calls, the claims its replies made). A whole-task pass is every
  checkpoint on one sample, with every run completed. The rubric score (the
  fraction met) is what moves while whole-task passes are still near zero.
- **A failure has one of a few names** (``FAILURE_MODES``). Each checkpoint
  says which mode its miss is; a sample's mode is the first missed
  checkpoint's, after the two that explain everything else (a run that did
  not complete; a thread that made no tool call at all). Counted per
  scenario, so "what goes wrong" is a short table rather than a list of
  messages.
- **Held-out and dev wordings.** Each scenario's wordings are written as one
  pool and dealt to the splits by a seeded shuffle (``deal``), so easy
  phrasings cannot gather on one side. Held-out is the number of record; dev
  is where a prompt change is iterated. An improvement on dev alone is not
  believed.
- **A number is comparable only with the same thing.** ``Scenario.fingerprint``
  hashes a split's wordings, the checkpoint names and a hand-bumped revision;
  the history compares two runs of a scenario only when it is the same.

Nothing here imports the app or needs a GPU: ``test_frontier.py`` is its spec
and runs in CI. The corpus (``frontier_corpus.py``) and the live module
(``test_agent_frontier.py``) supply the scenarios and the model.
"""

from __future__ import annotations

import hashlib
import json
import random
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from tests.claims import Tally
from tests.evalstats import wilson_interval

# One sample per wording, and this many wordings per split (chess#339: at the
# profile's temperature repeats of one wording mostly agree, so a scenario's
# information is in its wordings, not its repeats).
WORDINGS_PER_SPLIT = 5
SPLITS = ("heldout", "dev")

# The names a missed sample can have. Small and fixed on purpose: a table of
# these is readable across runs, and a new name is a decision, not a typo.
STOPPED = "stopped"  # a run ended on a limit or failure stop, not "completed"
FROM_MEMORY = "answered_from_memory"  # the whole thread made no tool call
DID_NOT_ASK = "did_not_ask"  # acted (or guessed) where the ask was ambiguous
MISSING_WRITE = "missing_write"  # part of the ask never happened
WRONG_TARGET = "wrong_target"  # the write landed on the wrong row
WRONG_VALUE = "wrong_value"  # the right row, the wrong field value
COLLATERAL = "collateral"  # something outside the ask changed
UNBACKED_CLAIM = "unbacked_claim"  # the reply said what the run didn't do
FAILURE_MODES = (
    STOPPED,
    FROM_MEMORY,
    DID_NOT_ASK,
    MISSING_WRITE,
    WRONG_TARGET,
    WRONG_VALUE,
    COLLATERAL,
    UNBACKED_CLAIM,
)
# What a checkpoint may name: a stop is read off the whole sample only.
CHECKPOINT_MODES = FAILURE_MODES[1:]


class ScenarioError(Exception):
    """The scenario is broken, not the model: a checkpoint that raised. It
    fails the item like a harness bug rather than being scored."""


# --- what a scenario is ------------------------------------------------------------


@dataclass(frozen=True)
class Variant:
    """One wording of a scenario: the user's asks, in order (one per turn)."""

    name: str
    asks: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.asks or not all(ask.strip() for ask in self.asks):
            raise ValueError(f"variant {self.name!r} has an empty ask")


@dataclass(frozen=True)
class Checkpoint:
    """One named, deterministic piece of the task, and what its miss means."""

    name: str
    check: Callable[[Any], bool]
    mode: str

    def __post_init__(self) -> None:
        if self.mode not in CHECKPOINT_MODES:
            raise ValueError(f"checkpoint {self.name!r}: unknown mode {self.mode!r}")


@dataclass(frozen=True)
class Scenario:
    """A hard task, its wordings and its checkpoints.

    ``wordings`` is the pool, each entry the asks of one wording (one per
    turn); ``deal`` splits it into ``dev`` and ``heldout``. ``setup`` seeds the sample's world (the corpus passes its ``Sample``);
    ``history`` names a stored thread the asks continue (the corpus maps the
    name to text; the name, not the text, is fingerprinted, since that text
    carries today's dates). ``notes`` keeps where a scenario came from: the
    gate's known-red before-numbers moved here keep their reasons and issues.
    """

    name: str
    tier: int
    why: str
    wordings: tuple[tuple[str, ...], ...]
    checkpoints: tuple[Checkpoint, ...]
    setup: Callable[[Any], None] | None = None
    history: str | None = None
    notes: str = ""
    # Bumped by hand when what a number means changes in a way the
    # fingerprint cannot see: a setup, a stored history's text, or a
    # checkpoint's logic under its old name.
    revision: int = 1

    @property
    def dev(self) -> tuple[Variant, ...]:
        return deal(self.name, self.wordings)[0]

    @property
    def heldout(self) -> tuple[Variant, ...]:
        return deal(self.name, self.wordings)[1]

    def variants(self, split: str) -> tuple[Variant, ...]:
        if split == "dev":
            return self.dev
        if split == "heldout":
            return self.heldout
        raise ValueError(f"unknown split {split!r}: dev or heldout")

    def fingerprint(self, split: str) -> str:
        """What a ``split`` number measured: its wordings, the checkpoints
        that grade them, the stored history's name and the revision. Not the
        tier, the ``why`` or the notes, which say nothing about what was
        measured. The history compares two runs of a scenario only when this
        is the same for both."""
        measured = {
            "revision": self.revision,
            "history": self.history,
            "checkpoints": [c.name for c in self.checkpoints],
            "variants": [[v.name, list(v.asks)] for v in self.variants(split)],
        }
        return hashlib.sha256(json.dumps(measured, sort_keys=True).encode()).hexdigest()[:12]


def deal(
    name: str, pool: Sequence[tuple[str, ...]], *, salt: str = "408"
) -> tuple[tuple[Variant, ...], tuple[Variant, ...]]:
    """A scenario's wordings dealt to (dev, heldout): a seeded shuffle of the
    pool, alternating, so where a wording lands is chance rather than choice
    and stays put while the pool does. Each wording is named by its position
    in the pool (``w03``), so a reordering is a fingerprint change."""
    if len(pool) != 2 * WORDINGS_PER_SPLIT:
        raise ValueError(f"{name}: {len(pool)} wordings, want {2 * WORDINGS_PER_SPLIT}")
    variants = [Variant(f"w{index:02d}", tuple(asks)) for index, asks in enumerate(pool)]
    random.Random(f"{salt}:{name}").shuffle(variants)
    dev = tuple(sorted(variants[0::2], key=lambda v: v.name))
    heldout = tuple(sorted(variants[1::2], key=lambda v: v.name))
    return dev, heldout


# --- grading ------------------------------------------------------------------------


@dataclass(frozen=True)
class Grade:
    """One sample against its checkpoints. ``stopped``: a run did not
    complete; ``silent``: the whole thread made no tool call."""

    hits: dict[str, bool]
    modes: dict[str, str] = field(default_factory=dict)
    stopped: bool = False
    silent: bool = False

    @property
    def whole(self) -> bool:
        return bool(self.hits) and all(self.hits.values()) and not self.stopped

    @property
    def score(self) -> float:
        return sum(self.hits.values()) / len(self.hits) if self.hits else 0.0

    @property
    def missed(self) -> list[str]:
        return [name for name, hit in self.hits.items() if not hit]

    @property
    def failure_mode(self) -> str | None:
        """The sample's one failure name, or None for a whole pass."""
        return failure_mode(self.missed, self.modes, stopped=self.stopped, silent=self.silent)


def failure_mode(
    missed: Sequence[str], modes: Mapping[str, str], *, stopped: bool, silent: bool
) -> str | None:
    """Normalise a miss to one ``FAILURE_MODES`` name. A run that did not
    complete explains the rest, then a thread that never called a tool (it
    answered from memory: #405's self-poisoning), then the first missed
    checkpoint's own mode. A sample with nothing missed and every run
    completed has none."""
    if stopped:
        return STOPPED
    if not missed:
        return None
    if silent:
        return FROM_MEMORY
    return modes.get(missed[0], MISSING_WRITE)


def grade(episode: Any, checkpoints: Sequence[Checkpoint], *, stopped: bool, silent: bool) -> Grade:
    """Every checkpoint, each on its own: one that raises is a broken scenario
    and says which, rather than scoring as a miss."""
    hits: dict[str, bool] = {}
    for checkpoint in checkpoints:
        try:
            hits[checkpoint.name] = bool(checkpoint.check(episode))
        except Exception as exc:
            raise ScenarioError(f"checkpoint {checkpoint.name} raised: {exc!r}") from exc
    return Grade(hits, {c.name: c.mode for c in checkpoints}, stopped=stopped, silent=silent)


def missed_all(checkpoints: Sequence[Checkpoint]) -> Grade:
    """The grade of a scored sample with nothing to grade: a run the route
    failed (a tool that raised) before the thread finished."""
    return Grade(
        {c.name: False for c in checkpoints},
        {c.name: c.mode for c in checkpoints},
        stopped=True,
    )


# --- the result -----------------------------------------------------------------------


@dataclass
class FrontierResult:
    """One scenario's measurement on one split. Never a verdict."""

    scenario: str
    tier: int
    split: str
    checkpoints: tuple[str, ...]
    fingerprint: str
    grades: list[Grade] = field(default_factory=list)
    samples: list[dict[str, Any]] = field(default_factory=list)
    infra: int = 0
    claims: Tally = field(default_factory=Tally)

    def add(self, verdict: Grade, sample: Mapping[str, Any]) -> None:
        """One scored sample: its grade, and its record (carrying at least
        ``variant``)."""
        self.grades.append(verdict)
        self.samples.append(
            {
                **sample,
                "whole": verdict.whole,
                "score": round(verdict.score, 4),
                "hits": dict(verdict.hits),
                "failure_mode": verdict.failure_mode,
            }
        )

    @property
    def runs(self) -> int:
        return len(self.grades)

    @property
    def passed(self) -> int:
        return sum(g.whole for g in self.grades)

    @property
    def interval(self) -> tuple[float, float]:
        return wilson_interval(self.passed, self.runs)

    @property
    def rubric(self) -> float | None:
        if not self.grades:
            return None
        return sum(g.score for g in self.grades) / len(self.grades)

    @property
    def checkpoint_hits(self) -> dict[str, int]:
        return {
            name: sum(g.hits.get(name, False) for g in self.grades) for name in self.checkpoints
        }

    @property
    def failure_modes(self) -> Counter[str]:
        return Counter(mode for g in self.grades if (mode := g.failure_mode) is not None)

    @property
    def variant_passes(self) -> dict[str, dict[str, int]]:
        """Whole-task passes per wording, in the order first sampled: where
        the scenario's number came from."""
        counts: dict[str, dict[str, int]] = {}
        for sample in self.samples:
            cell = counts.setdefault(str(sample["variant"]), {"passed": 0, "runs": 0})
            cell["passed"] += bool(sample["whole"])
            cell["runs"] += 1
        return counts

    def summary(self) -> str:
        low, high = self.interval
        rubric = "—" if self.rubric is None else f"{self.rubric:.2f}"
        return (
            f"{self.passed}/{self.runs} whole [{low:.2f}, {high:.2f}] rubric {rubric} "
            f"infra {self.infra} · claims {self.claims.summary()}"
        )

    def record(self, **meta: Any) -> dict[str, Any]:
        low, high = self.interval
        return {
            "kind": "frontier",
            "scenario": self.scenario,
            "tier": self.tier,
            "split": self.split,
            "fingerprint": self.fingerprint,
            "runs": self.runs,
            "passed": self.passed,
            "interval": [low, high],
            "rubric": self.rubric,
            "checkpoint_hits": self.checkpoint_hits,
            "failure_modes": dict(self.failure_modes),
            "variants": self.variant_passes,
            "infra": self.infra,
            "claims": self.claims.as_dict(),
            "samples": list(self.samples),
            **meta,
        }

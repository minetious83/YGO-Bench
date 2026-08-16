"""GOAT benchmark harness.

Runs repeated GOAT duels between deterministic agents and records enough about
each one to reproduce it and to audit it for integrity problems.  At this stage
the point is *infrastructure*, not deck strength: we are looking for crashes,
stalls, impossible actions, hidden-information leaks and nondeterminism.  Win
rates between passive/random/first-legal agents are diagnostics, not results.

Every duel is described by a :class:`DuelRecord`, and every run carries a
dependency fingerprint so a result can be tied to the exact engine, scripts,
card database and decks that produced it.
"""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
import traceback
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from itertools import combinations_with_replacement
from pathlib import Path
from typing import Any

from ygobench.agents.factory import create_agent
from ygobench.config import PROJECT_ROOT
from ygobench.engine.full_duel import run_duel
from ygobench.engine.upstream import UpstreamLayout
from ygobench.formats import get_format
from ygobench.goat.build import DECK_ROOT

#: Decks 01-04, the initial benchmark core.
CORE_DECKS: tuple[str, ...] = (
    "CHAOS_CONTROL_V1",
    "WARRIOR_V1",
    "CHAOS_TURBO_V1",
    "CHAOS_WARRIOR_V1",
)

AGENT_VERSIONS: dict[str, str] = {"passive": "1", "random": "1", "first_legal": "1"}


@dataclass
class DuelRecord:
    duel_id: str
    duel_format: str
    seed: int
    deck_ids: tuple[str, str]
    deck_hashes: tuple[str, str]
    seat_assignment: dict[str, str]
    agents: tuple[str, str]
    agent_versions: tuple[str, str]
    winner: int | None
    winner_seat: str | None
    turn_count: int
    decision_count: int
    illegal_actions: tuple[int, int]
    engine_exception: str | None
    stalled: bool
    termination: str
    replay_path: str | None
    replay_hash: str | None
    action_stream_hash: str | None
    elapsed_seconds: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BenchmarkResult:
    run_dir: Path
    records: list[DuelRecord] = field(default_factory=list)

    @property
    def completed(self) -> int:
        return sum(1 for r in self.records if r.engine_exception is None)

    @property
    def failures(self) -> list[DuelRecord]:
        return [r for r in self.records if r.engine_exception is not None]

    @property
    def stalls(self) -> list[DuelRecord]:
        return [r for r in self.records if r.stalled]


def _git_rev(path: Path) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except Exception:  # noqa: BLE001 - fingerprint is best-effort
        return "unknown"


def dependency_fingerprint() -> dict[str, Any]:
    """Everything a result depends on, so a run can be tied to its inputs."""

    layout = UpstreamLayout()
    distribution = layout.root / "vendor" / "distribution"
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "ygobench_commit": _git_rev(PROJECT_ROOT),
        "yugi_bench_commit": _git_rev(layout.root),
        "cardscripts_commit": _git_rev(distribution / "script"),
        "card_databases": sorted(p.name for p in layout.card_database_dir.glob("*.cdb")),
        "engine_library": str(layout.engine_library),
    }


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _deck_hashes() -> dict[str, str]:
    manifest = json.loads((DECK_ROOT / "manifest.json").read_text())
    return {entry["id"]: entry["deck_hash"] for entry in manifest["decks"]}


def _pairings(decks: tuple[str, ...], mirror: bool) -> list[tuple[str, str]]:
    pairs = list(combinations_with_replacement(decks, 2))
    return [pair for pair in pairs if mirror or pair[0] != pair[1]]


def run_benchmark(
    *,
    decks: tuple[str, ...] = CORE_DECKS,
    agents: tuple[str, ...] = ("passive", "random", "first_legal"),
    seeds: tuple[int, ...] = (0, 1),
    duel_format: str = "goat_2005",
    max_decisions: int = 4000,
    run_name: str | None = None,
    output_root: Path | None = None,
    deck_root: Path = DECK_ROOT,
    mirror_matches: bool = False,
) -> BenchmarkResult:
    """Run the benchmark matrix, swapping seats so both directions are recorded."""

    profile = get_format(duel_format)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    root = output_root or PROJECT_ROOT / "bench_data" / "goat"
    run_dir = root / (run_name or f"{stamp}_goat_benchmark")
    run_dir.mkdir(parents=True, exist_ok=True)

    hashes = _deck_hashes()
    result = BenchmarkResult(run_dir=run_dir)
    duel_index = 0

    for agent_a, agent_b in combinations_with_replacement(agents, 2):
        for deck_a, deck_b in _pairings(decks, mirror_matches):
            for seed in seeds:
                for swapped in (False, True):
                    first_agent, second_agent = (
                        (agent_b, agent_a) if swapped else (agent_a, agent_b)
                    )
                    first_deck, second_deck = (
                        (deck_b, deck_a) if swapped else (deck_a, deck_b)
                    )
                    duel_id = f"{duel_index:05d}"
                    duel_index += 1
                    record = _run_one(
                        duel_id=duel_id,
                        run_dir=run_dir,
                        deck_root=deck_root,
                        first_deck=first_deck,
                        second_deck=second_deck,
                        first_agent=first_agent,
                        second_agent=second_agent,
                        seed=seed,
                        swapped=swapped,
                        duel_format=profile.id,
                        max_decisions=max_decisions,
                        hashes=hashes,
                    )
                    result.records.append(record)

    summary = {
        "run": run_dir.name,
        "format": profile.to_dict(),
        "decks": list(decks),
        "agents": list(agents),
        "seeds": list(seeds),
        "duels": len(result.records),
        "completed": result.completed,
        "failures": len(result.failures),
        "stalls": len(result.stalls),
        "dependency_fingerprint": dependency_fingerprint(),
        "records": [record.to_dict() for record in result.records],
    }
    (run_dir / "benchmark.json").write_text(json.dumps(summary, indent=2) + "\n")
    return result


def _run_one(
    *,
    duel_id: str,
    run_dir: Path,
    deck_root: Path,
    first_deck: str,
    second_deck: str,
    first_agent: str,
    second_agent: str,
    seed: int,
    swapped: bool,
    duel_format: str,
    max_decisions: int,
    hashes: dict[str, str],
) -> DuelRecord:
    started = datetime.now(UTC)
    replay_name = f"duel_{duel_id}.jsonl"
    base = dict(
        duel_id=duel_id,
        duel_format=duel_format,
        seed=seed,
        deck_ids=(first_deck, second_deck),
        deck_hashes=(hashes.get(first_deck, ""), hashes.get(second_deck, "")),
        # Seat is recorded explicitly so a swapped pairing is a distinct row.
        seat_assignment={"first": first_agent, "second": second_agent, "swapped": swapped},
        agents=(first_agent, second_agent),
        agent_versions=(
            AGENT_VERSIONS.get(first_agent, "?"),
            AGENT_VERSIONS.get(second_agent, "?"),
        ),
    )
    try:
        outcome = run_duel(
            deck_root / f"{first_deck}.ydk",
            deck_root / f"{second_deck}.ydk",
            agent1=create_agent(first_agent, seed=seed * 2),
            agent2=create_agent(second_agent, seed=seed * 2 + 1),
            seed=seed,
            max_decisions=max_decisions,
            output_dir=run_dir,
            replay_name=replay_name,
            duel_format=duel_format,
        )
    except Exception as exc:  # noqa: BLE001 - a crash is a benchmark result
        return DuelRecord(
            **base,
            winner=None,
            winner_seat=None,
            turn_count=0,
            decision_count=0,
            illegal_actions=(0, 0),
            engine_exception=f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=3)}",
            stalled=False,
            termination="engine_exception",
            replay_path=None,
            replay_hash=None,
            action_stream_hash=None,
            elapsed_seconds=(datetime.now(UTC) - started).total_seconds(),
        )

    replay = outcome.replay_path
    winner_seat = None
    if outcome.winner is not None:
        winner_seat = "first" if outcome.winner == 0 else "second"
    return DuelRecord(
        **base,
        winner=outcome.winner,
        winner_seat=winner_seat,
        turn_count=outcome.turn_count,
        decision_count=outcome.decisions,
        illegal_actions=tuple(outcome.illegal_actions),
        engine_exception=None,
        # A duel that exhausts its decision budget never reached a terminal
        # state: that is a stall, not a draw.
        stalled=outcome.termination == "decision_budget_exhausted",
        termination=outcome.termination,
        replay_path=str(replay),
        # The file hash covers provenance; it is NOT a determinism signal,
        # because a replay embeds wall-clock timings that vary between runs.
        replay_hash=_file_hash(replay) if replay.is_file() else None,
        # This one is content-only, so identical inputs reproduce it exactly.
        action_stream_hash=action_stream_hash(replay) if replay.is_file() else None,
        elapsed_seconds=(datetime.now(UTC) - started).total_seconds(),
    )


def action_stream(replay: Path) -> list[tuple[int, str, str]]:
    """The ordered (player, tool, arguments) sequence a replay recorded."""

    stream: list[tuple[int, str, str]] = []
    for line in replay.read_text().splitlines():
        event = json.loads(line)
        if event.get("type") == "model_turn":
            call = event["tool_calls"][0]
            stream.append(
                (event["player"], call["name"], json.dumps(call["arguments"], sort_keys=True))
            )
    return stream


def action_stream_hash(replay: Path) -> str:
    """Digest of the decision sequence alone, excluding timings."""

    payload = json.dumps(action_stream(replay), sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def audit_replay(replay: Path) -> dict[str, Any]:
    """Integrity audit of one replay.

    Checks that no opponent card identity ever reached the acting player, that
    every submitted action was in the engine-provided legal set, and that no
    action was silently swapped for a fallback.
    """

    leaks: list[str] = []
    illegal: list[str] = []
    fallbacks: list[str] = []
    legal_for_decision: list[str] | None = None
    decisions = 0

    for line in replay.read_text().splitlines():
        event = json.loads(line)
        kind = event.get("type")

        if kind == "observation":
            for card in event["state"]["opponent"]["hand"]:
                if "code" in card or "name" in card:
                    leaks.append(f"opponent hand disclosed: {card}")
            for zone in ("monster_zone", "spell_trap_zone"):
                for card in event["state"]["opponent"].get(zone) or []:
                    if card and card.get("face_down") and ("code" in card or "name" in card):
                        leaks.append(f"face-down {zone} disclosed: {card}")
            semantic = event.get("semantic_actions") or {}
            legal_for_decision = [
                json.dumps(
                    {"tool": a["raw"]["tool"], "arguments": a["raw"]["arguments"]}, sort_keys=True
                )
                for a in semantic.get("actions", [])
            ]

        elif kind == "model_turn":
            decisions += 1
            call = event["tool_calls"][0]
            submitted = json.dumps(
                {"tool": call["name"], "arguments": call["arguments"]}, sort_keys=True
            )
            if legal_for_decision is not None and submitted not in legal_for_decision:
                illegal.append(submitted)
            if event.get("agent_error"):
                fallbacks.append(str(event["agent_error"]))

        elif kind == "invalid_action":
            illegal.append(json.dumps(event.get("attempted"), sort_keys=True))

    return {
        "decisions": decisions,
        "hidden_information_leaks": leaks,
        "actions_outside_legal_set": illegal,
        "silent_fallbacks": fallbacks,
        "ok": not leaks and not illegal and not fallbacks,
    }

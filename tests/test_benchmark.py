"""Benchmark integrity checks.

These assert properties of the harness, not deck strength: reproducibility,
seat bookkeeping, hidden information, and that every action an agent submitted
was one the engine actually offered.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ygobench.bench.goat_benchmark import (
    CORE_DECKS,
    action_stream,
    audit_replay,
    dependency_fingerprint,
    run_benchmark,
)
from ygobench.engine.upstream import UpstreamLayout
from ygobench.goat.build import DECK_ROOT

pytestmark = pytest.mark.skipif(
    bool(UpstreamLayout().runtime_errors()),
    reason="ocgcore/card database not built; run `ygo-bench setup`",
)


@pytest.fixture(scope="module")
def small_run(tmp_path_factory):
    """A deterministic two-deck, two-agent, single-seed matrix."""

    return run_benchmark(
        decks=("CHAOS_CONTROL_V1", "WARRIOR_V1"),
        agents=("passive", "first_legal"),
        seeds=(7,),
        run_name="integrity",
        output_root=tmp_path_factory.mktemp("bench"),
    )


def test_every_duel_completes_without_engine_exceptions(small_run) -> None:
    assert small_run.records
    assert small_run.failures == []
    assert small_run.completed == len(small_run.records)


def test_no_duel_stalls_out_of_its_decision_budget(small_run) -> None:
    """A duel that exhausts the budget never reached a terminal state."""

    assert small_run.stalls == []
    assert {r.termination for r in small_run.records} == {"game_over"}


def test_termination_is_classified_and_a_winner_recorded(small_run) -> None:
    for record in small_run.records:
        assert record.termination in {
            "game_over",
            "illegal_action_forfeit",
            "no_pending_decision",
            "decision_budget_exhausted",
            "engine_exception",
        }
        if record.termination == "game_over":
            assert record.winner in (0, 1)
            assert record.winner_seat in {"first", "second"}


def test_swapped_seats_are_recorded_as_distinct_rows(small_run) -> None:
    swapped = [r for r in small_run.records if r.seat_assignment["swapped"]]
    straight = [r for r in small_run.records if not r.seat_assignment["swapped"]]
    assert swapped and straight
    assert len({r.duel_id for r in small_run.records}) == len(small_run.records)
    # A swapped row genuinely reverses who sits first.
    for record in swapped:
        assert record.seat_assignment["first"] == record.agents[0]
        assert record.seat_assignment["second"] == record.agents[1]


def test_records_carry_deck_hashes_and_a_dependency_fingerprint(small_run) -> None:
    manifest = json.loads((DECK_ROOT / "manifest.json").read_text())
    hashes = {entry["id"]: entry["deck_hash"] for entry in manifest["decks"]}
    for record in small_run.records:
        for deck_id, deck_hash in zip(record.deck_ids, record.deck_hashes, strict=True):
            assert deck_hash == hashes[deck_id]
        assert record.replay_hash and len(record.replay_hash) == 64
        assert record.action_stream_hash and len(record.action_stream_hash) == 64

    fingerprint = dependency_fingerprint()
    assert fingerprint["card_databases"]
    assert "goat-entries.cdb" in fingerprint["card_databases"]


def test_identical_inputs_reproduce_an_identical_action_stream(tmp_path) -> None:
    def once(name):
        result = run_benchmark(
            decks=("CHAOS_CONTROL_V1",),
            agents=("first_legal",),
            seeds=(11,),
            run_name=name,
            output_root=tmp_path,
            mirror_matches=True,
        )
        return result.records

    first, second = once("a"), once("b")
    assert first and len(first) == len(second)
    for left, right in zip(first, second, strict=True):
        # The raw file hash deliberately is not asserted here: a replay embeds
        # wall-clock timings, so only the action stream is reproducible.
        assert left.action_stream_hash == right.action_stream_hash
        stream = action_stream(Path(left.replay_path))
        assert stream == action_stream(Path(right.replay_path))
        assert stream, "expected a non-empty action stream"
        assert (left.winner, left.turn_count, left.decision_count) == (
            right.winner,
            right.turn_count,
            right.decision_count,
        )


def test_changing_the_seed_changes_the_duel(tmp_path) -> None:
    result = run_benchmark(
        decks=("CHAOS_CONTROL_V1",),
        agents=("first_legal",),
        seeds=(1, 2, 3),
        run_name="seeds",
        output_root=tmp_path,
        mirror_matches=True,
    )
    openings = set()
    for record in result.records:
        for line in Path(record.replay_path).read_text().splitlines():
            event = json.loads(line)
            if event.get("type") == "observation":
                openings.add(
                    tuple(card["code"] for card in event["state"]["you"]["hand"])
                )
                break
    assert len(openings) > 1, "different seeds should not all deal the same opening"


def test_agents_never_receive_opponent_hidden_cards(small_run) -> None:
    for record in small_run.records:
        audit = audit_replay(Path(record.replay_path))
        assert audit["hidden_information_leaks"] == [], record.duel_id


def test_every_submitted_action_was_in_the_engine_legal_set(small_run) -> None:
    for record in small_run.records:
        audit = audit_replay(Path(record.replay_path))
        assert audit["actions_outside_legal_set"] == [], record.duel_id
        assert audit["decisions"] > 0


def test_no_silent_fallback_replaced_an_agent_action(small_run) -> None:
    """A fallback means the agent asked for something unavailable."""

    for record in small_run.records:
        audit = audit_replay(Path(record.replay_path))
        assert audit["silent_fallbacks"] == [], record.duel_id
        assert record.illegal_actions == (0, 0)


def test_benchmark_summary_is_written_and_self_describing(small_run) -> None:
    summary = json.loads((small_run.run_dir / "benchmark.json").read_text())
    assert summary["format"]["id"] == "goat_2005"
    assert summary["duels"] == len(small_run.records)
    assert summary["completed"] == small_run.completed
    assert summary["dependency_fingerprint"]["yugi_bench_commit"]
    assert len(summary["records"]) == len(small_run.records)


def test_core_deck_matrix_is_the_first_four_decks() -> None:
    assert CORE_DECKS == (
        "CHAOS_CONTROL_V1",
        "WARRIOR_V1",
        "CHAOS_TURBO_V1",
        "CHAOS_WARRIOR_V1",
    )

"""Deck-library tests: name resolution, legality and manifest integrity.

These require the pinned Project Ignis card database (`ygo-bench setup`).
"""

from __future__ import annotations

import json

import pytest

from ygobench.cards import CardIndex, CardResolutionError
from ygobench.engine.upstream import UpstreamLayout
from ygobench.formats import GOAT_2005
from ygobench.goat.build import DECK_ROOT, build_all, manifest_entry
from ygobench.goat.decklists import NAME_ALIASES, REFERENCE_DECKS
from ygobench.legality import parse_lflist

pytestmark = pytest.mark.skipif(
    not UpstreamLayout().card_database_dir.is_dir(),
    reason="card database not built; run `ygo-bench setup`",
)

#: Decks whose locked lists currently break April 2005 legality.  These are
#: reported upstream for a ruling, not silently corrected.  The exact expected
#: text keeps the discrepancy visible and catches any drift in either direction.
KNOWN_ILLEGAL: dict[str, set[str]] = {
    "PANDA_BURN_V1": {
        "Ceasefire (36468556) x2 exceeds 1 copy",
        "Magic Cylinder (62279055) x2 exceeds 1 copy",
    },
    "REASONING_GATE_TURBO_V1": {
        "Jinzo (77585513) x2 exceeds 1 copy",
    },
    "EARTH_BEAT_V1": {
        "Dust Tornado (60082869) x4 exceeds 3 copies",
        "Exiled Force (74131780) x2 exceeds 1 copy",
        "Injection Fairy Lily (GOAT) (504700138) x2 exceeds 1 copy",
    },
}


@pytest.fixture(scope="module")
def index() -> CardIndex:
    return CardIndex(UpstreamLayout().card_database_dir)


@pytest.fixture(scope="module")
def limit_list():
    assert GOAT_2005.lflist_path is not None
    return parse_lflist(GOAT_2005.lflist_path)


@pytest.fixture(scope="module")
def decks(index, limit_list):
    return build_all(index=index, limit_list=limit_list, profile=GOAT_2005)


def test_every_goat_pool_card_resolves(index, limit_list) -> None:
    """The April 2005 pool must be fully covered by the pinned databases."""

    missing = [code for code in limit_list.pool if index.get(code) is None]
    assert missing == []


def test_every_decklist_name_resolves(decks) -> None:
    unresolved = {built.deck.id: built.unresolved for built in decks if built.unresolved}
    assert unresolved == {}


def test_decks_have_the_expected_section_counts(decks) -> None:
    for built in decks:
        assert len(built.main) == 40, built.deck.id
        assert len(built.side) == 15, built.deck.id
        expected_fusion = 22 if built.deck.fusion_reference else 0
        assert len(built.fusion) == expected_fusion, built.deck.id


def test_only_the_known_discrepancies_are_illegal(decks) -> None:
    actual = {
        built.deck.id: set(built.validation.errors)
        for built in decks
        if built.validation is not None and built.validation.errors
    }
    assert actual == KNOWN_ILLEGAL


def test_name_alias_resolves_to_the_same_physical_card(index, limit_list) -> None:
    """`Kinetic Soldier` is the TCG name for passcode 79853073, `Cipher Soldier`."""

    with pytest.raises(CardResolutionError):
        index.resolve("Kinetic Soldier", pool=limit_list.pool)
    alias = NAME_ALIASES["Kinetic Soldier"]
    code = index.resolve(alias, pool=limit_list.pool)
    assert index.get(code).alias == 79853073


def test_resolution_prefers_the_canonical_printing(index) -> None:
    # Jinzo 77585514 is an alternate printing of 77585513.
    assert index.resolve("Jinzo") == 77585513
    assert index.limit_key(77585514) == 77585513


def test_alias_between_differently_named_cards_is_not_collapsed(index) -> None:
    """`alias` also means "treated as", which must not merge distinct cards."""

    harpie_lady_1 = index.resolve("Harpie Lady 1")
    assert harpie_lady_1 != index.resolve("Harpie Lady")
    # Its own allowance, despite aliasing to Harpie Lady.
    assert index.limit_key(harpie_lady_1) == harpie_lady_1
    assert index.resolve("A Legendary Ocean") != index.resolve("Umi")


def test_ambiguous_name_is_an_error_not_a_guess(index) -> None:
    with pytest.raises(CardResolutionError, match="matched no card"):
        index.resolve("Definitely Not A Real Card")


def test_generated_library_matches_the_committed_files(decks) -> None:
    """Guards against a stale checked-in library after a card-DB bump."""

    manifest_path = DECK_ROOT / "manifest.json"
    assert manifest_path.is_file(), "run scripts/build_goat_decks.py"
    manifest = json.loads(manifest_path.read_text())
    committed = {entry["id"]: entry for entry in manifest["decks"]}
    for built in decks:
        expected = manifest_entry(built, profile=GOAT_2005)
        assert committed[built.deck.id] == expected, built.deck.id


def test_manifest_records_format_and_provenance() -> None:
    manifest = json.loads((DECK_ROOT / "manifest.json").read_text())
    assert manifest["format"]["id"] == "goat_2005"
    assert manifest["format"]["duel_mode"] == "DUEL_MODE_GOAT"
    assert manifest["provenance"]["lflist"] == "GOAT.lflist.conf"
    assert "goat-entries.cdb" in manifest["provenance"]["card_database"]
    assert len(manifest["benchmark_core"]) == 4
    for entry in manifest["decks"]:
        assert entry["locked"] is True
        assert entry["version"] == 1
        assert entry["source"] == "goat_ai_trainer_reference"
        assert len(entry["deck_hash"]) == 64


def test_deck_hashes_are_unique_per_deck(decks) -> None:
    hashes = {
        manifest_entry(built, profile=GOAT_2005)["deck_hash"] for built in decks
    }
    assert len(hashes) == len(REFERENCE_DECKS)


def test_ydk_files_round_trip_through_the_engine_parser(decks) -> None:
    from ygobench.engine.full_duel import _parse_deck

    for built in decks:
        sections = _parse_deck(DECK_ROOT / f"{built.deck.id}.ydk")
        assert sections["main"] == built.main, built.deck.id
        assert sections["side"] == built.side, built.deck.id
        assert sections["extra"] == built.fusion, built.deck.id

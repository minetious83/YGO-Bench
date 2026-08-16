"""Card presentation: metadata, historical variants, and the visibility boundary.

None of this may influence engine behaviour. The tests that matter most are the
hidden-information ones: asset plumbing must not become a side channel.
"""

from __future__ import annotations

import json

import pytest

from ygobench.cards import CardIndex
from ygobench.engine.upstream import UpstreamLayout
from ygobench.presentation.assets import AssetEntry, AssetManifest
from ygobench.presentation.cards import (
    VARIANT_GOAT,
    VARIANT_PRE_ERRATA,
    VARIANT_STANDARD,
    CardPresenter,
)
from ygobench.presentation.view_model import (
    HIDDEN_STUB,
    leaked_fields,
    present_card,
    present_observation,
)

pytestmark = pytest.mark.skipif(
    not UpstreamLayout().card_database_dir.is_dir(),
    reason="card database not built; run `ygo-bench setup`",
)


@pytest.fixture(scope="module")
def presenter() -> CardPresenter:
    return CardPresenter(CardIndex(UpstreamLayout().card_database_dir))


def _visible(code: int, **extra):
    return {"code": code, "name": "x", "position": "face_up_attack", **extra}


# --------------------------------------------------------------------------
# Visible cards
# --------------------------------------------------------------------------


def test_hand_card_resolves_to_full_display_metadata(presenter) -> None:
    view = present_card(_visible(9596126), presenter)  # Chaos Sorcerer
    assert view["display_name"] == "Chaos Sorcerer"
    assert view["attribute"] == "DARK"
    assert view["race"] == "Spellcaster"
    assert view["level"] == 6
    assert view["attack"] == 2300
    assert "Monster" in view["card_types"] and "Effect" in view["card_types"]
    assert view["display_text"]


def test_face_up_field_and_graveyard_cards_resolve(presenter) -> None:
    for code in (11091375, 46986414):  # Luster Dragon, Dark Magician
        view = present_card(_visible(code), presenter)
        assert view["visibility"] == "visible"
        assert view["display_name"]


def test_fusion_card_resolves(presenter) -> None:
    view = present_card(_visible(504700102), presenter)  # Thousand-Eyes Restrict (GOAT)
    assert "Fusion" in view["card_types"]
    assert view["display_name"] == "Thousand-Eyes Restrict"


def test_spell_has_no_monster_only_fields(presenter) -> None:
    view = present_card(_visible(46411259), presenter)  # Metamorphosis
    assert "Spell" in view["card_types"]
    assert view["attribute"] is None and view["level"] is None and view["attack"] is None


# --------------------------------------------------------------------------
# Historical variants
# --------------------------------------------------------------------------


def test_goat_entry_maps_to_the_physical_cards_artwork(presenter) -> None:
    record = presenter.record(504700102)  # Thousand-Eyes Restrict (GOAT)
    assert record.variant == VARIANT_GOAT
    assert record.engine_card_id == 504700102
    assert record.display_image_id == 63519819  # the physical TCG card
    assert record.is_historical


def test_pre_errata_entry_maps_to_the_physical_cards_artwork(presenter) -> None:
    record = presenter.record(511000824)  # Ring of Destruction (Pre-Errata)
    assert record.variant == VARIANT_PRE_ERRATA
    assert record.display_image_id == 83555666
    assert record.display_name == "Ring of Destruction"  # qualifier stripped for display


def test_historical_text_is_the_engines_text_not_the_modern_wording(presenter) -> None:
    """Showing modern text would describe a card the simulator is not playing."""

    historical = presenter.record(511000824)  # Pre-Errata Ring
    modern = presenter.record(83555666)
    assert historical.display_text
    assert modern is not None
    assert historical.display_text != modern.display_text


def test_treated_as_aliases_keep_their_own_artwork(presenter) -> None:
    """`Harpie Lady 1` aliases to `Harpie Lady` but is a different card."""

    record = presenter.record(91932350)
    assert record.display_name == "Harpie Lady 1"
    assert record.display_image_id == 91932350
    assert record.variant == VARIANT_STANDARD


def test_alternate_printings_share_the_canonical_artwork(presenter) -> None:
    assert presenter.display_image_id(77585514) == 77585513  # Jinzo alt printing


# --------------------------------------------------------------------------
# Hidden information -- the side channel must not exist
# --------------------------------------------------------------------------


def test_opponent_unknown_hand_card_carries_no_identity(presenter) -> None:
    hidden = {"owner": "opponent", "position": "pos_0xa", "face_down": True}
    view = present_card(hidden, presenter)

    assert view["visibility"] == "hidden"
    assert view["card_back"] is True
    for field in ("engine_card_id", "display_image_id", "display_name", "display_text",
                  "image_path", "code", "name"):
        assert field not in view


def test_face_down_monster_carries_no_identity_even_with_a_code_present(presenter) -> None:
    """Defence in depth: a leaky upstream payload must still be filtered here."""

    leaky = {"code": 9596126, "name": "Chaos Sorcerer", "face_down": True,
             "position": "face_down_defense"}
    view = present_card(leaky, presenter)

    assert view == {**HIDDEN_STUB, "position": "face_down_defense"}
    assert leaked_fields(view) == []


def test_asset_lookup_is_never_called_for_a_hidden_card(presenter) -> None:
    calls: list[int] = []

    def lookup(image_id: int):
        calls.append(image_id)
        return "/tmp/leak.jpg"

    present_card({"face_down": True, "code": 9596126}, presenter, asset_lookup=lookup)
    assert calls == [], "asset resolution must happen after the visibility filter"


def test_full_observation_never_leaks_a_hidden_card(presenter) -> None:
    observation = {
        "turn": 3,
        "phase": "main1",
        "turn_player": "you",
        "you": {"lp": 8000, "hand": [_visible(9596126)], "monster_zone": [], "graveyard": []},
        "opponent": {
            "lp": 8000,
            "hand": [{"face_down": True}, {"face_down": True}],
            "monster_zone": [{"face_down": True, "position": "face_down_defense"}],
            "spell_trap_zone": [{"face_down": True}],
            "graveyard": [_visible(11091375)],
        },
    }
    view = present_observation(observation, presenter, asset_lookup=lambda _: "/img.jpg")

    assert leaked_fields(view) == []
    blob = json.dumps(view)
    assert "9596126" in blob  # my own hand card is fine
    assert view["opponent"]["hand"] == [HIDDEN_STUB, HIDDEN_STUB]
    # Public zones stay public.
    assert view["opponent"]["graveyard"][0]["display_name"] == "Luster Dragon"


def test_revealing_a_card_makes_its_metadata_available(presenter) -> None:
    face_down = present_card({"code": 9596126, "face_down": True}, presenter)
    assert face_down["visibility"] == "hidden"

    flipped = present_card(_visible(9596126), presenter)
    assert flipped["visibility"] == "visible"
    assert flipped["display_name"] == "Chaos Sorcerer"


# --------------------------------------------------------------------------
# Missing assets and cache behaviour
# --------------------------------------------------------------------------


def test_missing_image_yields_no_path_but_keeps_name_and_text(presenter, tmp_path) -> None:
    manifest = AssetManifest(path=tmp_path / "manifest.json", image_dir=tmp_path / "images")
    view = present_card(_visible(9596126), presenter, asset_lookup=manifest.lookup())

    assert view["image_path"] is None  # the UI shows a placeholder
    assert view["display_name"] == "Chaos Sorcerer"
    assert view["display_text"]


def test_cached_image_is_found_and_not_refetched(tmp_path) -> None:
    images = tmp_path / "images"
    images.mkdir()
    (images / "63519819.jpg").write_bytes(b"fake-jpeg-bytes")
    manifest = AssetManifest(path=tmp_path / "manifest.json", image_dir=images)

    assert manifest.is_cached(63519819)
    assert manifest.image_path(63519819).endswith("63519819.jpg")
    assert not manifest.is_cached(11091375)


def test_manifest_round_trips_provenance(tmp_path) -> None:
    images = tmp_path / "images"
    images.mkdir()
    (images / "63519819.jpg").write_bytes(b"x")
    manifest = AssetManifest(path=tmp_path / "manifest.json", image_dir=images)
    manifest.record(
        AssetEntry(
            engine_card_id=504700102,
            display_image_id=63519819,
            name="Thousand-Eyes Restrict",
            variant=VARIANT_GOAT,
            image_path="63519819.jpg",
            image_source="ygoprodeck",
            source_url_or_identifier="https://example/63519819.jpg",
            fetched_at="2026-01-01T00:00:00+00:00",
            status="cached",
        )
    )
    manifest.save()

    reloaded = AssetManifest(path=tmp_path / "manifest.json", image_dir=images)
    entry = reloaded.entries[63519819]
    assert entry.engine_card_id == 504700102
    assert entry.image_source == "ygoprodeck"
    assert entry.status == "cached"
    # No image bytes in the JSON.
    assert "base64" not in (tmp_path / "manifest.json").read_text()


def test_presentation_never_reaches_the_engine_or_agents() -> None:
    """Card imagery must not be able to affect duels, prompts or benchmarks."""

    from pathlib import Path

    roots = [Path("ygobench/engine"), Path("ygobench/agents"), Path("ygobench/bench")]
    offenders = [
        path
        for root in roots
        for path in root.rglob("*.py")
        if "ygobench.presentation" in path.read_text()
    ]
    assert offenders == []

"""The visibility boundary between backend card data and the frontend.

The rule this module exists to enforce: **a hidden card never leaves the backend
carrying anything that could identify it.** Not the engine id, not the image id,
not the name, not the text, not a path.

The tempting shortcut -- send the full card and let the UI decline to render it
-- is a leak, because anyone can read the payload. So hidden cards are replaced
here, before serialisation, with an opaque stub:

    {"visibility": "hidden", "card_back": true}

Asset resolution happens *after* this filter, so the image layer can never
become a side channel: there is simply no id left to resolve.
"""

from __future__ import annotations

from typing import Any

from ygobench.presentation.cards import CardPresenter

HIDDEN_STUB: dict[str, Any] = {"visibility": "hidden", "card_back": True}

_ZONES = (
    "hand",
    "monster_zone",
    "spell_trap_zone",
    "graveyard",
    "banished",
    "extra_deck",
)

#: Fields that would identify a card.  None of them may appear on a hidden card.
IDENTIFYING_FIELDS = frozenset(
    {
        "engine_card_id",
        "display_image_id",
        "display_name",
        "display_text",
        "image_path",
        "image_url",
        "code",
        "name",
        "desc",
        "text",
    }
)


def is_hidden(card: dict[str, Any] | None) -> bool:
    """A card is hidden when the observation withheld its identity."""

    if not card:
        return False
    if card.get("face_down"):
        return True
    # The visibility filter upstream strips names from anything not public.
    return not card.get("name")


def present_card(
    card: dict[str, Any] | None,
    presenter: CardPresenter,
    *,
    asset_lookup=None,
) -> dict[str, Any] | None:
    """Turn one observation card into a view model.

    ``asset_lookup(display_image_id) -> str | None`` supplies a local image path;
    a missing asset yields ``image_path: None`` so the UI can fall back to a
    placeholder while still showing name and text.
    """

    if card is None:
        return None
    if is_hidden(card):
        stub = dict(HIDDEN_STUB)
        # Position is public information -- you can see a card is set in defence.
        if card.get("position"):
            stub["position"] = card["position"]
        return stub

    code = card.get("code")
    record = presenter.record(int(code)) if code is not None else None
    if record is None:
        return {
            "visibility": "visible",
            "card_back": False,
            "display_name": card.get("name") or "Unknown card",
            "image_path": None,
            "position": card.get("position"),
        }

    payload = record.to_dict()
    payload["visibility"] = "visible"
    payload["card_back"] = False
    payload["position"] = card.get("position")
    payload["image_path"] = (
        asset_lookup(record.display_image_id) if asset_lookup is not None else None
    )
    return payload


def present_zone(
    zone: list[dict[str, Any]] | None, presenter: CardPresenter, *, asset_lookup=None
) -> list[dict[str, Any]]:
    return [
        present_card(card, presenter, asset_lookup=asset_lookup)
        for card in (zone or [])
        if card is not None
    ]


def present_observation(
    observation: dict[str, Any], presenter: CardPresenter, *, asset_lookup=None
) -> dict[str, Any]:
    """Render one player-visible observation as a frontend view model.

    The input is already the acting player's filtered observation, so this only
    has to avoid *re-introducing* identity for anything still hidden.
    """

    def side(payload: dict[str, Any]) -> dict[str, Any]:
        rendered = {
            key: value
            for key, value in payload.items()
            if key not in _ZONES
        }
        for zone in _ZONES:
            rendered[zone] = present_zone(payload.get(zone), presenter, asset_lookup=asset_lookup)
        return rendered

    return {
        "turn": observation.get("turn"),
        "phase": observation.get("phase"),
        "turn_player": observation.get("turn_player"),
        "you": side(observation.get("you", {}) or {}),
        "opponent": side(observation.get("opponent", {}) or {}),
    }


def leaked_fields(payload: Any) -> list[str]:
    """Any identifying field found on a hidden card, anywhere in the payload.

    Used by the regression tests to prove the asset plumbing cannot become a
    hidden-information side channel.
    """

    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("visibility") == "hidden" or node.get("card_back") is True:
                found.extend(sorted(IDENTIFYING_FIELDS & set(node)))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return found

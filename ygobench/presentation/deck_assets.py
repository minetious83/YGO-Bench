"""Which cards the GOAT deck library needs artwork for."""

from __future__ import annotations

import json

from ygobench.goat.build import DECK_ROOT
from ygobench.presentation.cards import CardPresenter, CardRecord


def required_card_ids() -> list[int]:
    """Every engine card id used by the ten decks' Main, Side and Fusion decks."""

    manifest = json.loads((DECK_ROOT / "manifest.json").read_text())
    codes: set[int] = set()
    for entry in manifest["decks"]:
        codes |= set(entry["main"]) | set(entry["side"]) | set(entry["fusion"])
    return sorted(codes)


def required_cards(presenter: CardPresenter) -> list[CardRecord]:
    records = [presenter.record(code) for code in required_card_ids()]
    return [record for record in records if record is not None]

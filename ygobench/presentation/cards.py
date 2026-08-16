"""Frontend-safe card metadata, sourced from the local Project Ignis databases.

Card text comes from the same BabelCDB snapshot the engine runs on, so what a
player reads is what the engine is executing.  That matters for GOAT: a
``(GOAT)`` or ``(Pre-Errata)`` entry carries the *historical* text, and replacing
it with the modern wording would be showing the player a card the simulator is
not playing.

Opening a card panel performs no network request -- the local database is
sufficient for every field below.

Identity is split into two ideas that are easy to conflate:

``engine_card_id``
    what ocgcore is actually executing (may be a 5xxxxxxxx GOAT entry).

``display_image_id``
    the physical TCG card whose artwork should be shown.

They differ for historical variants, and the mapping is explicit rather than
assumed.  Crucially it is *not* "just follow the alias": alias also links
genuinely different cards (``Harpie Lady 1`` -> ``Harpie Lady``), which would
show the wrong artwork.  The alias is only followed when both entries are the
same card under a format qualifier.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from ygobench.cards import CardEntry, CardIndex, normalize_name

TYPE_NAMES: tuple[tuple[int, str], ...] = (
    (0x1, "Monster"),
    (0x2, "Spell"),
    (0x4, "Trap"),
    (0x10, "Normal"),
    (0x20, "Effect"),
    (0x40, "Fusion"),
    (0x80, "Ritual"),
    (0x200, "Spirit"),
    (0x400, "Union"),
    (0x800, "Dual"),
    (0x1000, "Tuner"),
    (0x2000, "Synchro"),
    (0x10000, "Quick-Play"),
    (0x20000, "Continuous"),
    (0x40000, "Equip"),
    (0x80000, "Field"),
    (0x100000, "Counter"),
    (0x200000, "Flip"),
    (0x400000, "Toon"),
    (0x800000, "Xyz"),
)

ATTRIBUTE_NAMES = {
    0x1: "EARTH",
    0x2: "WATER",
    0x4: "FIRE",
    0x8: "WIND",
    0x10: "LIGHT",
    0x20: "DARK",
    0x40: "DIVINE",
}

RACE_NAMES = {
    0x1: "Warrior", 0x2: "Spellcaster", 0x4: "Fairy", 0x8: "Fiend",
    0x10: "Zombie", 0x20: "Machine", 0x40: "Aqua", 0x80: "Pyro",
    0x100: "Rock", 0x200: "Winged Beast", 0x400: "Plant", 0x800: "Insect",
    0x1000: "Thunder", 0x2000: "Dragon", 0x4000: "Beast", 0x8000: "Beast-Warrior",
    0x10000: "Dinosaur", 0x20000: "Fish", 0x40000: "Sea Serpent", 0x80000: "Reptile",
    0x100000: "Psychic", 0x200000: "Divine-Beast",
}

VARIANT_GOAT = "GOAT"
VARIANT_PRE_ERRATA = "Pre-Errata"
VARIANT_STANDARD = "standard"


def variant_of(name: str) -> str:
    if name.endswith("(GOAT)"):
        return VARIANT_GOAT
    if name.endswith("(Pre-Errata)"):
        return VARIANT_PRE_ERRATA
    return VARIANT_STANDARD


def display_name_of(name: str) -> str:
    """The name a player recognises, without the engine's format qualifier."""

    for suffix in (" (GOAT)", " (Pre-Errata)"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


@dataclass(frozen=True)
class CardRecord:
    """Everything the UI may show about a card whose identity is public."""

    engine_card_id: int
    display_image_id: int
    display_name: str
    display_text: str
    variant: str
    card_types: tuple[str, ...]
    attribute: str | None
    race: str | None
    level: int | None
    attack: int | None
    defense: int | None
    source_database: str

    @property
    def is_historical(self) -> bool:
        return self.variant != VARIANT_STANDARD

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["card_types"] = list(self.card_types)
        data["is_historical"] = self.is_historical
        return data


class CardPresenter:
    """Builds :class:`CardRecord` values from the local card databases."""

    def __init__(self, index: CardIndex) -> None:
        self.index = index

    def display_image_id(self, code: int) -> int:
        """The physical card whose artwork represents this engine entry.

        Follows ``alias`` only when the alias target is the same card under a
        format qualifier; otherwise the entry keeps its own artwork.
        """

        entry = self.index.get(code)
        if entry is None or not entry.alias:
            return code
        canonical = self.index.get(entry.alias)
        if canonical is None:
            return code
        if normalize_name(canonical.name) == normalize_name(entry.name):
            return entry.alias
        return code

    def record(self, code: int) -> CardRecord | None:
        entry = self.index.get(code)
        if entry is None:
            return None
        return self._record(entry)

    def _record(self, entry: CardEntry) -> CardRecord:
        types = tuple(name for bit, name in TYPE_NAMES if entry.type_mask & bit)
        is_monster = bool(entry.type_mask & 0x1)
        return CardRecord(
            engine_card_id=entry.code,
            display_image_id=self.display_image_id(entry.code),
            display_name=display_name_of(entry.name),
            # Historical entries keep their own text: this is what the engine runs.
            display_text=entry.text,
            variant=variant_of(entry.name),
            card_types=types,
            attribute=ATTRIBUTE_NAMES.get(entry.attribute) if is_monster else None,
            race=RACE_NAMES.get(entry.race) if is_monster else None,
            level=entry.level if is_monster and entry.level else None,
            attack=entry.attack if is_monster else None,
            defense=entry.defense if is_monster else None,
            source_database=entry.source,
        )

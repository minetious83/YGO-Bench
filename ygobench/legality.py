"""Deck legality validation against a Project Ignis Forbidden & Limited list.

ocgcore performs no deck validation whatsoever -- it will happily start a duel
with forbidden cards, eleven copies of a card, or a thirty-card Main Deck.  Deck
legality is therefore enforced here, *before* the duel is created, and is kept
strictly separate from gameplay rulings (which remain the engine's job).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from ygobench.cards import CardIndex
from ygobench.formats import FormatProfile

_ENTRY = re.compile(r"^(\d+)\s+(-?\d+)")


@dataclass(frozen=True)
class LimitList:
    """A parsed ``.lflist.conf``."""

    name: str
    limits: dict[int, int]
    whitelist: bool
    """When true, cards absent from the list are illegal rather than unlimited."""

    def limit_for(self, code: int) -> int | None:
        """Allowed copies, or ``None`` if the card is outside the legal pool."""

        if code in self.limits:
            return self.limits[code]
        return None if self.whitelist else 3

    @property
    def pool(self) -> set[int]:
        return set(self.limits)


def parse_lflist(path: Path) -> LimitList:
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    name = Path(path).stem
    limits: dict[int, int] = {}
    whitelist = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("!"):
            name = line[1:].strip() or name
            continue
        if line.startswith("$"):
            if line[1:].strip().lower() == "whitelist":
                whitelist = True
            continue
        if line.startswith("#"):
            continue
        match = _ENTRY.match(line)
        if match:
            limits[int(match.group(1))] = int(match.group(2))
    return LimitList(name=name, limits=limits, whitelist=whitelist)


@dataclass
class DeckValidation:
    deck_id: str
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_deck(
    *,
    deck_id: str,
    main: list[int],
    side: list[int],
    extra: list[int],
    profile: FormatProfile,
    index: CardIndex,
    limit_list: LimitList | None,
) -> DeckValidation:
    """Check section sizes, pool membership and copy limits."""

    result = DeckValidation(deck_id=deck_id)

    if not profile.main_min <= len(main) <= profile.main_max:
        result.errors.append(
            f"Main Deck has {len(main)} cards; {profile.display_name} requires "
            f"{profile.main_min}-{profile.main_max}"
        )
    if len(side) > profile.side_max:
        result.errors.append(f"Side Deck has {len(side)} cards; limit is {profile.side_max}")
    if profile.extra_max is not None and len(extra) > profile.extra_max:
        result.errors.append(
            f"Extra/Fusion Deck has {len(extra)} cards; limit is {profile.extra_max}"
        )

    if limit_list is None:
        return result

    # Copy limits apply across the whole deck, Side and Fusion included.
    counts: Counter[int] = Counter()
    for code in [*main, *side, *extra]:
        counts[index.limit_key(code)] += 1

    for code in sorted({*main, *side, *extra}):
        key = index.limit_key(code)
        allowed = limit_list.limit_for(key)
        if allowed is None:
            allowed = limit_list.limit_for(code)
        name = index.name_of(code)
        if allowed is None:
            result.errors.append(f"{name} ({code}) is not in the {limit_list.name} card pool")
        elif counts[key] > allowed:
            noun = "copy" if allowed == 1 else "copies"
            limit = "Forbidden" if allowed == 0 else f"{allowed} {noun}"
            result.errors.append(f"{name} ({code}) x{counts[key]} exceeds {limit}")

    return result

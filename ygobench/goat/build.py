"""Derive `.ydk` decks and a versioned manifest from the locked GOAT decklists.

Names are resolved against whichever Project Ignis card database this checkout
pins, so the generated ids track the pinned BabelCDB snapshot instead of being
maintained by hand.  Every generated deck is validated against the April 2005
Forbidden & Limited list before it is written.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from ygobench.cards import CardIndex, CardResolutionError
from ygobench.config import PROJECT_ROOT
from ygobench.formats import GOAT_2005, FormatProfile
from ygobench.goat.decklists import (
    BENCHMARK_CORE,
    FUSION_TOOLBOX_ID,
    GOAT_FUSION_TOOLBOX_V1,
    NAME_ALIASES,
    REFERENCE_DECKS,
    ReferenceDeck,
    expand,
)
from ygobench.legality import DeckValidation, LimitList, validate_deck

DECK_ROOT = PROJECT_ROOT / "resources" / "decks" / "goat"
MANIFEST_VERSION = 1
DECK_SOURCE = "goat_ai_trainer_reference"


@dataclass
class BuiltDeck:
    deck: ReferenceDeck
    main: list[int] = field(default_factory=list)
    side: list[int] = field(default_factory=list)
    fusion: list[int] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    aliases_applied: list[str] = field(default_factory=list)
    validation: DeckValidation | None = None

    @property
    def ok(self) -> bool:
        return not self.unresolved and self.validation is not None and self.validation.ok

    @property
    def errors(self) -> list[str]:
        validation_errors = self.validation.errors if self.validation is not None else []
        return [*self.unresolved, *validation_errors]


def deck_hash(
    *,
    main: list[int],
    side: list[int],
    fusion: list[int],
    fusion_reference: str | None,
    profile: FormatProfile,
    version: int,
) -> str:
    """Stable digest over the deck's contents and its format identity."""

    payload = json.dumps(
        {
            "format": profile.id,
            "version": version,
            "main": sorted(main),
            "side": sorted(side),
            "fusion": sorted(fusion),
            "fusion_reference": fusion_reference,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _resolve_section(
    names: list[str],
    index: CardIndex,
    pool: set[int] | None,
    unresolved: list[str],
    aliases_applied: list[str],
) -> list[int]:
    codes: list[int] = []
    for name in names:
        try:
            codes.append(index.resolve(name, pool=pool))
            continue
        except CardResolutionError as exc:
            failure = str(exc)
        alias = NAME_ALIASES.get(name)
        if alias is None:
            if failure not in unresolved:
                unresolved.append(failure)
            continue
        try:
            code = index.resolve(alias, pool=pool)
        except CardResolutionError as exc:
            message = f"{failure}; alias {alias!r} also failed: {exc}"
            if message not in unresolved:
                unresolved.append(message)
            continue
        codes.append(code)
        note = f"{name!r} -> {index.name_of(code)!r} ({code}) via NAME_ALIASES"
        if note not in aliases_applied:
            aliases_applied.append(note)
    return codes


def build_deck(
    reference: ReferenceDeck,
    *,
    index: CardIndex,
    limit_list: LimitList | None,
    profile: FormatProfile = GOAT_2005,
) -> BuiltDeck:
    pool = limit_list.pool if limit_list else None
    built = BuiltDeck(deck=reference)
    resolve = lambda names: _resolve_section(  # noqa: E731 - local binding for brevity
        names, index, pool, built.unresolved, built.aliases_applied
    )
    built.main = resolve(expand(reference.main))
    built.side = resolve(expand(reference.side))
    if reference.fusion_reference == FUSION_TOOLBOX_ID:
        built.fusion = resolve(expand(GOAT_FUSION_TOOLBOX_V1))
    built.validation = validate_deck(
        deck_id=reference.id,
        main=built.main,
        side=built.side,
        extra=built.fusion,
        profile=profile,
        index=index,
        limit_list=limit_list,
    )
    return built


def build_all(
    *,
    index: CardIndex,
    limit_list: LimitList | None,
    profile: FormatProfile = GOAT_2005,
) -> list[BuiltDeck]:
    return [
        build_deck(reference, index=index, limit_list=limit_list, profile=profile)
        for reference in REFERENCE_DECKS
    ]


def render_ydk(built: BuiltDeck) -> str:
    lines = [f"#created by ygo-bench ({DECK_SOURCE}: {built.deck.id})", "#main"]
    lines += [str(code) for code in built.main]
    lines.append("#extra")
    lines += [str(code) for code in built.fusion]
    lines.append("!side")
    lines += [str(code) for code in built.side]
    return "\n".join(lines) + "\n"


def manifest_entry(built: BuiltDeck, *, profile: FormatProfile) -> dict:
    reference = built.deck
    return {
        "id": reference.id,
        "display_name": reference.display_name,
        "version": reference.version,
        "format": profile.id,
        "archetype": reference.archetype,
        "strategic_identity": reference.strategic_identity,
        "notes": reference.notes,
        "role": "benchmark_core" if reference.id in BENCHMARK_CORE else "expansion",
        "file": f"{reference.id}.ydk",
        "main": built.main,
        "side": built.side,
        "fusion": built.fusion,
        "fusion_reference": reference.fusion_reference,
        "counts": {
            "main": len(built.main),
            "side": len(built.side),
            "fusion": len(built.fusion),
        },
        "source": DECK_SOURCE,
        "locked": True,
        "legal": built.ok,
        "validation_errors": built.errors,
        "aliases_applied": built.aliases_applied,
        "deck_hash": deck_hash(
            main=built.main,
            side=built.side,
            fusion=built.fusion,
            fusion_reference=reference.fusion_reference,
            profile=profile,
            version=reference.version,
        ),
    }


def write_library(
    decks: list[BuiltDeck],
    *,
    profile: FormatProfile = GOAT_2005,
    output_dir: Path = DECK_ROOT,
    provenance: dict | None = None,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    for built in decks:
        (output_dir / f"{built.deck.id}.ydk").write_text(render_ydk(built))
    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "format": profile.to_dict(),
        "source": DECK_SOURCE,
        "fusion_toolbox": {
            "id": FUSION_TOOLBOX_ID,
            "size": sum(count for count, _ in GOAT_FUSION_TOOLBOX_V1),
            "note": (
                "Literal 22-card Fusion Deck. GOAT Format predates the 15-card "
                "Extra Deck limit, so this is intentionally not truncated."
            ),
        },
        "benchmark_core": list(BENCHMARK_CORE),
        "provenance": provenance or {},
        "decks": [manifest_entry(built, profile=profile) for built in decks],
    }
    path = output_dir / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    return path

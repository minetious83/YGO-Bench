"""Duel format profiles.

A :class:`FormatProfile` is the single source of truth for everything that
distinguishes one ruleset from another: the ocgcore duel-mode flags, the
Forbidden & Limited list, the starting duel parameters and the deck-construction
limits.

The duel-mode flags are stored as an *attribute name* rather than a resolved
integer so that this module stays importable without a built ocgcore.  The name
is resolved against the upstream ``engine.core`` module at duel-creation time by
:func:`resolve_duel_flags`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ygobench.config import PROJECT_ROOT

LFLIST_ROOT = PROJECT_ROOT / "resources" / "lflists"


@dataclass(frozen=True)
class FormatProfile:
    """Everything that makes a ruleset what it is."""

    id: str
    display_name: str
    duel_mode: str
    """Name of the ``DUEL_MODE_*`` constant in the upstream ``engine.core``."""

    lflist: str | None = None
    """Basename of the ``.lflist.conf`` in ``resources/lflists``, if any."""

    starting_lp: int = 8000
    starting_hand: int = 5
    draw_per_turn: int = 1

    main_min: int = 40
    main_max: int = 60
    side_max: int = 15
    extra_max: int | None = 15
    """Extra/Fusion Deck cap.  ``None`` means the format imposes no limit."""

    @property
    def lflist_path(self) -> Path | None:
        if self.lflist is None:
            return None
        return LFLIST_ROOT / self.lflist

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "duel_mode": self.duel_mode,
            "lflist": self.lflist,
            "starting_lp": self.starting_lp,
            "starting_hand": self.starting_hand,
            "draw_per_turn": self.draw_per_turn,
            "main_min": self.main_min,
            "main_max": self.main_max,
            "side_max": self.side_max,
            "extra_max": self.extra_max,
        }


MR5 = FormatProfile(
    id="mr5",
    display_name="Master Rule 5",
    duel_mode="DUEL_MODE_MR5",
    lflist=None,
)

GOAT_2005 = FormatProfile(
    id="goat_2005",
    display_name="GOAT Format (April 2005)",
    duel_mode="DUEL_MODE_GOAT",
    lflist="GOAT.lflist.conf",
    # GOAT predates the 15-card Extra Deck limit: the Fusion Deck is uncapped.
    extra_max=None,
)

FORMATS: dict[str, FormatProfile] = {profile.id: profile for profile in (MR5, GOAT_2005)}

DEFAULT_FORMAT_ID = MR5.id


def get_format(format_id: str | FormatProfile | None) -> FormatProfile:
    """Look up a profile by id.  ``None`` yields the default (MR5)."""

    if isinstance(format_id, FormatProfile):
        return format_id
    if format_id is None:
        return FORMATS[DEFAULT_FORMAT_ID]
    try:
        return FORMATS[format_id]
    except KeyError:
        known = ", ".join(sorted(FORMATS))
        raise ValueError(f"Unknown format {format_id!r}; choose one of: {known}") from None


def resolve_duel_flags(profile: FormatProfile, core: Any) -> int:
    """Resolve the profile's duel-mode flags against the upstream engine module."""

    try:
        flags = getattr(core, profile.duel_mode)
    except AttributeError:
        raise RuntimeError(
            f"Format {profile.id!r} requires {profile.duel_mode}, which the pinned "
            "ocgcore build does not define."
        ) from None
    return int(flags)

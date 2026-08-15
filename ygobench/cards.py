"""Authoritative card-name resolution against the local Project Ignis card database.

Deck definitions are stored as card *names* so that they stay human-reviewable.
The card IDs are derived mechanically from whichever BabelCDB snapshot this
checkout pins, which keeps hundreds of hand-maintained IDs out of the tree.

Resolution is deliberately strict:

* an exact normalized name must match exactly one card,
* a name that matches nothing is an error,
* a name that matches two genuinely different cards is an error,
* a similar-but-different card is never substituted.

Two wrinkles in the Project Ignis data make naive matching wrong, and both are
handled here:

``(GOAT)`` / ``(Pre-Errata)`` suffixes
    The GOAT card pool replaces many modern printings with format-specific
    entries named e.g. ``Black Luster Soldier - Envoy of the Beginning (GOAT)``
    (from ``goat-entries.cdb``) or ``Ring of Destruction (Pre-Errata)`` (from
    ``cards-unofficial.cdb``).  These qualifiers are stripped before matching so
    that a decklist can simply say ``Ring of Destruction``.

``alias`` does not mean "same card"
    Alternate printings alias to their canonical id and share a name -- e.g.
    ``Jinzo`` 77585514 aliases to 77585513.  But ``alias`` is *also* used for
    "treated as" relationships between genuinely distinct cards: ``Harpie Lady
    1/2/3`` and ``Cyber Harpie Lady`` all alias to ``Harpie Lady``, and ``A
    Legendary Ocean`` aliases to ``Umi``.  Candidates are therefore only
    collapsed when they share a name as well as an alias group.
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

_QUALIFIER = re.compile(r"\s*\((?:goat|pre-errata)\)\s*$", re.IGNORECASE)
_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−"), "-")
_QUOTES = {ord("‘"): "'", ord("’"): "'", ord("“"): '"', ord("”"): '"'}


class CardResolutionError(ValueError):
    """A decklist name could not be resolved to exactly one card id."""


def normalize_name(name: str) -> str:
    """Fold a card name into a stable matching key.

    Strips format qualifiers, unifies Unicode dashes/quotes, treats hyphens as
    spaces (``Level Limit - Area B``), drops remaining punctuation and casefolds.
    """

    text = unicodedata.normalize("NFKC", name)
    text = text.translate(_DASHES).translate(_QUOTES)
    text = _QUALIFIER.sub("", text)
    text = text.casefold()
    text = re.sub(r"[\s\-]+", " ", text)
    text = re.sub(r"[^a-z0-9 '.,!?&]", "", text)
    return re.sub(r"\s+", " ", text).strip()


@dataclass(frozen=True)
class CardEntry:
    code: int
    name: str
    alias: int
    source: str


class CardIndex:
    """Name/id index over every ``.cdb`` in a Project Ignis distribution folder."""

    def __init__(self, db_dir: Path) -> None:
        self.db_dir = Path(db_dir)
        self._by_code: dict[int, CardEntry] = {}
        self._by_name: dict[str, set[int]] = defaultdict(set)
        databases = sorted(self.db_dir.glob("*.cdb"))
        if not databases:
            raise FileNotFoundError(
                f"No .cdb card databases in {self.db_dir}. Run: ygo-bench setup"
            )
        for path in databases:
            self._load(path)

    def _load(self, path: Path) -> None:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            rows = connection.execute(
                "SELECT d.id, d.alias, IFNULL(t.name, '') "
                "FROM datas d LEFT JOIN texts t ON t.id = d.id"
            ).fetchall()
        finally:
            connection.close()
        for code, alias, name in rows:
            if not name or code in self._by_code:
                continue
            self._by_code[int(code)] = CardEntry(int(code), name, int(alias or 0), path.name)
            self._by_name[normalize_name(name)].add(int(code))

    def __contains__(self, code: int) -> bool:
        return code in self._by_code

    def get(self, code: int) -> CardEntry | None:
        return self._by_code.get(code)

    def name_of(self, code: int) -> str:
        entry = self._by_code.get(code)
        return entry.name if entry else f"Card #{code}"

    def limit_key(self, code: int) -> int:
        """The id a Forbidden & Limited count should accrue against.

        Alternate printings share their canonical card's allowance; "treated as"
        aliases between differently-named cards do not.
        """

        entry = self._by_code.get(code)
        if entry is None or not entry.alias:
            return code
        canonical = self._by_code.get(entry.alias)
        if canonical is not None and normalize_name(canonical.name) == normalize_name(entry.name):
            return entry.alias
        return code

    def resolve(self, name: str, *, pool: set[int] | None = None) -> int:
        """Resolve a card name to exactly one id, restricted to ``pool`` if given."""

        key = normalize_name(name)
        if not key:
            raise CardResolutionError(f"Empty card name: {name!r}")
        candidates = set(self._by_name.get(key, ()))
        if pool is not None:
            candidates &= pool
        if not candidates:
            raise CardResolutionError(
                f"{name!r} matched no card"
                + (" in the format's legal pool" if pool is not None else " in the card database")
            )

        groups = {self.limit_key(code) for code in candidates}
        if len(groups) > 1:
            detail = ", ".join(
                f"{code} ({self._by_code[code].name})" for code in sorted(candidates)
            )
            raise CardResolutionError(f"{name!r} is ambiguous between distinct cards: {detail}")

        # One card, possibly several printings: prefer the canonical printing,
        # otherwise the lowest id, so the choice is stable across rebuilds.
        canonical = [code for code in candidates if not self._by_code[code].alias]
        return min(canonical) if canonical else min(candidates)

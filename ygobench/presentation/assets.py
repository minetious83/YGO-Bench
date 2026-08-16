"""Local card-image cache and its manifest.

Images are acquired once by a sync script and served from disk thereafter.
Normal play never depends on a live external URL, and a missing image is a
presentation detail: the card's name and text remain available and the duel is
unaffected.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ygobench.config import PROJECT_ROOT

ASSET_ROOT = PROJECT_ROOT / "resources" / "card_assets"
IMAGE_DIR = ASSET_ROOT / "images"
MANIFEST_PATH = ASSET_ROOT / "manifest.json"

STATUS_CACHED = "cached"
STATUS_MISSING = "missing"
STATUS_FAILED = "failed"


@dataclass
class AssetEntry:
    engine_card_id: int
    display_image_id: int
    name: str
    variant: str
    image_path: str | None
    image_source: str
    source_url_or_identifier: str
    fetched_at: str
    status: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AssetManifest:
    """Provenance for every image we hold, keyed by physical card."""

    def __init__(self, path: Path = MANIFEST_PATH, image_dir: Path = IMAGE_DIR) -> None:
        self.path = Path(path)
        self.image_dir = Path(image_dir)
        self.entries: dict[int, AssetEntry] = {}
        if self.path.is_file():
            raw = json.loads(self.path.read_text())
            for item in raw.get("assets", []):
                entry = AssetEntry(**item)
                self.entries[int(entry.display_image_id)] = entry

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "note": (
                "Presentation assets only. Image data never influences engine "
                "behaviour, deck hashes, seeds, agent prompts or benchmarks."
            ),
            "assets": [
                self.entries[key].to_dict() for key in sorted(self.entries)
            ],
        }
        self.path.write_text(json.dumps(payload, indent=2) + "\n")

    def record(self, entry: AssetEntry) -> None:
        self.entries[int(entry.display_image_id)] = entry

    def image_path(self, display_image_id: int) -> str | None:
        """Local path for a card's artwork, or ``None`` when it is not cached."""

        entry = self.entries.get(int(display_image_id))
        if entry and entry.image_path:
            candidate = self.image_dir / Path(entry.image_path).name
            if candidate.is_file():
                return str(candidate)
        candidate = self.image_dir / f"{int(display_image_id)}.jpg"
        return str(candidate) if candidate.is_file() else None

    def is_cached(self, display_image_id: int) -> bool:
        return self.image_path(display_image_id) is not None

    def lookup(self):
        """An ``asset_lookup`` callable for the view model."""

        return self.image_path

#!/usr/bin/env python3
"""Populate the local card-image cache for the GOAT deck library.

Acquisition only.  Images are downloaded once and served from disk afterwards;
nothing at runtime hotlinks an external URL, and a duel never waits on this.

    python scripts/sync_card_images.py --dry-run     # what would be fetched
    python scripts/sync_card_images.py               # fetch missing only

UNVERIFIED: the provider endpoint below has not been exercised from this
environment -- outbound access to the image host is blocked here, so the URL
template and any rate limits come from the provider's published description
rather than observation.  Confirm them, and the provider's current terms,
before running a real sync.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ygobench.cards import CardIndex  # noqa: E402
from ygobench.engine.upstream import UpstreamLayout  # noqa: E402
from ygobench.presentation.assets import (  # noqa: E402
    IMAGE_DIR,
    STATUS_CACHED,
    STATUS_FAILED,
    STATUS_MISSING,
    AssetEntry,
    AssetManifest,
)
from ygobench.presentation.cards import CardPresenter  # noqa: E402
from ygobench.presentation.deck_assets import required_cards  # noqa: E402

IMAGE_SOURCE = "ygoprodeck"
URL_TEMPLATE = "https://images.ygoprodeck.com/images/cards/{image_id}.jpg"

#: Deliberately conservative: this is a one-off sync of ~110 images, not a crawl.
REQUEST_INTERVAL_SECONDS = 1.5
MAX_RETRIES = 2
MAX_IMAGE_BYTES = 5_000_000


def _download(url: str) -> bytes:
    import httpx

    headers = {"User-Agent": "YGO-Bench/0.1 GOAT AI Trainer asset sync"}
    last: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            response = httpx.get(url, timeout=30, follow_redirects=True, headers=headers)
            response.raise_for_status()
            if not response.headers.get("content-type", "").startswith("image/"):
                raise ValueError(f"unexpected content type for {url}")
            if len(response.content) > MAX_IMAGE_BYTES:
                raise ValueError(f"image exceeds {MAX_IMAGE_BYTES} bytes: {url}")
            return response.content
        except Exception as exc:  # noqa: BLE001 - retried, then reported
            last = exc
            if attempt < MAX_RETRIES:
                time.sleep(REQUEST_INTERVAL_SECONDS * (attempt + 2))
    raise RuntimeError(f"failed after {MAX_RETRIES + 1} attempts: {last}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report without downloading")
    parser.add_argument("--limit", type=int, default=0, help="cap downloads this run")
    parser.add_argument("--image-dir", type=Path, default=IMAGE_DIR)
    args = parser.parse_args(argv)

    layout = UpstreamLayout()
    if not layout.card_database_dir.is_dir():
        print("Card database missing; run: ygo-bench setup", file=sys.stderr)
        return 2

    presenter = CardPresenter(CardIndex(layout.card_database_dir))
    manifest = AssetManifest(image_dir=args.image_dir)
    needed = required_cards(presenter)

    by_image: dict[int, list] = {}
    for record in needed:
        by_image.setdefault(record.display_image_id, []).append(record)

    cached = [key for key in by_image if manifest.is_cached(key)]
    missing = sorted(key for key in by_image if key not in cached)

    print(f"unique engine cards : {len(needed)}")
    print(f"distinct images     : {len(by_image)}")
    print(f"already cached      : {len(cached)}")
    print(f"to download         : {len(missing)}")

    if args.dry_run or not missing:
        for image_id in missing[:10]:
            print(f"  would fetch {URL_TEMPLATE.format(image_id=image_id)}")
        if args.dry_run:
            return 0

    args.image_dir.mkdir(parents=True, exist_ok=True)
    downloaded = failures = 0
    for position, image_id in enumerate(missing):
        if args.limit and downloaded >= args.limit:
            break
        record = by_image[image_id][0]
        url = URL_TEMPLATE.format(image_id=image_id)
        status, path = STATUS_MISSING, None
        try:
            if position:
                time.sleep(REQUEST_INTERVAL_SECONDS)
            payload = _download(url)
            destination = args.image_dir / f"{image_id}.jpg"
            temporary = destination.with_suffix(".tmp")
            temporary.write_bytes(payload)
            temporary.replace(destination)
            status, path = STATUS_CACHED, destination.name
            downloaded += 1
        except Exception as exc:  # noqa: BLE001 - a failed asset is not fatal
            failures += 1
            status = STATUS_FAILED
            print(f"  {image_id}: {exc}", file=sys.stderr)
        manifest.record(
            AssetEntry(
                engine_card_id=record.engine_card_id,
                display_image_id=image_id,
                name=record.display_name,
                variant=record.variant,
                image_path=path,
                image_source=IMAGE_SOURCE,
                source_url_or_identifier=url,
                fetched_at=datetime.now(UTC).isoformat(timespec="seconds"),
                status=status,
            )
        )

    manifest.save()
    print(f"downloaded {downloaded}, failed {failures}; manifest -> {manifest.path}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

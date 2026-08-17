"""YGOResources (db.ygoresources.com) as a research/validation source.

This is a *librarian*, not a referee.  The runtime rules authority is Project
Ignis / ocgcore; this module only helps us investigate why the engine behaves as
it does and assemble evidence for later explanation work.

Source hierarchy, in order of authority:

1. Project Ignis / ocgcore GOAT behaviour -- runtime truth for the simulator.
2. Engine regression tests -- what our installed engine actually does.
3. Historical GOAT/TCG evidence -- what we *expect* GOAT behaviour to be.
4. YGOResources FAQ/Q&A -- supporting and diagnostic evidence only.

YGOResources carries translated **OCG** FAQ/Q&A material, which does not
automatically govern the historical TCG format we model.  Where it disagrees
with the engine or with a historical TCG source, the disagreement is recorded as
a conflict (see :class:`RulingNote`) rather than silently resolved.

Design constraints, deliberately enforced in code rather than by convention:

* reads are served from a local, versioned cache and never touch the network,
* network access is opt-in per client and off by default,
* a session may fetch only a small number of specific resources -- there is no
  crawl path, and bulk coverage is an explicit error pointing at the
  maintainers' request to be contacted instead,
* ``X-Cache-Revision`` is recorded per document and ``/manifest/<revision>``
  drives invalidation of only the documents that actually changed.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ygobench.config import PROJECT_ROOT

BASE_URL = "https://db.ygoresources.com"
CACHE_ROOT = PROJECT_ROOT / "resources" / "rulings_cache" / "ygoresources"

#: A research session is for looking things up, not for mirroring the database.
#: Bulk/offline coverage is something the maintainers ask to be asked about.
MAX_FETCHES_PER_SESSION = 25

_SAFE_RESOURCE = re.compile(r"^[A-Za-z0-9_./-]+$")


class OfflineError(RuntimeError):
    """A document was not cached and network access was not enabled."""


class BulkAccessRefused(RuntimeError):
    """The caller tried to pull more than a research session's worth of data."""


class Fetcher(Protocol):
    def __call__(self, url: str) -> tuple[dict[str, Any], dict[str, str]]:
        """Return ``(payload, headers)`` for a URL."""


@dataclass(frozen=True)
class CachedDocument:
    resource: str
    payload: dict[str, Any]
    revision: str | None
    fetched_at: str
    source_url: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RulingNote:
    """One curated GOAT reference entry, with its evidence and any conflict.

    ``engine_behaviour`` is what our regression suite observed; the other fields
    are supporting evidence.  ``conflict`` is set when the sources disagree --
    the note records the disagreement instead of picking a winner.
    """

    topic: str
    engine_behaviour: str
    test_reference: str = ""
    historical_expectation: str = ""
    ygoresources_qa_ids: tuple[int, ...] = ()
    notes: str = ""
    conflict: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["ygoresources_qa_ids"] = list(self.ygoresources_qa_ids)
        data["authority"] = "engine observation is authoritative; Q&A is supporting evidence"
        return data


class YgoResourcesCache:
    """A local, versioned document store.  Reading never performs I/O upstream."""

    def __init__(self, root: Path = CACHE_ROOT) -> None:
        self.root = Path(root)
        self.index_path = self.root / "index.json"

    # -- index -------------------------------------------------------------

    def _index(self) -> dict[str, Any]:
        if self.index_path.is_file():
            return json.loads(self.index_path.read_text())
        return {"revision": None, "documents": {}}

    def _write_index(self, index: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n")

    @property
    def revision(self) -> str | None:
        return self._index().get("revision")

    def resources(self) -> list[str]:
        return sorted(self._index().get("documents", {}))

    # -- documents ---------------------------------------------------------

    def path_for(self, resource: str) -> Path:
        if not _SAFE_RESOURCE.match(resource) or ".." in resource:
            raise ValueError(f"Unsafe cache resource name: {resource!r}")
        return self.root / f"{resource.strip('/')}.json"

    def get(self, resource: str) -> CachedDocument | None:
        path = self.path_for(resource)
        if not path.is_file():
            return None
        return CachedDocument(**json.loads(path.read_text()))

    def put(self, document: CachedDocument) -> None:
        path = self.path_for(document.resource)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document.to_dict(), indent=2, sort_keys=True) + "\n")
        index = self._index()
        index.setdefault("documents", {})[document.resource] = {
            "revision": document.revision,
            "fetched_at": document.fetched_at,
            "source_url": document.source_url,
        }
        if document.revision:
            index["revision"] = document.revision
        self._write_index(index)

    def forget(self, resource: str) -> bool:
        path = self.path_for(resource)
        existed = path.is_file()
        if existed:
            path.unlink()
        index = self._index()
        index.get("documents", {}).pop(resource, None)
        self._write_index(index)
        return existed

    def stale(self, changed_resources: list[str]) -> list[str]:
        """Which cached documents a manifest says have changed."""

        cached = set(self.resources())
        return sorted(cached & set(changed_resources))

    def invalidate(self, changed_resources: list[str]) -> list[str]:
        """Drop only the documents the manifest reports as changed."""

        dropped = self.stale(changed_resources)
        for resource in dropped:
            self.forget(resource)
        return dropped


class YgoResourcesClient:
    """Cache-first client.  Network access is opt-in and rate-bounded.

    ``fetcher`` is injected so tests never depend on the network; when it is
    omitted and ``allow_network`` is set, a fetcher must be supplied by the
    caller.  There is deliberately no built-in HTTP default, so importing this
    module can never cause a request.
    """

    def __init__(
        self,
        cache: YgoResourcesCache | None = None,
        *,
        allow_network: bool = False,
        fetcher: Fetcher | None = None,
        base_url: str = BASE_URL,
        clock: Callable[[], str] | None = None,
    ) -> None:
        self.cache = cache or YgoResourcesCache()
        self.allow_network = allow_network
        self.fetcher = fetcher
        self.base_url = base_url.rstrip("/")
        self._clock = clock or (lambda: "unknown")
        self.fetches: list[str] = []

    # -- resource helpers --------------------------------------------------

    def card(self, konami_id: int) -> dict[str, Any]:
        """Card + FAQ data for one Konami database id."""

        return self._document(f"card/{int(konami_id)}").payload

    def qa(self, qa_id: int) -> dict[str, Any]:
        """One Q&A entry by its id."""

        return self._document(f"qa/{int(qa_id)}").payload

    def index(self, name: str) -> dict[str, Any]:
        """A lookup index (for example a name or print-code index)."""

        return self._document(f"index/{name}").payload

    # -- machinery ---------------------------------------------------------

    def _document(self, resource: str) -> CachedDocument:
        cached = self.cache.get(resource)
        if cached is not None:
            return cached
        if not self.allow_network:
            raise OfflineError(
                f"{resource!r} is not in the local cache and network access is disabled. "
                "Pre-cache it in a research session rather than fetching during a run."
            )
        return self.fetch(resource)

    def fetch(self, resource: str) -> CachedDocument:
        """Fetch one specific resource and cache it."""

        if not self.allow_network:
            raise OfflineError("Network access is disabled for this client")
        if self.fetcher is None:
            raise OfflineError("No fetcher was supplied; refusing to invent one")
        if len(self.fetches) >= MAX_FETCHES_PER_SESSION:
            raise BulkAccessRefused(
                f"A research session is capped at {MAX_FETCHES_PER_SESSION} fetches. "
                "For broad or offline coverage, contact the YGOResources maintainers "
                "instead of pulling the database through the API."
            )

        url = f"{self.base_url}/{resource.strip('/')}"
        payload, headers = self.fetcher(url)
        self.fetches.append(resource)
        document = CachedDocument(
            resource=resource,
            payload=payload,
            revision=_header(headers, "X-Cache-Revision"),
            fetched_at=self._clock(),
            source_url=url,
        )
        self.cache.put(document)
        return document

    def refresh_from_manifest(self, manifest: dict[str, Any]) -> list[str]:
        """Invalidate only what ``/manifest/<revision>`` reports as changed."""

        changed = [str(entry) for entry in manifest.get("changed", [])]
        return self.cache.invalidate(changed)


def _header(headers: dict[str, str], name: str) -> str | None:
    lowered = {key.lower(): value for key, value in (headers or {}).items()}
    return lowered.get(name.lower())


@dataclass
class RulingReference:
    """A small curated file of GOAT ruling notes."""

    path: Path
    notes: list[RulingNote] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> RulingReference:
        if not Path(path).is_file():
            return cls(path=Path(path))
        raw = json.loads(Path(path).read_text())
        return cls(
            path=Path(path),
            notes=[
                RulingNote(
                    topic=entry["topic"],
                    engine_behaviour=entry["engine_behaviour"],
                    test_reference=entry.get("test_reference", ""),
                    historical_expectation=entry.get("historical_expectation", ""),
                    ygoresources_qa_ids=tuple(entry.get("ygoresources_qa_ids", [])),
                    notes=entry.get("notes", ""),
                    conflict=bool(entry.get("conflict", False)),
                )
                for entry in raw.get("notes", [])
            ],
        )

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "source_hierarchy": [
                "Project Ignis / ocgcore GOAT behaviour (runtime truth)",
                "engine regression tests (observed implementation)",
                "historical GOAT/TCG evidence (expected behaviour)",
                "YGOResources FAQ/Q&A (supporting evidence only)",
            ],
            "notes": [note.to_dict() for note in self.notes],
        }
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")

    @property
    def conflicts(self) -> list[RulingNote]:
        return [note for note in self.notes if note.conflict]

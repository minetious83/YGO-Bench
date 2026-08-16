"""YGOResources research cache: offline-first, bounded, and never in the hot path."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ygobench.research.ygoresources import (
    MAX_FETCHES_PER_SESSION,
    BulkAccessRefused,
    CachedDocument,
    OfflineError,
    RulingNote,
    RulingReference,
    YgoResourcesCache,
    YgoResourcesClient,
)


def _fetcher(payload=None, revision="rev-1", calls=None):
    def fetch(url):
        if calls is not None:
            calls.append(url)
        return (payload or {"id": 4007, "name": "Test Card"}), {"X-Cache-Revision": revision}

    return fetch


def test_reads_never_touch_the_network(tmp_path) -> None:
    cache = YgoResourcesCache(tmp_path)
    cache.put(
        CachedDocument(
            resource="card/4007",
            payload={"name": "Cached Card"},
            revision="rev-1",
            fetched_at="t",
            source_url="https://example/card/4007",
        )
    )
    # No fetcher at all: a cached read must still succeed.
    client = YgoResourcesClient(cache, allow_network=False)
    assert client.card(4007)["name"] == "Cached Card"


def test_missing_document_offline_is_an_explicit_error(tmp_path) -> None:
    client = YgoResourcesClient(YgoResourcesCache(tmp_path), allow_network=False)
    with pytest.raises(OfflineError, match="not in the local cache"):
        client.card(4007)


def test_network_is_opt_in_and_needs_an_explicit_fetcher(tmp_path) -> None:
    """Importing the module can never cause a request: there is no default client."""

    client = YgoResourcesClient(YgoResourcesCache(tmp_path), allow_network=True)
    with pytest.raises(OfflineError, match="refusing to invent one"):
        client.fetch("card/4007")


def test_fetch_caches_the_document_and_its_revision(tmp_path) -> None:
    calls: list[str] = []
    cache = YgoResourcesCache(tmp_path)
    client = YgoResourcesClient(
        cache, allow_network=True, fetcher=_fetcher(calls=calls), clock=lambda: "2005-04-01"
    )

    first = client.card(4007)
    assert first["name"] == "Test Card"
    assert calls == ["https://db.ygoresources.com/card/4007"]

    document = cache.get("card/4007")
    assert document.revision == "rev-1"
    assert document.fetched_at == "2005-04-01"

    # Second read is served from cache: only ever one request per resource.
    client.card(4007)
    assert len(calls) == 1


def test_a_session_cannot_be_used_to_mirror_the_database(tmp_path) -> None:
    client = YgoResourcesClient(
        YgoResourcesCache(tmp_path), allow_network=True, fetcher=_fetcher()
    )
    for index in range(MAX_FETCHES_PER_SESSION):
        client.fetch(f"card/{index}")

    with pytest.raises(BulkAccessRefused, match="contact the YGOResources maintainers"):
        client.fetch("card/9999")


def test_manifest_invalidates_only_what_changed(tmp_path) -> None:
    cache = YgoResourcesCache(tmp_path)
    for resource in ("card/1", "card/2", "qa/7"):
        cache.put(
            CachedDocument(
                resource=resource, payload={}, revision="rev-1", fetched_at="t", source_url="u"
            )
        )
    client = YgoResourcesClient(cache, allow_network=False)

    dropped = client.refresh_from_manifest({"revision": "rev-2", "changed": ["card/2", "card/99"]})

    assert dropped == ["card/2"], "only cached-and-changed documents are dropped"
    assert cache.get("card/1") is not None
    assert cache.get("card/2") is None
    assert cache.get("qa/7") is not None


def test_cache_rejects_path_traversal(tmp_path) -> None:
    cache = YgoResourcesCache(tmp_path)
    for bad in ("../escape", "card/../../etc/passwd", "card/../secret"):
        with pytest.raises(ValueError, match="Unsafe cache resource name"):
            cache.path_for(bad)


def test_ruling_notes_record_conflicts_rather_than_resolving_them(tmp_path) -> None:
    reference = RulingReference(path=tmp_path / "goat_rulings.json")
    reference.notes.append(
        RulingNote(
            topic="Book of Moon on Spirit Reaper",
            engine_behaviour="Reaper is flipped face-down and survives being targeted",
            test_reference="tests/test_goat_rulings.py::test_book_of_moon_flips_spirit_reaper"
            "_down_instead_of_destroying_it",
            historical_expectation="targeting effects destroy Spirit Reaper",
            ygoresources_qa_ids=(1234,),
            conflict=True,
        )
    )
    reference.save()

    loaded = RulingReference.load(reference.path)
    assert len(loaded.conflicts) == 1
    payload = json.loads(reference.path.read_text())
    assert payload["source_hierarchy"][0].startswith("Project Ignis")
    assert payload["notes"][0]["authority"].startswith("engine observation is authoritative")


def test_research_package_is_never_imported_by_engine_agents_or_bench() -> None:
    """Nothing on the duel path may reach the network, even transitively."""

    roots = [Path("ygobench/engine"), Path("ygobench/agents"), Path("ygobench/bench")]
    offenders = [
        path
        for root in roots
        for path in root.rglob("*.py")
        if "ygobench.research" in path.read_text()
    ]
    assert offenders == [], f"research module leaked into the duel path: {offenders}"

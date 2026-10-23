"""Regression tests for the connector sync fixes:

1. Pre-upload freshness skip — `crawl_and_sync_doc` must NOT upload pages whose stored
   timestamp is already current, and `get_folder_freshness` must build the map correctly.
2. Schedule math (`_compute_next_sync`) — underpins the duplicate-run fix (a manual run
   advances next_sync_at so the scheduler doesn't re-fire). initial==now, else now+interval,
   manual-only==None.

Async coroutines are driven with asyncio.run() so these need no pytest-asyncio plugin.
They touch no DB / network — connector I/O methods are monkeypatched per test.
"""

import asyncio
from types import SimpleNamespace

from utils.connectors.clickup import ClickUpConnector, ClickUpConfig
from utils.connectors.base import SyncStats
from utils.db.connector_ops import _compute_next_sync


def _connector(**overrides) -> ClickUpConnector:
    cfg = ClickUpConfig(
        knowledge_id="k", knowledge_api_base_url="http://x", knowledge_api_key="key",
        api_token="t", workspace_id="w", space_ids=["s"], folder_ids=["f"],
        # zero pacing so the test never actually sleeps
        page_delay_min=0.0, page_delay_max=0.0, folder_delay_min=0.0, folder_delay_max=0.0,
        **overrides,
    )
    return ClickUpConnector(cfg)


def _page(pid: str, updated: int, content: str = "body"):
    return {"id": pid, "name": pid, "content": content, "date_updated": updated}


async def _spaces_ok(client):
    """Default get_spaces stub — the folder-sweep path now verifies spaces first."""
    return [{"id": "s", "name": "S"}]


# ----------------------------------------------------- crawl-failure surfacing

def test_run_sync_records_crawl_error_on_folder_listing_failure():
    """get_folders failing must be RECORDED (not swallowed) so the run can be flagged failed."""
    conn = _connector()

    async def boom_get_folders(client, space_id):
        raise RuntimeError("clickup 429")

    conn.get_spaces = _spaces_ok
    conn.get_folders = boom_get_folders

    stats = asyncio.run(conn.run_sync())
    assert stats.crawl_errors, "folder-listing failure should be recorded, not swallowed"
    assert stats.errors == 1
    assert "space s" in stats.crawl_errors[0]


def test_run_sync_records_crawl_error_on_doc_listing_failure():
    """get_docs_in_folder failing is recorded per-folder; reconcile is skipped for it."""
    conn = _connector()

    async def one_folder(client, space_id):
        return [{"id": "f", "name": "F"}]

    async def boom_get_docs(client, folder_id):
        raise RuntimeError("clickup 500")

    reconciled = []

    async def fake_reconcile(client, folder_id, live_ids, stats):
        reconciled.append(folder_id)
        return True

    conn.get_spaces = _spaces_ok
    conn.get_folders = one_folder
    conn.get_docs_in_folder = boom_get_docs
    conn.reconcile_folder = fake_reconcile
    conn.sync_space_docs = lambda *a, **k: _noop()

    stats = asyncio.run(conn.run_sync())
    assert stats.crawl_errors and "folder f" in stats.crawl_errors[0]
    assert reconciled == []  # never reconcile a folder we couldn't fully list


async def _noop():
    return None


def test_clean_run_has_no_crawl_errors():
    """A fully-successful crawl leaves crawl_errors empty (run stays 'completed')."""
    conn = _connector()

    async def one_folder(client, space_id):
        return [{"id": "f", "name": "F"}]

    async def docs(client, folder_id):
        return [{"id": "d", "name": "D"}]

    async def pages(client, doc_id):
        return [_page("p1", 1000)]

    async def fake_sync_page(client, space_id, folder_id, folder_name, doc, page):
        return "skipped"

    async def fake_reconcile(client, folder_id, live_ids, stats):
        return True

    conn.get_spaces = _spaces_ok
    conn.get_folders = one_folder
    conn.get_docs_in_folder = docs
    conn.get_pages = pages
    conn.sync_page = fake_sync_page
    conn.reconcile_folder = fake_reconcile
    conn.get_folder_freshness = lambda *a, **k: _empty_map()
    conn.sync_space_docs = lambda *a, **k: _noop()

    stats = asyncio.run(conn.run_sync())
    assert stats.crawl_errors == []


async def _empty_map():
    return {}


# ------------------------------------------------------- space verification
# (verify_selection itself is exercised in the "save-time selection validation"
# block below; these cover the crawl-time run_sync precheck that uses it.)

def test_run_sync_raises_when_no_spaces_configured():
    """A folder-sweep run with no spaces configured fails fast with a clear reason."""
    conn = _connector()
    conn.cfg.space_ids = []
    try:
        asyncio.run(conn.run_sync())
        assert False, "expected ValueError when no spaces configured"
    except ValueError as e:
        assert "No spaces" in str(e)


def test_run_sync_aborts_before_crawl_when_space_invalid():
    """run_sync must fail fast on a bad space and never list folders."""
    conn = _connector()
    conn.cfg.space_ids = ["gone"]

    async def spaces(client):
        return [{"id": "s"}]

    async def boom_folders(client, space_id):
        raise AssertionError("folders must not be listed when space verification fails")

    conn.get_spaces = spaces
    conn.get_folders = boom_folders
    try:
        asyncio.run(conn.run_sync())
        assert False, "expected ValueError from verify_selection"
    except ValueError as e:
        assert "gone" in str(e)


# ------------------------------------------------ save-time selection validation

def test_verify_selection_passes_when_ids_present():
    """All space/doc ids exist -> no raise (config-save / resync accept them)."""
    conn = _connector()

    async def spaces(client):
        return [{"id": "s1"}, {"id": "s2"}]

    async def get_doc(client, doc_id):
        return {"id": doc_id}

    conn.get_spaces = spaces
    conn.get_doc = get_doc
    asyncio.run(conn.verify_selection(space_ids=["s1"], doc_ids=["d1"]))  # must not raise


def test_verify_selection_raises_on_missing_space():
    conn = _connector()

    async def spaces(client):
        return [{"id": "s1"}]

    conn.get_spaces = spaces
    try:
        asyncio.run(conn.verify_selection(space_ids=["s1", "gone"]))
        assert False, "expected ValueError for a missing space"
    except ValueError as e:
        assert "gone" in str(e)


def test_verify_selection_raises_on_missing_doc():
    """A doc id ClickUp rejects (403 not_found_or_authorized) -> ValueError naming it."""
    import httpx
    conn = _connector()

    async def get_doc(client, doc_id):
        req = httpx.Request("GET", "http://x")
        raise httpx.HTTPStatusError("nf", request=req, response=httpx.Response(403, request=req))

    conn.get_doc = get_doc
    try:
        asyncio.run(conn.verify_selection(doc_ids=["dgone"]))
        assert False, "expected ValueError for a missing doc"
    except ValueError as e:
        assert "dgone" in str(e)


def test_verify_selection_raises_clear_message_on_bad_token():
    import httpx
    from utils.connectors.clickup import ClickUpAuthError
    conn = _connector()

    async def spaces(client):
        req = httpx.Request("GET", "http://x")
        raise httpx.HTTPStatusError("unauth", request=req, response=httpx.Response(401, request=req))

    conn.get_spaces = spaces
    try:
        asyncio.run(conn.verify_selection(space_ids=["s1"]))
        assert False, "expected ClickUpAuthError on 401"
    except ClickUpAuthError as e:
        assert "reconnect" in str(e).lower()
    except ValueError:
        assert False, "bad token must raise ClickUpAuthError (reconnect), not a generic ValueError"


# --------------------------------------------------------------- pre-upload skip

def test_crawl_skips_unchanged_pages_before_upload():
    """Unchanged page (stored >= page ts) -> no sync_page call, counted skipped, still live."""
    conn = _connector()
    pages = [_page("p-unchanged", 1000), _page("p-changed", 2000), _page("p-new", 1500)]
    fresh_map = {"p-unchanged": 1000, "p-changed": 1000}  # p-new absent

    synced = []

    async def fake_get_pages(client, doc_id):
        return pages

    async def fake_sync_page(client, space_id, folder_id, folder_name, doc, page):
        synced.append(page["id"])
        return "indexed"

    conn.get_pages = fake_get_pages
    conn.sync_page = fake_sync_page

    stats = SyncStats()
    live_ids, ok = asyncio.run(conn.crawl_and_sync_doc(
        clickup_client=None, knowledge_client=None, space_id="s",
        folder_id="f", folder_name="F", doc={"id": "d"}, stats=stats, fresh_map=fresh_map,
    ))

    assert ok is True
    # Only the changed + new pages hit the network; the unchanged one was skipped pre-upload.
    assert synced == ["p-changed", "p-new"]
    assert stats.indexed == 2
    assert stats.skipped == 1
    # Every page is still "live" (present in ClickUp) so reconcile never deletes a skipped page.
    assert live_ids == {"p-unchanged", "p-changed", "p-new"}


def test_force_reindex_disables_skip():
    """With force_reindex, the page timestamp is bumped above any stored value -> always uploads."""
    conn = _connector(force_reindex=True)
    pages = [_page("p1", 1000)]
    fresh_map = {"p1": 1000}  # would normally skip, but force_reindex must override

    synced = []

    async def fake_get_pages(client, doc_id):
        return pages

    async def fake_sync_page(client, space_id, folder_id, folder_name, doc, page):
        synced.append(page["id"])
        return "indexed"

    conn.get_pages = fake_get_pages
    conn.sync_page = fake_sync_page

    stats = SyncStats()
    asyncio.run(conn.crawl_and_sync_doc(
        clickup_client=None, knowledge_client=None, space_id="s",
        folder_id="f", folder_name="F", doc={"id": "d"}, stats=stats, fresh_map=fresh_map,
    ))
    assert synced == ["p1"]
    assert stats.indexed == 1
    assert stats.skipped == 0


# ------------------------------------------------------ page-listing consistency

def test_get_pages_returns_once_two_reads_agree():
    """A stable page tree is confirmed on the second read and returned (no extra reads)."""
    conn = _connector()
    reads = []

    async def fake_fetch_once(client, doc_id):
        reads.append(1)
        return [_page("p1", 1000), _page("p2", 2000)]

    conn._fetch_pages_once = fake_fetch_once
    pages = asyncio.run(conn.get_pages(client=None, doc_id="d"))
    assert {p["id"] for p in pages} == {"p1", "p2"}
    assert len(reads) == 2  # first read + one confirming read


def test_get_pages_stabilises_after_a_flap():
    """A partial first read then a stable pair -> returns the stable (complete) set."""
    conn = _connector()
    sequence = [
        [_page("p1", 1000)],                       # partial (missing p2)
        [_page("p1", 1000), _page("p2", 2000)],    # full
        [_page("p1", 1000), _page("p2", 2000)],    # full again -> confirmed
    ]
    calls = {"n": 0}

    async def fake_fetch_once(client, doc_id):
        r = sequence[calls["n"]]
        calls["n"] += 1
        return r

    conn._fetch_pages_once = fake_fetch_once
    pages = asyncio.run(conn.get_pages(client=None, doc_id="d"))
    assert {p["id"] for p in pages} == {"p1", "p2"}
    assert calls["n"] == 3


def test_get_pages_raises_when_never_stable():
    """Perpetually inconsistent tree -> raise so the caller skips reconcile (never delete)."""
    conn = _connector(page_consistency_attempts=3)
    calls = {"n": 0}

    async def fake_fetch_once(client, doc_id):
        calls["n"] += 1
        # Alternate the page set forever so two consecutive reads never agree.
        return [_page("p1", 1000)] if calls["n"] % 2 else [_page("p2", 2000)]

    conn._fetch_pages_once = fake_fetch_once
    try:
        asyncio.run(conn.get_pages(client=None, doc_id="d"))
        assert False, "expected RuntimeError on unstable page listing"
    except RuntimeError as e:
        assert "never stabilised" in str(e)
    assert calls["n"] == 3  # bounded by page_consistency_attempts


def test_get_pages_single_read_when_check_disabled():
    """page_consistency_attempts<=1 disables the double-read (one fetch, returned as-is)."""
    conn = _connector(page_consistency_attempts=1)
    calls = {"n": 0}

    async def fake_fetch_once(client, doc_id):
        calls["n"] += 1
        return [_page("p1", 1000)]

    conn._fetch_pages_once = fake_fetch_once
    pages = asyncio.run(conn.get_pages(client=None, doc_id="d"))
    assert {p["id"] for p in pages} == {"p1"}
    assert calls["n"] == 1


def test_missing_or_none_stored_ts_falls_through_to_upload():
    """No stored ts (new page) or empty map -> never pre-skipped; goes through normal path."""
    conn = _connector()
    pages = [_page("p1", 1000)]

    synced = []

    async def fake_get_pages(client, doc_id):
        return pages

    async def fake_sync_page(client, space_id, folder_id, folder_name, doc, page):
        synced.append(page["id"])
        return "indexed"

    conn.get_pages = fake_get_pages
    conn.sync_page = fake_sync_page

    stats = SyncStats()
    asyncio.run(conn.crawl_and_sync_doc(
        clickup_client=None, knowledge_client=None, space_id="s",
        folder_id="f", folder_name="F", doc={"id": "d"}, stats=stats, fresh_map={},
    ))
    assert synced == ["p1"]


def test_get_folder_freshness_builds_map_newest_wins_and_drops_nulls():
    """Map keeps the newest stored ts per source_id and omits rows without source_id/last_modified."""
    conn = _connector()

    async def fake_fetch(client, folder_id):
        return [
            {"object_id": "a", "source_id": "s1", "last_modified": 1000},
            {"object_id": "b", "source_id": "s1", "last_modified": 2000},   # dup -> newest wins
            {"object_id": "c", "source_id": "s2", "last_modified": None},   # dropped (no ts)
            {"object_id": "d", "source_id": None, "last_modified": 5000},   # dropped (no sid)
            {"object_id": "e", "source_id": "s3", "last_modified": "3000"}, # coerced to int
        ]

    conn._fetch_kb_docs = fake_fetch
    fresh = asyncio.run(conn.get_folder_freshness(client=None, folder_id="f"))
    assert fresh == {"s1": 2000, "s3": 3000}


def test_get_folder_freshness_swallows_errors():
    """A prefetch failure must yield an empty map (no pre-skip), never raise."""
    conn = _connector()

    async def boom(client, folder_id):
        raise RuntimeError("network down")

    conn._fetch_kb_docs = boom
    assert asyncio.run(conn.get_folder_freshness(client=None, folder_id="f")) == {}


# ----------------------------------------------------- live progress callback

def test_run_sync_flushes_progress_per_doc():
    """on_progress fires after each doc (and after space docs) with current stats."""
    conn = _connector()

    async def fake_get_folders(client, space_id):
        return [{"id": "f", "name": "F"}]

    async def fake_get_docs(client, folder_id):
        return [{"id": "d1", "name": "d1"}, {"id": "d2", "name": "d2"}]

    async def fake_freshness(client, folder_id):
        return {}

    async def fake_crawl(client, kclient, space_id, folder_id, folder_name, doc, stats, fresh_map):
        stats.indexed += 1
        return {doc["id"]}, True

    async def fake_space_docs(client, kclient, space_id, stats):
        return None

    async def fake_reconcile(client, folder_id, live_ids, stats):
        return None

    conn.get_spaces = _spaces_ok
    conn.get_folders = fake_get_folders
    conn.get_docs_in_folder = fake_get_docs
    conn.get_folder_freshness = fake_freshness
    conn.crawl_and_sync_doc = fake_crawl
    conn.sync_space_docs = fake_space_docs
    conn.reconcile_folder = fake_reconcile

    seen = []

    async def on_progress(stats):
        seen.append(stats.indexed)

    stats = asyncio.run(conn.run_sync(on_progress=on_progress))
    assert stats.indexed == 2
    # Two per-doc flushes (1, 2) plus the post-space-docs flush (2).
    assert seen == [1, 2, 2]


def test_run_sync_swallows_progress_errors():
    """A failing on_progress must not abort the crawl."""
    conn = _connector()

    async def fake_get_folders(client, space_id):
        return [{"id": "f", "name": "F"}]

    async def fake_get_docs(client, folder_id):
        return [{"id": "d1", "name": "d1"}]

    async def fake_freshness(client, folder_id):
        return {}

    async def fake_crawl(client, kclient, space_id, folder_id, folder_name, doc, stats, fresh_map):
        stats.indexed += 1
        return {doc["id"]}, True

    async def fake_space_docs(client, kclient, space_id, stats):
        return None

    async def fake_reconcile(client, folder_id, live_ids, stats):
        return None

    conn.get_spaces = _spaces_ok
    conn.get_folders = fake_get_folders
    conn.get_docs_in_folder = fake_get_docs
    conn.get_folder_freshness = fake_freshness
    conn.crawl_and_sync_doc = fake_crawl
    conn.sync_space_docs = fake_space_docs
    conn.reconcile_folder = fake_reconcile

    async def boom(stats):
        raise RuntimeError("flush failed")

    stats = asyncio.run(conn.run_sync(on_progress=boom))
    assert stats.indexed == 1


# ------------------------------------------------------------- doc_ids / resync
def test_run_sync_only_doc_ids_skips_folder_sweep():
    """only_doc_ids mode (per-doc resync) crawls just the named docs, never folders."""
    conn = _connector(only_doc_ids=True, doc_ids=["d1", "d2"])

    async def boom_folders(client, space_id):
        raise AssertionError("folder sweep must be skipped in only_doc_ids mode")

    async def fake_get_doc(client, doc_id):
        return {"id": doc_id, "name": doc_id, "parent": {"id": "fold"}}

    async def fake_freshness(client, folder_id):
        return {}

    async def fake_crawl(client, kclient, space_id, folder_id, folder_name, doc, stats, fresh_map):
        stats.indexed += 1
        return {doc["id"]}, True

    conn.get_folders = boom_folders
    conn.get_doc = fake_get_doc
    conn.get_folder_freshness = fake_freshness
    conn.crawl_and_sync_doc = fake_crawl

    stats = asyncio.run(conn.run_sync())
    assert stats.indexed == 2  # one per explicit doc, folders untouched


def test_run_sync_doc_ids_crawled_in_addition_to_folders():
    """Persistent doc_ids are indexed additively, after the folder crawl."""
    conn = _connector(doc_ids=["dx"])

    async def fake_get_folders(client, space_id):
        return [{"id": "f", "name": "F"}]

    async def fake_get_docs(client, folder_id):
        return [{"id": "d1", "name": "d1"}]

    async def fake_freshness(client, folder_id):
        return {}

    async def fake_crawl(client, kclient, space_id, folder_id, folder_name, doc, stats, fresh_map):
        stats.indexed += 1
        return {doc["id"]}, True

    async def fake_space_docs(client, kclient, space_id, stats):
        return None

    async def fake_reconcile(client, folder_id, live_ids, stats):
        return None

    async def fake_get_doc(client, doc_id):
        return {"id": doc_id, "name": doc_id, "parent": {"id": "p"}}

    conn.get_spaces = _spaces_ok
    conn.get_folders = fake_get_folders
    conn.get_docs_in_folder = fake_get_docs
    conn.get_folder_freshness = fake_freshness
    conn.crawl_and_sync_doc = fake_crawl
    conn.sync_space_docs = fake_space_docs
    conn.reconcile_folder = fake_reconcile
    conn.get_doc = fake_get_doc

    stats = asyncio.run(conn.run_sync())
    assert stats.indexed == 2  # one folder doc + one explicit doc


def test_sync_explicit_docs_counts_fetch_error():
    """A doc whose metadata fetch fails is counted as an error, not crash the run."""
    conn = _connector(doc_ids=["bad"])

    async def boom_get_doc(client, doc_id):
        raise RuntimeError("404 doc gone")

    conn.get_doc = boom_get_doc
    stats = SyncStats()
    asyncio.run(conn.sync_explicit_docs(None, None, stats))
    assert stats.errors == 1


# ------------------------------------------------------------- schedule math

# --------------------------------------------------------- folder selection
def _sel_connector(**selection) -> ClickUpConnector:
    """Connector built purely from folder-selection fields (no hardcoded folder_ids,
    unlike _connector) so include/exclude lists can be set explicitly."""
    cfg = ClickUpConfig(
        knowledge_id="k", knowledge_api_base_url="http://x", knowledge_api_key="key",
        api_token="t", workspace_id="w", space_ids=["s"], **selection,
    )
    return ClickUpConnector(cfg)


def test_folder_selected_empty_selection_includes_all():
    c = _sel_connector()
    assert c._folder_selected("123", "Anything") is True


def test_folder_selected_include_by_id_or_name():
    c = _sel_connector(folder_ids=["123"], folder_names=["docs"])
    assert c._folder_selected("123", "Whatever") is True       # id match
    assert c._folder_selected("999", "Docs") is True           # name match (case-insensitive)
    assert c._folder_selected("999", "Other") is False         # neither


def test_exclude_folder_name_wins_over_include():
    # In the include list by name, but also excluded -> skipped.
    c = _sel_connector(folder_names=["docs"], exclude_folder_name=["Docs"])
    assert c._folder_selected("1", "Docs") is False


def test_exclude_folder_id_wins_over_include():
    # Explicitly included by id, but excluded by id -> skipped.
    c = _sel_connector(folder_ids=["123"], exclude_folder_id=["123"])
    assert c._folder_selected("123", "Docs") is False


def test_exclude_applies_with_empty_include_selection():
    # No include selection (all folders), exclusion still removes the named/ided one.
    c = _sel_connector(exclude_folder_name=["archive"])
    assert c._folder_selected("1", "Archive") is False
    assert c._folder_selected("2", "Active") is True

    c2 = _sel_connector(exclude_folder_id=["77"])
    assert c2._folder_selected("77", "Anything") is False
    assert c2._folder_selected("78", "Anything") is True


def test_name_excluded_empty_keywords_never_excludes():
    # No keywords configured -> nothing is skipped by name (no hardcoded 'archive').
    c = _sel_connector()
    assert c._name_excluded("Archive") is False
    assert c._name_excluded("anything") is False
    # A folder/doc/page literally named 'Archive' is now indexed by default.
    assert c._folder_selected("1", "Archive") is True


def test_name_excluded_substring_case_insensitive():
    c = _sel_connector(exclude_name_keywords=["Archive", "Draft"])
    assert c._name_excluded("Q1 Archive") is True        # substring, mixed case
    assert c._name_excluded("ARCHIVED NOTES") is True     # substring of a larger word
    assert c._name_excluded("draft-spec") is True
    assert c._name_excluded("Knowledge Service") is False
    assert c._name_excluded("") is False


def test_keyword_exclusion_applies_to_folder_selection():
    # Keyword match skips a folder even when it is in the include selection.
    c = _sel_connector(folder_names=["docs"], exclude_name_keywords=["archive"])
    assert c._folder_selected("1", "Docs Archive") is False   # included by name but keyword wins
    assert c._folder_selected("2", "Docs") is True


# ----------------------------------------------------- runner force_reindex wiring
def test_build_connector_maps_force_reindex_from_selection():
    """The one-shot force_reindex flag stashed in the selection blob must reach the
    ClickUpConfig (and default to False when absent)."""
    from utils.connectors.runner import _build_connector
    on = _build_connector("clickup", {
        "knowledge_id": "k",
        "selection": {"workspace_id": "w", "space_ids": ["s"], "force_reindex": True},
    }, token="t")
    assert on.cfg.force_reindex is True

    off = _build_connector("clickup", {
        "knowledge_id": "k",
        "selection": {"workspace_id": "w", "space_ids": ["s"]},
    }, token="t")
    assert off.cfg.force_reindex is False


def test_build_connector_maps_doc_ids_and_only_doc_ids():
    """doc_ids and the one-shot only_doc_ids flag must reach the ClickUpConfig."""
    from utils.connectors.runner import _build_connector
    c = _build_connector("clickup", {
        "knowledge_id": "k",
        "selection": {"workspace_id": "w", "doc_ids": ["d1"], "only_doc_ids": True},
    }, token="t")
    assert c.cfg.doc_ids == ["d1"]
    assert c.cfg.only_doc_ids is True

    d = _build_connector("clickup", {
        "knowledge_id": "k", "selection": {"workspace_id": "w"},
    }, token="t")
    assert d.cfg.doc_ids == []
    assert d.cfg.only_doc_ids is False


def _cfg(interval=None, cron=None):
    return SimpleNamespace(schedule_cron=cron, interval_minutes=interval, knowledge_id="k")


def test_compute_next_sync_initial_is_due_now():
    import datetime as dt
    before = dt.datetime.now(dt.timezone.utc)
    nxt = _compute_next_sync(_cfg(interval=360), initial=True)
    # initial -> ~now (due immediately); allow a tiny execution delta
    assert 0 <= (nxt - before).total_seconds() < 5


def test_compute_next_sync_subsequent_adds_interval():
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc)
    nxt = _compute_next_sync(_cfg(interval=360), initial=False)
    assert 355 <= (nxt - now).total_seconds() / 60 <= 361


def test_compute_next_sync_manual_only_is_none():
    # No interval and no cron -> manual-only -> advance/backstop must no-op.
    assert _compute_next_sync(_cfg(), initial=False) is None
    assert _compute_next_sync(_cfg(), initial=True) is None


def test_compute_next_sync_cron_is_future():
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc)
    nxt = _compute_next_sync(_cfg(cron="0 * * * *"), initial=False)
    assert nxt > now


if __name__ == "__main__":
    # Standalone runner (the venv has no pytest): execute every test_* and report.
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    raise SystemExit(1 if failed else 0)

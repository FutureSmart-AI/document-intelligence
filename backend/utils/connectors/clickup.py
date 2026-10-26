"""ClickUp connector — crawl ClickUp docs and feed each page to the knowledge
indexing path.

This is the reusable, config-driven form of scripts/clickup/sync_clickup_docs.py.
All behaviour that the script read from module-level env vars is now carried on a
ClickUpConfig passed to the connector, so the same logic runs from the API/Celery
task (per-knowledge, multi-tenant) and from the thin CLI wrapper.

Pipeline per page (unchanged from the script):
  1. POST /v1/{knowledge_id}/upload-url -> presigned PUT + download URLs
  2. PUT the page markdown to the presigned URL
  3. POST /v1/{knowledge_id}/documents with the download URL + extra_metadata
  4. The knowledge API does the freshness check (skip/reindex) and dispatches the
     existing worker indexing task.
Then a per-folder set-difference sweep reconciles deletions (guarded, dry-run by default).
"""

import asyncio
import json
import random
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from utils.connectors.base import Connector, SyncStats
from utils.logger import logger

CLICKUP_V2 = "https://api.clickup.com/api/v2"
CLICKUP_V3 = "https://api.clickup.com/api/v3"


class ClickUpAuthError(Exception):
    """ClickUp rejected the stored token — expired, revoked, or lacking workspace access
    (HTTP 401/403). Distinct from a bad selection so callers can tell the user to RECONNECT
    rather than surfacing an opaque 502/400."""


def clickup_is_auth_error(exc: BaseException) -> bool:
    """True if `exc` signals an expired/invalid ClickUp token — either our own
    ClickUpAuthError or a raw 401/403 from the ClickUp API."""
    if isinstance(exc, ClickUpAuthError):
        return True
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (401, 403)

# Shared httpx timeouts: connect=5s, read=20s, write=10s, pool=5s
_HTTPX_TIMEOUT = httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0)


@dataclass
class ClickUpConfig:
    """Everything one ClickUp sync run needs. Built from a connector_sync_config row
    (with the token decrypted) or from env in the CLI wrapper."""
    # Target knowledge base + how to reach its indexing API
    knowledge_id: str
    knowledge_api_base_url: str
    knowledge_api_key: str
    # ClickUp connection
    api_token: str = ""
    workspace_id: str = ""
    space_ids: list = field(default_factory=list)
    # Folder selection (empty = all folders). ids and names are both honoured.
    folder_ids: list = field(default_factory=list)
    folder_names: list = field(default_factory=list)
    # Extra docs to index by id, in addition to the folder selection (additive, no reconcile).
    doc_ids: list = field(default_factory=list)
    # When True, crawl ONLY doc_ids and skip the folder loop (used by per-doc resync).
    only_doc_ids: bool = False
    # Folder names/ids to skip — exclusions win over the inclusion selection above.
    exclude_folder_name: list = field(default_factory=list)
    exclude_folder_id: list = field(default_factory=list)
    # Substring keywords matched (case-insensitive) against every folder/doc/page name;
    # a match skips that item (and a page's subtree). Empty = nothing skipped by name —
    # there is no built-in default (e.g. "archive" is only skipped if listed here).
    exclude_name_keywords: list = field(default_factory=list)
    # Optional narrower filters (used by CLI/testing; partial crawls skip reconcile)
    filter_doc_id: Optional[str] = None
    filter_single_page: bool = False
    force_reindex: bool = False
    skip_indexing: bool = False
    # Standalone Space-level docs (e.g. meeting notes) the folder crawl never reaches
    space_docs_enabled: bool = True
    space_doc_allowlist: list = field(default_factory=list)
    # Deletion reconcile (default OFF -> dry-run). When enabled, any doc absent from ClickUp
    # is removed — no percentage or full-folder threshold. The crawl-completeness gate
    # (reconcile only runs after a clean, consistent crawl) is what guards against partial data.
    deletion_enabled: bool = False
    # Pacing (defaults mirror the original script)
    page_delay_min: float = 0.3
    page_delay_max: float = 1.0
    folder_delay_min: float = 15.0
    folder_delay_max: float = 18.0
    clickup_min_gap: float = 0.6
    # ClickUp's docs/pages endpoint intermittently returns a partial subtree with a 200.
    # Re-read the tree until two consecutive reads agree on the page-id set before
    # trusting it; give up (and skip reconcile) after this many reads. <=1 disables the check.
    page_consistency_attempts: int = 4

    def __post_init__(self):
        # Normalise name matching to lowercase, mirroring the script.
        self.folder_names = [n.strip().lower() for n in (self.folder_names or []) if str(n).strip()]
        self.exclude_folder_name = [n.strip().lower() for n in (self.exclude_folder_name or []) if str(n).strip()]
        self.space_doc_allowlist = [n.strip().lower() for n in (self.space_doc_allowlist or []) if str(n).strip()]
        self.exclude_folder_id = [str(f).strip() for f in (self.exclude_folder_id or []) if str(f).strip()]
        self.exclude_name_keywords = [n.strip().lower() for n in (self.exclude_name_keywords or []) if str(n).strip()]
        self.folder_ids = [str(f).strip() for f in (self.folder_ids or []) if str(f).strip()]
        self.doc_ids = [str(d).strip() for d in (self.doc_ids or []) if str(d).strip()]


def _is_retryable(exc: BaseException) -> bool:
    """Retry on transient network errors and server-side HTTP failures.

    429 (rate limit) + 5xx are transient; 4xx (400/401/403/404) are permanent
    and must NOT be retried — retrying a bad token only burns the rate budget.
    """
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    return False


def _clickup_wait(retry_state) -> float:
    """Tenacity wait callback for ClickUp retries.

    On a 429, honor ClickUp's X-RateLimit-Reset (Unix ts) — wait until the window
    resets (+0.5s buffer), clamped to [1, 60]s. Otherwise exponential backoff.
    """
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
        reset_raw = exc.response.headers.get("X-RateLimit-Reset")
        try:
            delay = float(reset_raw) - time.time() + 0.5
            clamped = max(1.0, min(delay, 60.0))
            logger.warning(f"ClickUp 429 — waiting {clamped:.1f}s (X-RateLimit-Reset={reset_raw})")
            return clamped
        except (TypeError, ValueError):
            logger.warning("ClickUp 429 — no usable X-RateLimit-Reset, using exponential backoff")
    return min(2.0 ** (retry_state.attempt_number - 1), 10.0)


def _retryable_clickup(fn):
    """Decorator: retry a ClickUp helper on transient errors using the reset-aware wait."""
    return retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(3),
        wait=_clickup_wait,
        reraise=True,
    )(fn)


class ClickUpConnector(Connector):
    source = "clickup"

    def __init__(self, cfg: ClickUpConfig):
        self.cfg = cfg
        # Monotonic timestamp of the last ClickUp call — instance state so the
        # throttle is scoped to a single run (no cross-run/global leakage).
        self._last_clickup_call: float = 0.0

    # ------------------------------------------------------------------ headers
    def _clickup_headers(self) -> dict:
        return {"Authorization": self.cfg.api_token, "Content-Type": "application/json"}

    def _knowledge_headers(self) -> dict:
        return {"x-api-key": self.cfg.knowledge_api_key, "accept": "application/json"}

    # ----------------------------------------------------------------- throttle
    async def _throttle_clickup(self) -> None:
        """Sleep just enough so consecutive ClickUp reads stay >= clickup_min_gap apart.

        ClickUp's limit is 100 req/min/token -> 0.6s/req reciprocal. Spacing crawl
        calls keeps us under the limit by construction.
        https://developer.clickup.com/docs/rate-limits
        """
        now = time.monotonic()
        wait = self.cfg.clickup_min_gap - (now - self._last_clickup_call)
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_clickup_call = time.monotonic()

    def _effective_last_modified(self, page: dict) -> Optional[int]:
        """Page timestamp, or a forced-newer value when reindex override is enabled."""
        raw_last_modified = page.get("date_updated")
        try:
            page_last_modified = int(raw_last_modified) if raw_last_modified is not None else None
        except (TypeError, ValueError):
            page_last_modified = None

        if not self.cfg.force_reindex:
            return page_last_modified

        forced = max(int(time.time() * 1000), (page_last_modified or 0) + 1)
        logger.info("Force reindex for page %s: last_modified %r -> %s",
                    page.get("id"), raw_last_modified, forced)
        return forced

    # -------------------------------------------------------------- ClickUp API
    @_retryable_clickup
    async def get_spaces(self, client: httpx.AsyncClient) -> list[dict]:
        """GET /v2/team/{workspace_id}/space — spaces in the workspace (team)."""
        await self._throttle_clickup()
        resp = await client.get(
            f"{CLICKUP_V2}/team/{self.cfg.workspace_id}/space",
            headers=self._clickup_headers(),
        )
        resp.raise_for_status()
        spaces = resp.json().get("spaces", [])
        logger.info(f"Fetched {len(spaces)} spaces from workspace {self.cfg.workspace_id}")
        return spaces

    async def verify_selection(
        self, space_ids: Optional[list] = None, doc_ids: Optional[list] = None,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        """Confirm the given space/doc ids exist in ClickUp, raising ValueError naming any
        that don't. Backs both the crawl-time precheck (run_sync passes its own client) and
        the config-save / resync endpoints (no client -> one is opened here), so a stale id
        is rejected up front instead of failing opaquely once the crawl is underway.

        A 401/403 surfaces as a readable invalid-token error; other upstream failures
        propagate for the caller to map to a 502.
        """
        if client is None:
            async with httpx.AsyncClient(timeout=_HTTPX_TIMEOUT) as client:
                return await self.verify_selection(space_ids, doc_ids, client=client)
        try:
            if space_ids:
                available = {s.get("id") for s in await self.get_spaces(client)}
                missing = [sid for sid in space_ids if sid not in available]
                if missing:
                    raise ValueError(
                        f"space(s) not found in workspace {self.cfg.workspace_id} "
                        f"or not accessible with this token: {', '.join(missing)}"
                    )
            missing_docs = []
            for doc_id in doc_ids or []:
                try:
                    await self.get_doc(client, doc_id)
                except httpx.HTTPStatusError as e:
                    # ClickUp returns 403 not_found_or_authorized (never 404) for a doc that
                    # doesn't exist or isn't reachable; a genuinely bad token is 401 (below).
                    if e.response.status_code in (403, 404):
                        missing_docs.append(doc_id)
                    else:
                        raise
            if missing_docs:
                raise ValueError(
                    f"doc(s) not found in workspace {self.cfg.workspace_id} "
                    f"or not accessible with this token: {', '.join(missing_docs)}"
                )
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (401, 403):
                raise ClickUpAuthError("ClickUp token expired or invalid — please reconnect.") from e
            raise

    @_retryable_clickup
    async def get_folders(self, client: httpx.AsyncClient, space_id: str) -> list[dict]:
        """GET /v2/space/{space_id}/folder"""
        await self._throttle_clickup()
        resp = await client.get(f"{CLICKUP_V2}/space/{space_id}/folder", headers=self._clickup_headers())
        resp.raise_for_status()
        folders = resp.json().get("folders", [])
        logger.info(f"Fetched {len(folders)} folders from space {space_id}")
        return folders

    @_retryable_clickup
    async def get_docs_in_folder(self, client: httpx.AsyncClient, folder_id: str) -> list[dict]:
        """GET /v3/workspaces/{workspace_id}/docs?parent_type=FOLDER&parent_id={folder_id}"""
        await self._throttle_clickup()
        resp = await client.get(
            f"{CLICKUP_V3}/workspaces/{self.cfg.workspace_id}/docs",
            headers=self._clickup_headers(),
            params={"parent_type": "FOLDER", "parent_id": folder_id},
        )
        resp.raise_for_status()
        docs = resp.json().get("docs", [])
        logger.info(f"Fetched {len(docs)} docs from folder {folder_id}")
        return docs

    @_retryable_clickup
    async def get_docs_in_space(self, client: httpx.AsyncClient, space_id: str) -> list[dict]:
        """GET /v3/workspaces/{workspace_id}/docs?parent_type=SPACE&parent_id={space_id}

        Standalone Docs attached directly to the Space (e.g. meeting notes) — invisible
        to the folder-scoped query.
        """
        await self._throttle_clickup()
        resp = await client.get(
            f"{CLICKUP_V3}/workspaces/{self.cfg.workspace_id}/docs",
            headers=self._clickup_headers(),
            params={"parent_type": "SPACE", "parent_id": space_id},
        )
        resp.raise_for_status()
        docs = resp.json().get("docs", [])
        logger.info(f"Fetched {len(docs)} space-level docs from space {space_id}")
        return docs

    @_retryable_clickup
    async def get_doc(self, client: httpx.AsyncClient, doc_id: str) -> dict:
        """GET /v3/workspaces/{workspace_id}/docs/{doc_id} — one doc's metadata (id, name, parent)."""
        await self._throttle_clickup()
        resp = await client.get(
            f"{CLICKUP_V3}/workspaces/{self.cfg.workspace_id}/docs/{doc_id}",
            headers=self._clickup_headers(),
        )
        resp.raise_for_status()
        return resp.json()

    async def get_pages(self, client: httpx.AsyncClient, doc_id: str) -> list[dict]:
        """Fetch a doc's pages as a flat list, guarding against partial responses.

        ClickUp's docs/pages endpoint intermittently returns an incomplete subtree with
        a 200 (no error to retry on). Trusting that poisons the reconcile sweep: a page
        missing from `live_ids` looks deleted and gets reconciled away, while a genuinely
        removed page can never be reconciled if the crawl keeps flapping. So re-read the
        tree until two consecutive reads agree on the page-id set before trusting it.
        Raises if it never stabilises — the caller then marks the crawl incomplete and
        skips reconcile, which is the safe outcome (never delete on uncertain data).
        """
        if self.cfg.page_consistency_attempts <= 1:
            return await self._fetch_pages_once(client, doc_id)

        prev_ids: Optional[set] = None
        pages: list[dict] = []
        attempts = self.cfg.page_consistency_attempts
        for attempt in range(1, attempts + 1):
            pages = await self._fetch_pages_once(client, doc_id)
            ids = {p["id"] for p in pages}
            if prev_ids is not None and ids == prev_ids:
                logger.info(f"Doc {doc_id}: page set stable at {len(ids)} page(s) "
                            f"(confirmed on read {attempt})")
                return pages
            if prev_ids is not None:
                logger.warning(f"Doc {doc_id}: page set changed between reads "
                               f"({len(prev_ids)} -> {len(ids)}) — re-reading to confirm "
                               f"(read {attempt}/{attempts})")
            prev_ids = ids
        raise RuntimeError(
            f"Doc {doc_id}: page listing never stabilised after {attempts} reads "
            f"(last saw {len(prev_ids or [])} page(s)) — ClickUp returning inconsistent "
            f"trees; skipping this doc to protect the reconcile sweep")

    @_retryable_clickup
    async def _fetch_pages_once(self, client: httpx.AsyncClient, doc_id: str) -> list[dict]:
        """One full read of a doc's page tree, flattened to a list.

        Recursively flattens nested pages, skips deleted/archived/empty pages and any
        page (+ its subtree) whose name matches a configured exclude keyword. Handles
        cursor pagination.
        """
        url = f"{CLICKUP_V3}/workspaces/{self.cfg.workspace_id}/docs/{doc_id}/pages"
        params = {"max_page_depth": -1, "content_format": "text/md"}
        all_pages_flat = []
        cursor = None

        while True:
            if cursor:
                params["cursor"] = cursor

            await self._throttle_clickup()
            resp = await client.get(url, headers=self._clickup_headers(), params=params)
            resp.raise_for_status()

            raw = resp.json()
            top_level = raw if isinstance(raw, list) else raw.get("pages", [])

            def _flatten(pages: list[dict]) -> list[dict]:
                result = []
                for p in pages:
                    name = p.get("name", "")
                    if self._name_excluded(name):
                        logger.info(f"Skipping page and subtree: {p.get('id')} ({name!r}) — name matches an exclude keyword")
                        continue
                    if p.get("deleted") or p.get("archived") or not p.get("content"):
                        sub_pages = p.get("pages", [])
                        if sub_pages:
                            result.extend(_flatten(sub_pages))
                        continue
                    result.append(p)
                    sub_pages = p.get("pages", [])
                    if sub_pages:
                        result.extend(_flatten(sub_pages))
                return result

            all_pages_flat.extend(_flatten(top_level))

            cursor = raw.get("cursor") if isinstance(raw, dict) else None
            if not cursor:
                break

        logger.info(f"Fetched {len(all_pages_flat)} pages (flattened, excl. deleted/archived/empty) for doc {doc_id}")
        return all_pages_flat

    # ------------------------------------------------------------ Knowledge API
    async def get_upload_url(self, client: httpx.AsyncClient, page_id: str, page_name: str) -> tuple[str, str]:
        """POST /v1/{knowledge_id}/upload-url -> (presigned_put_url, download_presigned_url)."""
        logger.info(f"Requesting upload URL for page {page_id}")
        resp = await client.post(
            f"{self.cfg.knowledge_api_base_url}/{self.cfg.knowledge_id}/upload-url",
            headers=self._knowledge_headers(),
            data={"file_name": f"{page_name}.md", "content_type": "text/markdown"},
            timeout=15.0,
        )
        resp.raise_for_status()
        body = resp.json()
        return body["presigned_url"], body["download_presigned_url"]

    async def upload_to_presigned_url(self, presigned_url: str, content: str) -> None:
        """PUT the page markdown to the S3 presigned URL.

        Uses httpx with the shared bounded timeout (NOT requests, which defaults to no
        timeout). A stalled upload therefore raises a retryable httpx error instead of
        hanging the whole serial crawl forever — a missing timeout here previously froze
        runs in 'running' until the 90-min stale-requeue. The surrounding sync_page retry
        re-mints a fresh URL and retries transient failures; a 4xx (bad/expired signature)
        is non-retryable and fails fast.
        """
        async with httpx.AsyncClient(timeout=_HTTPX_TIMEOUT) as client:
            resp = await client.put(
                presigned_url,
                content=content.encode("utf-8"),
                headers={"Content-Type": "text/markdown"},
            )
        resp.raise_for_status()
        logger.info("Content uploaded successfully to presigned URL")

    async def post_document(self, client: httpx.AsyncClient, file_url: str, extra_metadata: dict) -> dict:
        """POST /v1/{knowledge_id}/documents with file_url + extra_metadata."""
        resp = await client.post(
            f"{self.cfg.knowledge_api_base_url}/{self.cfg.knowledge_id}/documents",
            headers=self._knowledge_headers(),
            data={"file_url": file_url, "extra_metadata": json.dumps(extra_metadata)},
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.json()

    async def _fetch_kb_docs(self, client: httpx.AsyncClient, folder_id: str) -> list[dict]:
        """GET the COMPLETE slim doc list for one ClickUp folder: [{object_id, source_id,
        last_modified}]. Server paginates to exhaustion, so this is the full set."""
        metadata_filter = json.dumps({"source": "clickup", "clickup_folder_id": folder_id})
        resp = await client.get(
            f"{self.cfg.knowledge_api_base_url}/{self.cfg.knowledge_id}/documents",
            headers=self._knowledge_headers(),
            params={"metadata_filter": metadata_filter, "include_source_id": "true"},
            timeout=60.0,
        )
        resp.raise_for_status()
        return resp.json().get("documents", [])

    async def list_kb_docs_in_folder(self, client: httpx.AsyncClient, folder_id: str) -> dict:
        """Enumerate the COMPLETE set of KB docs for one ClickUp folder.

        Returns { object_id: source_id } for docs with a usable source_id. Entries
        missing source_id are skipped (never deleted — unmatchable against live ids).
        """
        logger.info(f"Listing KB docs for folder {folder_id}")
        documents = await self._fetch_kb_docs(client, folder_id)

        kb_map: dict = {}
        missing_source_id = 0
        for d in documents:
            object_id = d.get("object_id")
            source_id = d.get("source_id")
            if not object_id:
                continue
            if not source_id:
                missing_source_id += 1
                continue
            kb_map[object_id] = source_id

        logger.info(f"Folder {folder_id}: KB returned {len(documents)} docs — "
                    f"{len(kb_map)} reconcilable, {missing_source_id} skipped (no source_id)")
        return kb_map

    async def get_folder_freshness(self, client: httpx.AsyncClient, folder_id: str) -> dict:
        """Map { source_id: stored_last_modified } for a folder's already-indexed docs.

        Lets the crawl skip re-uploading unchanged pages BEFORE any upload/POST. Entries
        without a usable source_id or last_modified are omitted (so they fall through to
        the upload + server-side freshness check — the authoritative backstop). On dup
        source_ids (legacy), keeps the newest stored timestamp.
        """
        fresh: dict = {}
        try:
            documents = await self._fetch_kb_docs(client, folder_id)
        except Exception as e:
            # Non-fatal: no map -> nothing pre-skipped -> every page takes the normal
            # upload + server-check path. Never blocks or mis-skips a sync.
            logger.warning(f"Folder {folder_id}: freshness prefetch failed [{type(e).__name__}]: {e} — no pre-skip this run")
            return fresh
        for d in documents:
            sid = d.get("source_id")
            lm = d.get("last_modified")
            if sid is None or lm is None:
                continue
            try:
                lm = int(lm)
            except (TypeError, ValueError):
                continue
            if sid not in fresh or lm > fresh[sid]:
                fresh[sid] = lm
        logger.info(f"Folder {folder_id}: freshness map has {len(fresh)} stored timestamp(s)")
        return fresh

    async def delete_kb_document(self, client: httpx.AsyncClient, object_id: str) -> str:
        """DELETE /v1/{knowledge_id}/documents/{object_id}. 404 -> 'already_gone'.

        Returns: 'deleted' | 'already_gone' | 'error'
        """
        @retry(
            retry=retry_if_exception(_is_retryable),
            stop=stop_after_attempt(3),
            wait=wait_exponential_jitter(initial=1, max=10, jitter=1),
            reraise=True,
        )
        async def _attempt() -> str:
            resp = await client.delete(
                f"{self.cfg.knowledge_api_base_url}/{self.cfg.knowledge_id}/documents/{object_id}",
                headers=self._knowledge_headers(),
                timeout=30.0,
            )
            if resp.status_code == 404:
                logger.info(f"Delete {object_id}: already gone (404) — treating as success")
                return "already_gone"
            resp.raise_for_status()
            logger.info(f"Delete {object_id}: accepted — {resp.json().get('message', 'ok')}")
            return "deleted"

        try:
            return await _attempt()
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP {e.response.status_code} deleting {object_id}: {e.response.text}")
            return "error"
        except Exception as e:
            logger.error(f"Error deleting {object_id} [{type(e).__name__}]: {e}", exc_info=True)
            return "error"

    # ------------------------------------------------------- reconcile (delete)
    async def reconcile_folder(self, client: httpx.AsyncClient, folder_id: str, live_ids: set, stats: SyncStats) -> bool:
        """Remove KB docs for folder_id whose source_id is no longer live in ClickUp.

        Any doc absent from ClickUp is deleted — no percentage or full-folder threshold.
        Only skipped for a partial crawl (doc/single-page filter) or dry-run when
        deletion_enabled is false. Reconcile is reached only after a complete, consistent
        crawl (see run_sync / get_pages), which is what protects against acting on partial
        ClickUp data. Folds deleted count into stats. Returns True if the sweep ran.
        """
        if self.cfg.filter_doc_id or self.cfg.filter_single_page:
            logger.warning(f"Folder {folder_id}: skipping reconcile — partial crawl (doc/single-page filter active)")
            return False

        kb_map = await self.list_kb_docs_in_folder(client, folder_id)
        if not kb_map:
            logger.info(f"Folder {folder_id}: no reconcilable KB docs — nothing to sweep")
            return True

        to_delete = [oid for oid, sid in kb_map.items() if sid not in live_ids]
        logger.info(f"Folder {folder_id}: live={len(live_ids)} kb={len(kb_map)} to_delete={len(to_delete)}")

        if not to_delete:
            logger.info(f"Folder {folder_id}: in sync — nothing to delete")
            return True

        for idx, oid in enumerate(to_delete, 1):
            sid = kb_map[oid]
            if not self.cfg.deletion_enabled:
                logger.info(f"[DRY-RUN] [{idx}/{len(to_delete)}] would delete {oid} (source_id={sid})")
                continue
            logger.info(f"[{idx}/{len(to_delete)}] deleting {oid} (source_id={sid})")
            outcome = await self.delete_kb_document(client, oid)
            if outcome == "deleted":
                stats.deleted += 1
            if idx < len(to_delete):
                await asyncio.sleep(random.uniform(self.cfg.page_delay_min, self.cfg.page_delay_max))

        return True

    # ------------------------------------------------------------- core crawl
    async def sync_page(self, knowledge_client: httpx.AsyncClient, space_id: str,
                        folder_id: str, folder_name: str, doc: dict, page: dict) -> str:
        """Upload one page via presigned URL then POST to the documents endpoint.

        Returns: 'indexed' | 'skipped' | 'skipped_empty' | 'error'
        """
        page_id = page["id"]
        page_name = page.get("name", page_id)
        content = page.get("content", "")

        if not content or not content.strip():
            logger.info(f"Skipping empty page: {page_name} ({page_id})")
            return "skipped_empty"

        extra_metadata = {
            "source_id": page_id,
            "clickup_page_id": page_id,
            "clickup_doc_id": doc["id"],
            "clickup_folder_id": folder_id,
            "clickup_folder_name": folder_name,
            "clickup_space_id": space_id,
            "clickup_doc_name": doc.get("name", ""),
            "clickup_page_name": page_name,
            "clickup_parent_page_id": page.get("parent_page_id"),
            "last_modified": self._effective_last_modified(page),
            "source": "clickup",
            "clickup_page_url": f"https://app.clickup.com/{page.get('workspace_id', self.cfg.workspace_id)}/v/dc/{doc['id']}/{page_id}",
        }
        extra_metadata = {k: v for k, v in extra_metadata.items() if v is not None}

        @retry(
            retry=retry_if_exception(_is_retryable),
            stop=stop_after_attempt(3),
            wait=wait_exponential_jitter(initial=1, max=10, jitter=1),
            reraise=True,
        )
        async def _attempt() -> str:
            presigned_put_url, download_url = await self.get_upload_url(knowledge_client, page_id, page_name)
            await self.upload_to_presigned_url(presigned_put_url, content)
            logger.info(f"Uploaded page content to S3: {page_name} ({page_id})")
            result = await self.post_document(knowledge_client, download_url, extra_metadata)
            if result.get("status") == "skipped":
                logger.warning(f"Up-to-date, skipped: {page_name} ({page_id}) — {result.get('reason', 'unknown')}")
                return "skipped"
            logger.info(f"Indexed: {page_name} ({page_id}) -> task_id={result.get('task_id')}")
            return "indexed"

        try:
            return await _attempt()
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP {e.response.status_code} for page {page_id} after retries: {e.response.text}")
            return "error"
        except Exception as e:
            logger.error(f"Error syncing page {page_id} [{type(e).__name__}]: {e}", exc_info=True)
            return "error"

    async def crawl_and_sync_doc(self, clickup_client: httpx.AsyncClient, knowledge_client: httpx.AsyncClient,
                                 space_id: str, folder_id: str, folder_name: str, doc: dict,
                                 stats: SyncStats, fresh_map: Optional[dict] = None) -> tuple[set, bool]:
        """Crawl one doc's pages and sync each. Returns (live_ids, crawl_ok).

        live_ids: every page id seen in ClickUp for this doc (incl. errored pages —
        present == live, never reconcile away). crawl_ok: False if the page fetch failed.

        fresh_map ({source_id: stored_last_modified}) lets us skip the upload + POST for
        pages whose stored timestamp is already current — a no-change run then does no
        network work per page. The server-side freshness check remains the backstop.
        """
        doc_id = doc["id"]
        live_ids: set = set()
        fresh_map = fresh_map or {}

        try:
            pages = await self.get_pages(clickup_client, doc_id)
        except Exception as e:
            logger.error(f"Failed to fetch pages for doc {doc_id} after retries [{type(e).__name__}]: {e}")
            return live_ids, False
        logger.info(f"Processing {len(pages)} pages from doc {doc_id}")

        if self.cfg.filter_single_page:
            pages = pages[:1]
            logger.info("Limited to 1 page due to filter_single_page")

        for idx, page in enumerate(pages, 1):
            page_id = page["id"]
            live_ids.add(page_id)  # live regardless of index outcome

            if self.cfg.skip_indexing:
                stats.skipped += 1
                continue

            # Pre-upload freshness skip: if the KB already has this page at >= the page's
            # current timestamp, don't upload or POST at all. force_reindex bumps the page
            # timestamp (see _effective_last_modified), so it never matches here -> always
            # re-indexes. Missing/None stored ts -> falls through to the normal path.
            page_lm = self._effective_last_modified(page)
            stored_lm = fresh_map.get(page_id)
            if stored_lm is not None and page_lm is not None and stored_lm >= page_lm:
                logger.info(f"Up-to-date (pre-upload skip): {page.get('name', page_id)} ({page_id}) "
                            f"stored={stored_lm} >= page={page_lm}")
                stats.skipped += 1
                # No upload/POST happened -> no knowledge-side load to pace -> no delay.
                continue

            outcome = await self.sync_page(knowledge_client, space_id, folder_id, folder_name, doc, page)
            if outcome == "indexed":
                stats.indexed += 1
            elif outcome in ("skipped", "skipped_empty"):
                stats.skipped += 1
            else:
                stats.errors += 1
            if idx < len(pages):
                await asyncio.sleep(random.uniform(self.cfg.page_delay_min, self.cfg.page_delay_max))

        return live_ids, True

    async def sync_space_docs(self, clickup_client: httpx.AsyncClient, knowledge_client: httpx.AsyncClient,
                              space_id: str, stats: SyncStats) -> None:
        """Crawl + index standalone Docs directly under the Space (parent_type=SPACE).

        Each matched doc is its own reconcile group (folder_id = doc id, folder_name =
        doc name). Gated by space_docs_enabled, exclude keywords, filter_doc_id, allowlist.
        """
        if not self.cfg.space_docs_enabled:
            logger.info(f"Space {space_id}: skipping space-level docs (disabled)")
            return

        try:
            docs = await self.get_docs_in_space(clickup_client, space_id)
        except Exception as e:
            logger.error(f"Failed to list space-level docs for space {space_id} [{type(e).__name__}]: {e}")
            return

        for doc in docs:
            doc_id = doc["id"]
            doc_name = doc.get("name", "")
            if self._name_excluded(doc_name):
                logger.info(f"Skipping space doc {doc_id} ({doc_name!r}) — name matches an exclude keyword")
                continue
            if self.cfg.filter_doc_id and doc_id != self.cfg.filter_doc_id:
                continue
            if self.cfg.space_doc_allowlist and not any(n in doc_name.lower() for n in self.cfg.space_doc_allowlist):
                logger.info(f"Skipping space doc {doc_id} ({doc_name!r}) — not in allowlist")
                continue

            # Each space doc is its own reconcile group (folder_id = doc id), so its
            # freshness map is scoped to that doc id.
            fresh_map = ({} if self.cfg.force_reindex
                         else await self.get_folder_freshness(knowledge_client, doc_id))
            live_ids, crawl_ok = await self.crawl_and_sync_doc(
                clickup_client, knowledge_client, space_id, doc_id, doc_name, doc, stats, fresh_map)
            if not crawl_ok:
                logger.warning(f"Space doc {doc_id}: skipping reconcile — page fetch failed (live_ids partial)")
                continue
            await self.reconcile_folder(knowledge_client, doc_id, live_ids, stats)

    async def sync_explicit_docs(self, clickup_client: httpx.AsyncClient,
                                 knowledge_client: httpx.AsyncClient, stats: SyncStats) -> None:
        """Crawl + index docs named explicitly in cfg.doc_ids.

        Additive: these are indexed on top of the folder crawl and never reconcile-deleted
        (a doc id appearing here is an inclusion, not a scope boundary). Metadata folder is
        the doc's real parent when known, else the doc id, so freshness pre-skip still works.
        """
        for doc_id in self.cfg.doc_ids:
            try:
                doc = await self.get_doc(clickup_client, doc_id)
            except Exception as e:
                logger.error(f"Explicit doc {doc_id}: fetch failed [{type(e).__name__}]: {e}")
                stats.errors += 1
                continue
            doc_name = doc.get("name", "")
            if self._name_excluded(doc_name):
                logger.info(f"Skipping explicit doc {doc_id} ({doc_name!r}) — name matches an exclude keyword")
                continue
            folder_id = str((doc.get("parent") or {}).get("id") or doc_id)
            space_id = str(doc.get("workspace_id") or self.cfg.workspace_id or "")
            fresh_map = ({} if self.cfg.force_reindex
                         else await self.get_folder_freshness(knowledge_client, folder_id))
            await self.crawl_and_sync_doc(
                clickup_client, knowledge_client, space_id, folder_id, doc_name, doc, stats, fresh_map)

    def _name_excluded(self, name: str) -> bool:
        """True if `name` contains any configured exclude keyword (case-insensitive).
        Empty keyword list => never excludes. This is the config-driven replacement for
        the former hardcoded 'archive' skip; applied to folder, doc and page names."""
        if not self.cfg.exclude_name_keywords:
            return False
        lowered = (name or "").lower()
        return any(kw in lowered for kw in self.cfg.exclude_name_keywords)

    def _folder_selected(self, folder_id: str, folder_name: str) -> bool:
        """Apply the configured folder selection. Empty selection = all folders.
        Exclusions win: a folder matching exclude_folder_id/exclude_folder_name, or whose
        name matches an exclude keyword, is skipped regardless of the include selection."""
        if folder_id in self.cfg.exclude_folder_id:
            return False
        if folder_name.lower() in self.cfg.exclude_folder_name:
            return False
        if self._name_excluded(folder_name):
            return False
        if not (self.cfg.folder_ids or self.cfg.folder_names):
            return True
        id_match = folder_id in self.cfg.folder_ids if self.cfg.folder_ids else False
        name_match = folder_name.lower() in self.cfg.folder_names if self.cfg.folder_names else False
        return id_match or name_match

    # --------------------------------------------------------- Connector iface
    async def validate_credentials(self) -> bool:
        async with httpx.AsyncClient(timeout=_HTTPX_TIMEOUT) as client:
            try:
                await self._throttle_clickup()
                resp = await client.get(f"{CLICKUP_V2}/team", headers=self._clickup_headers())
                return resp.status_code == 200
            except Exception as e:
                logger.error(f"ClickUp credential validation failed [{type(e).__name__}]: {e}")
                return False

    async def list_spaces(self) -> list[dict]:
        async with httpx.AsyncClient(timeout=_HTTPX_TIMEOUT) as client:
            spaces = await self.get_spaces(client)
            return [{"id": s.get("id"), "name": s.get("name", "")} for s in spaces]

    async def list_folders(self, space_id: str) -> list[dict]:
        async with httpx.AsyncClient(timeout=_HTTPX_TIMEOUT) as client:
            folders = await self.get_folders(client, space_id)
            return [{"id": f.get("id"), "name": f.get("name", "")} for f in folders]

    async def run_sync(self, on_progress=None) -> SyncStats:
        stats = SyncStats()
        space_ids = self.cfg.space_ids or []

        async def _flush():
            if on_progress is None:
                return
            try:
                await on_progress(stats)
            except Exception as e:
                logger.warning(f"Progress flush failed [{type(e).__name__}]: {e}")

        async with (
            httpx.AsyncClient(timeout=_HTTPX_TIMEOUT) as clickup_client,
            httpx.AsyncClient(timeout=_HTTPX_TIMEOUT) as knowledge_client,
        ):
            # Resync path: crawl only the explicit doc_ids, skip the folder/space sweep.
            if self.cfg.only_doc_ids:
                await self.sync_explicit_docs(clickup_client, knowledge_client, stats)
                await _flush()
                logger.info(f"Sync complete (doc_ids only) — indexed={stats.indexed} "
                            f"skipped={stats.skipped} errors={stats.errors}")
                return stats

            # Verify credentials + spaces before crawling; a bad token or stale
            # space_id fails the run fast with a clear reason.
            if not space_ids:
                raise ValueError("No spaces configured for this ClickUp connector")
            await self.verify_selection(space_ids=space_ids, client=clickup_client)

            for space_id in space_ids:
                logger.info(f"Processing space: {space_id}")
                try:
                    folders = await self.get_folders(clickup_client, space_id)
                except Exception as e:
                    # Record, don't swallow: a failed space listing means this run did not
                    # cover its scope, so the runner must surface it (not report success).
                    logger.error(f"Failed to list folders for space {space_id} [{type(e).__name__}]: {e}")
                    stats.note_crawl_error(f"list folders for space {space_id}: {type(e).__name__}: {e}")
                    continue

                for folder in folders:
                    folder_id = folder["id"]
                    folder_name = folder.get("name", "")
                    if not self._folder_selected(folder_id, folder_name):
                        logger.info(f"Skipping folder {folder_id} ({folder_name!r}) — not in selection")
                        continue

                    try:
                        docs = await self.get_docs_in_folder(clickup_client, folder_id)
                    except Exception as e:
                        # Record, don't swallow: without this folder's docs its reconcile
                        # (deletes) can't run, so the run must not be reported as a success.
                        logger.error(f"Failed to list docs for folder {folder_id} [{type(e).__name__}]: {e}")
                        stats.note_crawl_error(f"list docs for folder {folder_id}: {type(e).__name__}: {e}")
                        continue

                    # Prefetch stored timestamps once per folder so unchanged pages skip
                    # upload+POST entirely. Skipped when force_reindex (nothing would match).
                    fresh_map = ({} if self.cfg.force_reindex
                                 else await self.get_folder_freshness(knowledge_client, folder_id))

                    folder_live_ids: set = set()
                    crawl_complete = True
                    for doc in docs:
                        doc_id = doc["id"]
                        doc_name = doc.get("name", "")
                        if self._name_excluded(doc_name):
                            logger.info(f"Skipping document {doc_id} ({doc_name!r}) — name matches an exclude keyword")
                            continue
                        if self.cfg.filter_doc_id and doc_id != self.cfg.filter_doc_id:
                            continue
                        doc_live_ids, crawl_ok = await self.crawl_and_sync_doc(
                            clickup_client, knowledge_client, space_id, folder_id, folder_name, doc, stats, fresh_map)
                        folder_live_ids |= doc_live_ids
                        if not crawl_ok:
                            crawl_complete = False
                        await _flush()  # per-doc live progress

                    if not crawl_complete:
                        logger.warning(f"Folder {folder_id}: skipping reconcile — crawl incomplete (live_ids partial)")
                    else:
                        await self.reconcile_folder(knowledge_client, folder_id, folder_live_ids, stats)

                    # Folder gap lets the backend drain queued indexing — skip when nothing indexed.
                    if not self.cfg.skip_indexing:
                        folder_delay = random.uniform(self.cfg.folder_delay_min, self.cfg.folder_delay_max)
                        logger.info(f"Folder {folder_id} done — sleeping {folder_delay:.2f}s before next folder")
                        await asyncio.sleep(folder_delay)

                # Standalone space-level docs after all folders in this space.
                await self.sync_space_docs(clickup_client, knowledge_client, space_id, stats)
                await _flush()

            # Docs named explicitly by id — indexed in addition to the folder crawl.
            if self.cfg.doc_ids:
                await self.sync_explicit_docs(clickup_client, knowledge_client, stats)
                await _flush()

        logger.info(f"Sync complete — indexed={stats.indexed} skipped={stats.skipped} "
                    f"errors={stats.errors} deleted={stats.deleted}")
        return stats

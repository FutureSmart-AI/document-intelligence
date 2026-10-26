"""Connector framework for self-serve external indexing.

A Connector encapsulates one external source (ClickUp first; Notion etc. later).
Knowledge owns the control plane — config storage, scheduling, run tracking — and
a Connector does the source-specific work:

  - validate_credentials() : confirm a token works (wizard "Connect" step)
  - list_spaces()          : browse top-level containers (wizard "Select" step)
  - list_folders(space_id) : browse folders within a space (wizard "Select" step)
  - run_sync()             : crawl selected scope and feed each page to the
                             existing knowledge indexing path, then reconcile deletes

run_sync deliberately does NO embedding/enrichment — it hands every page to the
knowledge HTTP indexing endpoints, so the heavy indexing still runs in the external
worker (workers.tasks.process_and_index_file), exactly like a file upload.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Awaitable, Callable, List


@dataclass
class SyncStats:
    """Run outcome, folded from per-page crawl results + the reconcile sweep."""
    indexed: int = 0
    skipped: int = 0
    errors: int = 0
    deleted: int = 0
    # Crawl-level failures: a folder/space/doc *listing* that errored out, so the sweep
    # could not cover its full scope (and reconcile deletes never ran for it). Distinct
    # from per-page index `errors`. When non-empty the run did NOT fully execute and must
    # not be reported as a clean success — otherwise a swallowed ClickUp/network error
    # looks identical to a genuine no-op "completed" run.
    crawl_errors: List[str] = field(default_factory=list)

    def note_crawl_error(self, msg: str) -> None:
        """Record a scope-enumeration failure and reflect it in the error count."""
        self.crawl_errors.append(msg)
        self.errors += 1

    def as_dict(self) -> dict:
        return {
            "indexed": self.indexed,
            "skipped": self.skipped,
            "errors": self.errors,
            "deleted": self.deleted,
        }


# Best-effort async hook to persist live progress mid-crawl. Must never raise.
ProgressCallback = Callable[[SyncStats], Awaitable[None]]


class Connector(ABC):
    """Interface every source connector implements."""

    #: short source identifier, e.g. "clickup"
    source: str = "base"

    @abstractmethod
    async def validate_credentials(self) -> bool:
        """Return True if the configured credentials authenticate successfully."""

    @abstractmethod
    async def list_spaces(self) -> list[dict]:
        """Return [{"id", "name"}, ...] of top-level containers (spaces)."""

    @abstractmethod
    async def list_folders(self, space_id: str) -> list[dict]:
        """Return [{"id", "name"}, ...] of folders within a space."""

    @abstractmethod
    async def run_sync(self, on_progress: "ProgressCallback | None" = None) -> SyncStats:
        """Crawl the configured scope, index each page, reconcile deletions.

        on_progress, if given, is flushed periodically with current stats.
        """

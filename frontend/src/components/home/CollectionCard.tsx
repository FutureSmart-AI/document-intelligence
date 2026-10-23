"use client";

import { useRouter } from "next/navigation";

import dayjs from "dayjs";
import relativeTime from "dayjs/plugin/relativeTime";

import Icon from "@/components/Icon";
import { IconBtn, Pill } from "@/components/ui";
import { useCollectionStats, type Collection } from "@/data/collections";
import { COLLECTION_CARD_HEIGHT } from "@/lib/constants";
import { collectionIcon } from "@/lib/documentIcon";
import { collectionRoute, ROUTES } from "@/lib/routes";
import { cn } from "@/platform/utils";

dayjs.extend(relativeTime);

interface CollectionCardProps {
  collection: Collection;
  /**
   * Asks the board to confirm and delete this collection. Omitted for one
   * somebody else owns — offering the action only to refuse it later is worse
   * than not offering it.
   */
  onDelete?: (collection: Collection) => void;
  /** Copies the collection id, when the surface offers that. */
  onCopyId?: (collection: Collection) => void;
}

/**
 * One collection on the board.
 *
 * The three facts it carries are the artifact's: what schema the collection has,
 * how many documents are in it (and how many are still being read), and when it
 * last changed. Each is read live — the counts come from the collection's own
 * documents and extracted fields, so a card never claims a number the
 * collection does not have.
 *
 * Drawn in the same card grammar as the NL2SQL dashboard: a washed plate, one
 * centred mark, the name on the display face, the counts as pills, and a row of
 * actions in the card body. Every card is the same height, so the pill row
 * is what clips rather than the name.
 *
 * The mark is not fixed: it is what the collection holds — a person for
 * resumes, a signature for contracts, a globe for a crawled site, layers for a
 * genuinely mixed one. See `lib/documentIcon`. The name is enough to pick one
 * before the documents return, so a newly added card is not a generic file
 * while its stats load.
 */
export default function CollectionCard({
  collection,
  onDelete,
  onCopyId,
}: CollectionCardProps) {
  const router = useRouter();
  const { data: stats, isLoading } = useCollectionStats(
    collection.knowledgeId,
    collection.name,
  );

  const schema = !stats
    ? "Reading schema…"
    : stats.fields === 0
      ? "Basic index — no fields"
      : stats.customFields > 0
        ? `${stats.fields} fields · ${stats.customFields} custom`
        : "Default schema";

  const icon = stats?.icon ?? collectionIcon(collection.name, []);

  const open = () =>
    router.push(collectionRoute(ROUTES.knowledge, collection.id));

  const actions = (
    [
      {
        key: "settings",
        icon: "sun" as const,
        label: `Settings for ${collection.name}`,
        run: () => router.push(collectionRoute(ROUTES.settings, collection.id)),
      },
      collection.shared
        ? onCopyId && {
            key: "copy",
            icon: "copy" as const,
            label: `Copy the ID of ${collection.name}`,
            run: () => onCopyId(collection),
          }
        : {
            key: "share",
            icon: "share" as const,
            label: `Share ${collection.name}`,
            run: () =>
              router.push(collectionRoute(ROUTES.share, collection.id)),
          },
      onDelete && {
        key: "delete",
        icon: "trash" as const,
        label: `Delete ${collection.name}`,
        run: () => onDelete(collection),
      },
    ].filter(Boolean) as Array<{
      key: string;
      icon: "chart" | "sun" | "copy" | "share" | "trash";
      label: string;
      run: () => void;
    }>
  ).map((action) => (
    <span key={action.key} className="flex items-center">
      <IconBtn
        label={action.label}
        className="size-8 rounded-di-sm border border-line bg-surface hover:border-accent hover:bg-accent-soft hover:text-accent"
        onClick={(event) => {
          // The whole card opens the collection, so a row action must not
          // bubble up into it.
          event.stopPropagation();
          action.run();
        }}
      >
        <Icon name={action.icon} className="size-4" />
      </IconBtn>
    </span>
  ));

  return (
    <div
      role="button"
      tabIndex={0}
      aria-label={`Open ${collection.name}`}
      onClick={open}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          open();
        }
      }}
      className={cn(
        "group relative flex cursor-pointer flex-col overflow-hidden rounded-di-lg text-left",
        "border border-line bg-linear-to-br from-accent-soft to-surface shadow-card",
        "transition-all duration-200 hover:-translate-y-1 hover:border-accent hover:shadow-pop",
        COLLECTION_CARD_HEIGHT,
      )}
    >
      <span className="flex grow flex-col items-center overflow-hidden p-5 text-center">
        <span className="mb-3 flex size-14 shrink-0 items-center justify-center rounded-2xl border border-accent-soft-2 bg-accent-soft text-accent shadow-inner">
          <Icon name={icon} className="size-7" />
        </span>

        {/* `shrink-0` on the name and the count line: the card is a fixed
            height, so a collection with several pills would otherwise squeeze
            its own name out of existence. The pills below are what clips. */}
        <span className="di-display line-clamp-1 w-full shrink-0 text-lg font-bold text-ink transition-colors group-hover:text-accent">
          {collection.name}
        </span>

        <span className="mt-2 flex shrink-0 items-center justify-center gap-1.5 text-[12px] font-medium text-ink-2">
          <Icon name="spark" className="size-3 text-accent" />
          <span className="max-w-50 truncate">
            <b className="tnum font-bold text-ink">
              {isLoading ? "—" : (stats?.documents ?? 0)}
            </b>{" "}
            document{stats?.documents === 1 ? "" : "s"}
            {collection.updatedAt
              ? ` · Updated ${dayjs(collection.updatedAt).fromNow()}`
              : ""}
          </span>
        </span>

        <span className="mt-3 flex flex-wrap content-start justify-center gap-1.5 overflow-hidden">
          {/* First pill on a shared card: what makes it different from every
              other card on the board is that somebody else owns it. */}
          {collection.shared ? (
            <Pill tone="accent">
              <Icon name="users" className="size-3" />
              Shared with you
            </Pill>
          ) : null}
          <Pill tone="neutral">{schema}</Pill>
          {/* Whether a collection still answers questions lives on its chat
              config, one request per collection — too much for a board of cards,
              so the state is shown once a collection is open (see `AppShell`). */}
          {stats?.processing ? (
            <Pill tone="warn">
              <span className="di-spinner" />
              {stats.processing} processing
            </Pill>
          ) : null}
          {stats?.failed ? (
            <Pill tone="danger">
              <span className="size-1.5 rounded-full bg-danger-dot" />
              {stats.failed} failed
            </Pill>
          ) : null}
        </span>

        <span className="mt-auto flex w-full shrink-0 items-center justify-center gap-1.5 border-t border-line-soft pt-4">
          {actions}
        </span>
      </span>
    </div>
  );
}

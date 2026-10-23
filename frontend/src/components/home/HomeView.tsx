"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

import { BrandMark } from "@/components/Brand";
import Icon from "@/components/Icon";
import { Btn, EmptyNote, Modal } from "@/components/ui";
import {
  useCollections,
  useDeleteCollection,
  type Collection,
} from "@/data/collections";
import { apiMessage } from "@/lib/apiError";
import { copyText } from "@/lib/clipboard";
import { COLLECTION_CARD_HEIGHT, COLLECTION_GRID } from "@/lib/constants";
import { productTagline } from "@/lib/platformCopy";
import { ROUTES } from "@/lib/routes";
import { useToast } from "@/platform/components/ToastContext";
import { cn } from "@/platform/utils";

import AccountCards from "./AccountCards";
import CollectionCard from "./CollectionCard";
import CollectionGridSkeleton from "./CollectionCardSkeleton";

type HomeTab = "mine" | "shared";

/**
 * The board of collections — the product's front door once you are signed in.
 *
 * No rail here: being inside a collection is what the rail signals, and on this
 * screen you are not in one yet.
 */
export default function HomeView() {
  const { toast } = useToast();
  const router = useRouter();
  const [tab, setTab] = useState<HomeTab>("mine");
  const [pendingDelete, setPendingDelete] = useState<Collection | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const { collections, isLoading, isError } = useCollections();
  const remove = useDeleteCollection();

  const confirmDelete = async () => {
    if (!pendingDelete) return;
    setDeleteError(null);
    try {
      await remove.mutateAsync(pendingDelete);
      toast.success(`${pendingDelete.name} deleted.`);
      setPendingDelete(null);
    } catch (error) {
      // The dialog stays open with the reason: a card that quietly refuses to
      // disappear reads as the list being stale.
      setDeleteError(
        apiMessage(error, "That collection could not be deleted."),
      );
    }
  };

  const copyId = async (collection: Collection) => {
    const copied = await copyText(collection.id);
    toast[copied ? "success" : "error"](
      copied
        ? `Collection ID for ${collection.name} copied.`
        : "Could not copy the collection ID.",
    );
  };

  /**
   * The one control on this screen that needs no data, so it is drawn while the
   * list is still loading as well as after it lands.
   */
  const createTile = (
    <button
      type="button"
      data-demo="create-collection"
      // A screen, not a dialog: creating a collection has steps worth seeing
      // before you commit to the first one, and a dialog can only ever show you
      // the step you are on.
      onClick={() => router.push(ROUTES.create)}
      className={cn(
        "group flex cursor-pointer flex-col items-center justify-center gap-2 rounded-di-lg p-5 text-center",
        "border border-dashed border-line-strong bg-surface text-ink-2 shadow-card",
        "transition-all duration-200 hover:-translate-y-1 hover:border-accent hover:bg-accent-soft hover:shadow-pop",
        COLLECTION_CARD_HEIGHT,
      )}
    >
      <span className="flex size-14 items-center justify-center rounded-2xl border border-accent-soft-2 bg-accent-soft text-accent shadow-inner">
        <Icon name="plus" className="size-7" />
      </span>
      <b className="di-display text-[15px] font-bold text-ink transition-colors group-hover:text-accent">
        Create document collection
      </b>
      <span className="text-[12px]">
        A name, what it&rsquo;s about, and your documents
      </span>
    </button>
  );

  // One list, split by who owns it. `shared` is the account's own answer from
  // the service's validate route, so a collection someone invited this account
  // to never sits among the ones it created.
  const mine = collections.filter((collection) => !collection.shared);
  const shared = collections.filter((collection) => collection.shared);
  const shown = tab === "mine" ? mine : shared;

  return (
    // Full width: the board is the one screen whose content is a grid that
    // keeps filling, so a centred column would leave the plane empty on both
    // sides at the widths this product is actually used at. The shell's own
    // padding is the only gutter.
    <div>
      <div className="mb-6 flex items-center gap-4">
        <BrandMark className="size-11" />
        <div>
          <h1 className="m-0 text-[30px] font-extrabold tracking-[-0.02em] text-ink text-balance">
            Document Intelligence
          </h1>
          <p className="m-0 mt-1 max-w-[60ch] text-[14px] text-ink-2">
            {productTagline()}
          </p>
        </div>
      </div>

      <AccountCards />

      {isError && (
        <EmptyNote className="mb-3.5">
          Your collections could not be loaded. Refresh to try again.
        </EmptyNote>
      )}

      {/* Two tabs, always both: "Shared with me" is where an invited collection
          arrives, and a tab that appears only once something is in it leaves
          nowhere to look for what someone said they had shared. */}
      <div
        className="mb-4 inline-flex overflow-hidden rounded-di-md border border-line"
        role="tablist"
        aria-label="Collection ownership"
      >
        {(
          [
            ["mine", "My collections", mine.length],
            ["shared", "Shared with me", shared.length],
          ] as const
        ).map(([key, label, count]) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={tab === key}
            onClick={() => setTab(key)}
            className={
              tab === key
                ? "cursor-pointer bg-accent-soft px-3.5 py-2 text-[13px] font-bold text-accent-softtext"
                : "cursor-pointer bg-surface px-3.5 py-2 text-[13px] font-semibold text-ink-2 hover:bg-sunken"
            }
          >
            {label}
            {!isLoading && (
              <span className="tnum ml-1.5 font-semibold opacity-60">
                {count}
              </span>
            )}
          </button>
        ))}
      </div>

      {isLoading ? (
        // The board is a grid of fixed-height cards, so it loads as an outline
        // of that grid: a spinner would collapse the page and then shove
        // everything down when the collections land. The create tile is real
        // even here — it needs no data, and on a new account it is the only
        // thing to do.
        <CollectionGridSkeleton leading={tab === "mine" ? createTile : null} />
      ) : (
        <div className={COLLECTION_GRID}>
          {tab === "mine" && createTile}

          {shown.map((collection) => (
            <CollectionCard
              key={collection.id}
              collection={collection}
              onDelete={collection.shared ? undefined : setPendingDelete}
              onCopyId={collection.shared ? copyId : undefined}
            />
          ))}
        </div>
      )}

      {!isLoading && tab === "mine" && mine.length === 0 && (
        <p className="mt-4 text-[13px] text-ink-2">
          No collections yet. Create one above — add a handful of documents and
          ask your first question.
        </p>
      )}

      {!isLoading && tab === "shared" && shared.length === 0 && (
        <p className="mt-4 text-[13px] text-ink-2">
          Nothing shared with you yet. When someone invites you to their
          collection from its Share screen, it appears here — open it and ask
          questions, without it counting as one of yours.
        </p>
      )}

      <Modal
        open={Boolean(pendingDelete)}
        onClose={() => {
          setPendingDelete(null);
          setDeleteError(null);
        }}
        title={
          pendingDelete ? `Delete ${pendingDelete.name}?` : "Delete collection?"
        }
        width="520px"
        footer={
          <>
            <Btn
              onClick={() => {
                setPendingDelete(null);
                setDeleteError(null);
              }}
            >
              Cancel
            </Btn>
            <Btn
              variant="dangerGhost"
              onClick={() => void confirmDelete()}
              disabled={remove.isPending}
            >
              {remove.isPending ? "Deleting…" : "Delete collection"}
            </Btn>
          </>
        }
      >
        <p className="m-0 text-[13.5px] text-ink-2">
          Its documents, extracted fields, conversations and share links go with
          it. This cannot be undone.
        </p>
        {deleteError ? (
          <p className="mt-3 rounded-di-md border border-danger-border bg-danger-bg p-3 text-[13px] text-danger-text">
            {deleteError}
          </p>
        ) : null}
      </Modal>
    </div>
  );
}

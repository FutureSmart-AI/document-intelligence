"use client";

import { useRouter } from "next/navigation";

import Icon from "@/components/Icon";
import { Card, EmptyNote, PageHead } from "@/components/ui";
import { useCollections } from "@/data/collections";
import { collectionRoute, type Route } from "@/lib/routes";

/**
 * Guards the collection-scoped views.
 *
 * Reached without `?collection=` — from a bookmark, or from a stale link — these
 * views would otherwise show the first collection in the account, which reads as
 * a collection being open when none is. This asks which one you mean instead.
 */
export default function CollectionScope({
  collectionId,
  route,
  purpose,
  children,
}: {
  collectionId?: string;
  /** The route to come back to once a collection has been picked. */
  route: Route;
  /** What this view needs a collection for, shown above the picker. */
  purpose: string;
  children: React.ReactNode;
}) {
  const router = useRouter();
  const { collections, isLoading } = useCollections();

  if (collectionId) return <>{children}</>;

  return (
    <>
      <PageHead title="Pick a collection" sub={purpose} />

      {isLoading && !collections.length ? (
        <EmptyNote>Loading your collections…</EmptyNote>
      ) : collections.length ? (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(240px,1fr))] gap-3.5">
          {collections.map((collection) => (
            <Card key={collection.id}>
              <button
                type="button"
                onClick={() =>
                  router.push(collectionRoute(route, collection.id))
                }
                className="flex w-full cursor-pointer flex-col gap-2 p-4 text-left"
              >
                <span className="flex size-[38px] items-center justify-center rounded-di-md bg-accent-soft text-accent">
                  <Icon name="file" className="size-[18px]" />
                </span>
                <span className="text-[15px] font-bold text-ink">
                  {collection.name}
                </span>
                <span className="text-[12px] text-ink-2">
                  Open this collection
                </span>
              </button>
            </Card>
          ))}
        </div>
      ) : (
        <EmptyNote>
          No collections yet. Create one from Home — a name, what it&rsquo;s
          about, and your documents.
        </EmptyNote>
      )}
    </>
  );
}

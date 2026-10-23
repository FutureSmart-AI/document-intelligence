"use client";

import { useEffect, useState } from "react";

import {
  toAppearance,
  useWidgetSettings,
  type WidgetAppearance,
} from "@/data/widgetSettings";

/**
 * The appearance being edited, held above the rows that read it.
 *
 * The editor and the preview are two separate disclosure rows, and a collapsed
 * row is unmounted — so a draft owned by the editor would be thrown away every
 * time that row was closed, and the preview would have nothing to draw. The
 * draft lives here instead, on the screen both rows sit inside.
 */
export function useAppearanceDraft(collectionId: string) {
  const { settings, icons, avatars, save, reset } =
    useWidgetSettings(collectionId);
  const [draft, setDraft] = useState<WidgetAppearance>(() =>
    toAppearance(undefined),
  );
  const [loaded, setLoaded] = useState(false);

  // Hydrate once the stored settings land, and not again: re-syncing on every
  // refetch would throw away edits in progress the moment a poll returned.
  useEffect(() => {
    if (loaded || settings.isLoading) return;
    setDraft(toAppearance(settings.data));
    setLoaded(true);
  }, [loaded, settings.isLoading, settings.data]);

  return {
    draft,
    patch: (next: Partial<WidgetAppearance>) =>
      setDraft((current) => ({ ...current, ...next })),
    /** Takes the next stored settings after a reset, rather than keeping the draft. */
    rehydrate: () => setLoaded(false),
    icons,
    avatars,
    save,
    reset,
  };
}

export type AppearanceDraft = ReturnType<typeof useAppearanceDraft>;

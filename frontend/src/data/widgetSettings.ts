"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { buildApiUrl } from "@/platform/utils";
import axios from "@/platform/utils/axios";

import { APIS } from "./api";

/**
 * How a shared collection looks — the heading, greeting, brand colour, launcher
 * icon and chat avatar of the bubble and the full-page link.
 *
 * `GET …/widget-config` answers the stored settings, `POST` writes them and
 * `DELETE` puts them back to the service defaults. Two things about this route
 * are load-bearing:
 *
 * 1. **It takes form fields, not JSON.** An icon can be an uploaded file, so
 *    the whole body is multipart. This app's axios instance defaults every
 *    request to `application/json`, and axios serialises a `FormData` body to a
 *    JSON string when that is the declared type — on the NL2SQL equivalent that
 *    answered `200 {"message": "Added widget settings successfully"}` and stored
 *    nothing, so every save reported success while the appearance stayed at the
 *    defaults. The content type is therefore named explicitly below.
 * 2. **A picked file wins over a chosen preset's URL.** The service applies
 *    that precedence itself, so sending both fields would be ambiguous; only
 *    one of the pair is ever sent.
 *
 * The stored body is read defensively: it comes back either bare or wrapped in
 * `widget_settings`, and a collection that has never been shared has no
 * settings at all.
 */

export interface WidgetAppearance {
  heading: string;
  sub: string;
  greet: string;
  /** Hex, as the service stores it. */
  color: string;
  show_sources: boolean;
  /** A default's URL, from `WIDGET_ICONS` / `WIDGET_AVATARS`. */
  widget_icon?: string;
  widget_avatar?: string;
  /** A picked file, set only while it is still unsaved. */
  widget_icon_file?: File;
  widget_avatar_file?: File;
}

/** The largest icon or avatar the service accepts — more is a 400. */
export const MAX_WIDGET_ASSET_BYTES = 2 * 1024 * 1024;

/** What the product shows a collection that has never been shared. */
export const DEFAULT_APPEARANCE: WidgetAppearance = {
  heading: "Ask these documents",
  sub: "Answers come from this collection, with their sources.",
  greet: "Hello! Ask me anything about these documents.",
  color: "#2563eb",
  show_sources: true,
};

interface WidgetSettingsWire {
  heading?: string;
  sub_heading?: string;
  greet_message?: string;
  color?: string;
  show_sources?: boolean;
  widget_icon?: string;
  widget_avatar?: string;
}

const asRecord = (value: unknown): Record<string, unknown> =>
  value && typeof value === "object" ? (value as Record<string, unknown>) : {};

/** The stored settings, wrapped or bare, as one shape. */
function readSettings(body: unknown): WidgetSettingsWire {
  const record = asRecord(body);
  const inner = record.widget_settings ?? record.widget_config ?? record;
  return asRecord(inner) as WidgetSettingsWire;
}

/** Stored settings merged over the defaults, so the form is never blank. */
export function toAppearance(stored: WidgetSettingsWire | undefined) {
  return {
    ...DEFAULT_APPEARANCE,
    ...(stored?.heading ? { heading: stored.heading } : {}),
    ...(stored?.sub_heading ? { sub: stored.sub_heading } : {}),
    ...(stored?.greet_message ? { greet: stored.greet_message } : {}),
    ...(stored?.color ? { color: stored.color } : {}),
    ...(typeof stored?.show_sources === "boolean"
      ? { show_sources: stored.show_sources }
      : {}),
    ...(stored?.widget_icon ? { widget_icon: stored.widget_icon } : {}),
    ...(stored?.widget_avatar ? { widget_avatar: stored.widget_avatar } : {}),
  } satisfies WidgetAppearance;
}

/** `{ url }[]` from either default-asset route, whatever it names the array. */
function readAssets(body: unknown): string[] {
  const record = asRecord(body);
  const list =
    record.widget_icons ??
    record.widget_avatars ??
    record.icons ??
    record.avatars ??
    record.default_icons ??
    record.default_avatars ??
    body;
  if (!Array.isArray(list)) return [];
  return list
    .map((entry) =>
      typeof entry === "string" ? entry : String(asRecord(entry).url ?? ""),
    )
    .filter(Boolean);
}

export function useWidgetSettings(collectionId: string | undefined) {
  const client = useQueryClient();
  const url = () =>
    buildApiUrl(APIS.WIDGET_CONFIG, { knowledge_id: collectionId as string });

  const settings = useQuery({
    queryKey: ["widget-config", collectionId],
    enabled: Boolean(collectionId),
    queryFn: async (): Promise<WidgetSettingsWire> =>
      readSettings((await axios.get(url())).data),
  });

  const icons = useQuery({
    queryKey: ["widget-icons"],
    staleTime: 60 * 60 * 1000,
    queryFn: async () => readAssets((await axios.get(APIS.WIDGET_ICONS)).data),
  });

  const avatars = useQuery({
    queryKey: ["widget-avatars"],
    staleTime: 60 * 60 * 1000,
    queryFn: async () =>
      readAssets((await axios.get(APIS.WIDGET_AVATARS)).data),
  });

  const save = useMutation({
    mutationFn: async (appearance: WidgetAppearance) => {
      const form = new FormData();
      form.append("heading", appearance.heading);
      form.append("sub_heading", appearance.sub);
      form.append("greet_message", appearance.greet);
      form.append("color", appearance.color);
      form.append("show_sources", String(appearance.show_sources));
      if (appearance.widget_icon_file)
        form.append("widget_icon", appearance.widget_icon_file);
      else if (appearance.widget_icon)
        form.append("widget_icon_url", appearance.widget_icon);
      if (appearance.widget_avatar_file)
        form.append("widget_avatar", appearance.widget_avatar_file);
      else if (appearance.widget_avatar)
        form.append("widget_avatar_url", appearance.widget_avatar);
      // Not a formality — see the note at the top of this file.
      await axios.post(url(), form, {
        headers: { "Content-Type": "multipart/form-data" },
      });
    },
    onSuccess: () =>
      client.invalidateQueries({ queryKey: ["widget-config", collectionId] }),
  });

  const reset = useMutation({
    mutationFn: async () => axios.delete(url()),
    onSuccess: () =>
      client.invalidateQueries({ queryKey: ["widget-config", collectionId] }),
  });

  return { settings, icons, avatars, save, reset };
}

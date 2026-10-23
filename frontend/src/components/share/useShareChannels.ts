"use client";

import { useState } from "react";

import {
  useCollectionSettings,
  useSaveCollectionSettings,
  type CollectionSettings,
} from "@/data/chatConfig";
import { apiMessage } from "@/lib/apiError";
import { useToast } from "@/platform/components/ToastContext";

/**
 * The two hosted surfaces, switched on and off for real.
 *
 * Both flags live on `GET/PUT …/chat/config` — `is_public` for the website
 * bubble, `fullscreen_is_public` for the full-page link — each with its own
 * allowlist of hostnames that may load it. The service is explicit that the two
 * are independent, so they are two channels here rather than one shared switch,
 * and the PUT merges what it is given, so a switch sends only its own field.
 *
 * A write is applied to the screen first and rolled back only if the service
 * refuses it: a switch that waits for a round trip reads as broken, and a switch
 * that keeps a state the service rejected is a lie.
 */

export type ChannelKey = "widget" | "fullpage";

const FIELDS = {
  widget: { on: "is_public", domains: "allowed_domains" },
  fullpage: {
    on: "fullscreen_is_public",
    domains: "fullscreen_allowed_domains",
  },
} as const satisfies Record<
  ChannelKey,
  { on: keyof CollectionSettings; domains: keyof CollectionSettings }
>;

const NAMES: Record<ChannelKey, string> = {
  widget: "Website bubble",
  fullpage: "Full-page link",
};

export interface ShareChannel {
  key: ChannelKey;
  on: boolean;
  /** Empty means any origin may load it, which is what the service does. */
  domains: string[];
  busy: boolean;
  setOn: (next: boolean) => Promise<void>;
  addDomain: (domain: string) => Promise<void>;
  removeDomain: (domain: string) => Promise<void>;
}

export function useShareChannels(collectionId: string) {
  const { toast } = useToast();
  const settings = useCollectionSettings(collectionId);
  const save = useSaveCollectionSettings(collectionId);
  /**
   * What has been written since the screen loaded. Kept rather than cleared on
   * success: the refetch it triggers lands a moment later, and dropping the
   * value in between would flick the switch back to where it was.
   */
  const [written, setWritten] = useState<CollectionSettings>({});
  const value = { ...(settings.data ?? {}), ...written };

  const write = async (patch: CollectionSettings, done: string) => {
    setWritten((current) => ({ ...current, ...patch }));
    try {
      await save.mutateAsync(patch);
      toast.success(done);
    } catch (error) {
      // Back to whatever the service still holds.
      setWritten((current) => {
        const next = { ...current };
        for (const field of Object.keys(patch))
          delete next[field as keyof CollectionSettings];
        return next;
      });
      toast.error(apiMessage(error, "That change could not be saved."));
    }
  };

  const channel = (key: ChannelKey): ShareChannel => {
    const fields = FIELDS[key];
    const on = Boolean(value[fields.on]);
    const domains = (value[fields.domains] as string[] | undefined) ?? [];
    const writeDomains = (next: string[], done: string) =>
      write({ [fields.domains]: next }, done);

    return {
      key,
      on,
      domains,
      busy: settings.isLoading || save.isPending,
      setOn: (next) =>
        write(
          { [fields.on]: next },
          `${NAMES[key]} ${next ? "switched on" : "switched off"}.`,
        ),
      addDomain: (domain) => {
        // Bare lowercase hostnames are what the service stores; a pasted URL is
        // the obvious mistake, so the host is taken out of it rather than
        // refused.
        const clean = domain
          .trim()
          .toLowerCase()
          .replace(/^[a-z]+:\/\//, "")
          .replace(/\/.*$/, "");
        if (!clean || domains.includes(clean)) return Promise.resolve();
        return writeDomains([...domains, clean], `${clean} may load it.`);
      },
      removeDomain: (domain) =>
        writeDomains(
          domains.filter((value) => value !== domain),
          `${domain} may no longer load it.`,
        ),
    };
  };

  return { isLoading: settings.isLoading, channel };
}

"use client";

import { useState, type ReactNode } from "react";

import Icon from "@/components/Icon";
import { Btn, FieldHelp, FieldLabel, IconBtn, Input } from "@/components/ui";

import type { ShareChannel as Channel } from "./useShareChannels";

/**
 * One hosted surface's body: who may load it, and what you would paste.
 *
 * The allowlist is the part that is fully real — the hostnames are stored on the
 * chat config and the service enforces them once the channel is switched on.
 * What is still missing is an address to hand out: the embed script this repo
 * ships reads `data-agent-id` and the full-page route is keyed by a chat-registry
 * agent, so neither can name a collection yet. That is said here rather than
 * drawn as a snippet that would not load.
 */
export default function ShareChannel({
  channel,
  blurb,
  missing,
}: {
  channel: Channel;
  blurb: string;
  /** What this surface still needs before there is an address to give out. */
  missing: ReactNode;
}) {
  const [draft, setDraft] = useState("");

  const add = async () => {
    await channel.addDomain(draft);
    setDraft("");
  };

  return (
    <>
      <p className="text-[13px] text-ink-2">{blurb}</p>

      <div>
        <FieldLabel>Allowed domains</FieldLabel>
        <div className="flex flex-wrap items-center gap-2">
          <Input
            aria-label="Domain allowed to load this"
            placeholder="e.g. acme.com"
            className="min-w-[160px] flex-1"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") add();
            }}
          />
          <Btn size="sm" disabled={!draft.trim() || channel.busy} onClick={add}>
            Add
          </Btn>
        </div>
        <FieldHelp>
          Leave it empty to allow anywhere. <code>acme.com</code> for the apex,{" "}
          <code>*.acme.com</code> for subdomains. This list is this
          channel&apos;s own — the other one keeps its own.
        </FieldHelp>

        <div className="mt-2 flex flex-wrap gap-2">
          {channel.domains.length ? (
            channel.domains.map((domain) => (
              <span
                key={domain}
                className="inline-flex items-center gap-1 rounded-full border border-line bg-sunken py-0.5 pr-0.5 pl-2.5 text-[12px] text-ink"
              >
                {domain}
                <IconBtn
                  label={`Remove ${domain}`}
                  disabled={channel.busy}
                  className="size-5 border-0 bg-transparent"
                  onClick={() => channel.removeDomain(domain)}
                >
                  <Icon name="x" className="size-3" />
                </IconBtn>
              </span>
            ))
          ) : (
            <span className="text-[12px] text-ink-3">
              {channel.on
                ? "All domains allowed — it can be loaded on any website."
                : "No list yet. Switch this on to let anyone load it."}
            </span>
          )}
        </div>
      </div>

      <p className="rounded-di-md border border-line bg-sunken px-3 py-2.5 text-[12.5px] text-ink-2">
        {missing}
      </p>
    </>
  );
}

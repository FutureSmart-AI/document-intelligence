"use client";

import { useState } from "react";

import { Btn, PageHead, Switch } from "@/components/ui";
import { usePeople } from "@/data/people";
import { useOwnerId } from "@/data/session";
import { apiMessage } from "@/lib/apiError";
import { useToast } from "@/platform/components/ToastContext";
import { useAuth } from "@/platform/hooks/useAuth";

import { SHARE_ROWS, type ShareRowKey } from "./constants";
import ShareActivity from "./ShareActivity";
import ShareApi from "./ShareApi";
import ShareAppearance from "./ShareAppearance";
import ShareChannel from "./ShareChannel";
import SharePeople from "./SharePeople";
import SharePreview from "./SharePreview";
import ShareRow, { ShareGroup } from "./ShareRow";
import { useAppearanceDraft } from "./useAppearanceDraft";
import { useShareChannels } from "./useShareChannels";

/**
 * Share — letting people ask this collection questions without handing them the
 * documents.
 *
 * Two tabs, as the artifact draws them: **Sharing**, which is the ways out and
 * who may use them, and **Activity**, which is what came of it. They belong on
 * one screen because the reader who has just handed out a key is the one who
 * wants to know what has been asked with it.
 *
 * Sharing carries the three groups the artifact numbers: the ways to share, who
 * can get in, and how it looks. Each feature is ONE disclosure row rather than a
 * card standing permanently open — open, the people list and the appearance
 * editor pushed everything below them off the screen, so you only found out you
 * could invite somebody by scrolling. Every row states its name, its purpose and
 * its state on one line, and all six fit a viewport with nothing expanded.
 *
 * The artifact's two enable switches and its domain allowlists are real: they
 * are `is_public` / `fullscreen_is_public` and their two hostname lists on
 * `…/chat/config`, independent of each other, so each channel row carries its
 * own switch and its own list. What is *not* here is the link expiry and the
 * passcode — the knowledge service stores neither, and both are controls whose
 * promise this app could not keep.
 *
 * Switched on, a channel is on at the service; what it still lacks is an address
 * to hand out, because the embed script reads `data-agent-id` and the full-page
 * route is keyed by a chat-registry agent, so neither can name a collection.
 * Each row says so rather than showing a snippet that would not load.
 */
export default function ShareView() {
  // A collection *is* the knowledge base, so the bridge's `agentId` is the id
  // every route on this screen is keyed by.
  const { agentId: collectionId } = useAuth();
  const { toast } = useToast();
  const owner = useOwnerId();
  const [tab, setTab] = useState<"sharing" | "activity">("sharing");
  /**
   * Which rows are open, held here rather than in each row: the header's
   * expand-all has to be able to move all of them at once, and a row that is
   * closed renders nothing at all.
   */
  const [openRows, setOpenRows] = useState<ShareRowKey[]>([]);
  const [previewMode, setPreviewMode] = useState<"bubble" | "full">("bubble");

  const appearance = useAppearanceDraft(collectionId ?? "");
  const channels = useShareChannels(collectionId ?? "");
  // The header says how many people can already get in, so that row does not
  // have to be opened to answer the question it is there for.
  const people = usePeople(collectionId, owner);

  const isOpen = (row: ShareRowKey) => openRows.includes(row);
  const toggle = (row: ShareRowKey) => (open: boolean) =>
    setOpenRows((current) =>
      open
        ? current.includes(row)
          ? current
          : [...current, row]
        : current.filter((key) => key !== row),
    );
  const allOpen = openRows.length === SHARE_ROWS.length;

  if (!collectionId) return null;

  const invited = (people.data ?? []).filter(
    (person) => person.role !== "owner",
  ).length;

  return (
    <>
      <PageHead
        title="Share"
        sub="Let people ask this collection questions without giving them the documents — from your own code, or by inviting them by email."
      />

      <div
        className="mb-4 inline-flex overflow-hidden rounded-di-md border border-line"
        role="tablist"
        aria-label="Share sections"
      >
        {(
          [
            ["sharing", "Sharing"],
            ["activity", "Activity"],
          ] as const
        ).map(([key, label]) => (
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
          </button>
        ))}
      </div>

      {tab === "activity" ? (
        <ShareActivity collectionId={collectionId} />
      ) : (
        <>
          <div className="mb-3 flex flex-wrap items-center gap-2 px-0.5">
            <p className="text-[12.5px] text-ink-2">
              {SHARE_ROWS.length} features, each one row.{" "}
              <span className="font-bold text-ink">
                {openRows.length === 0
                  ? "Nothing open"
                  : `${openRows.length} open`}
              </span>
            </p>
            <Btn
              size="sm"
              variant="ghost"
              className="ml-auto"
              onClick={() => setOpenRows(allOpen ? [] : [...SHARE_ROWS])}
            >
              {allOpen ? "Collapse all" : "Expand all"}
            </Btn>
          </div>

          <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_392px]">
            <div className="grid gap-5">
              <ShareGroup
                step={1}
                title="Ways to share"
                purpose="How people reach this collection. Switch on the ones you want — each is set up separately, and the two hosted surfaces take their look from the panel on the right."
              >
                <ShareRow
                  accent
                  id="share-api"
                  icon="code"
                  title="API"
                  purpose="Ask questions from your own code, with a key you create here"
                  state={{ label: "Live", live: true }}
                  open={isOpen("api")}
                  onToggle={toggle("api")}
                >
                  <ShareApi collectionId={collectionId} />
                </ShareRow>

                {[
                  {
                    key: "widget" as const,
                    id: "share-widget",
                    icon: "chat" as const,
                    title: "Chat bubble on your website",
                    purpose:
                      "One script tag, a bubble in the corner of your own pages",
                    label: "Enable the website bubble",
                    blurb:
                      "A chat bubble you drop into any page. No API key — who may load it is the domain list below.",
                    missing:
                      "The embed script this app ships reads a chat agent id rather than a collection id, so there is no snippet to paste yet. The switch above is real: it is what makes this collection answer a public caller.",
                  },
                  {
                    key: "fullpage" as const,
                    id: "share-fullpage",
                    icon: "expand" as const,
                    title: "Full-page chat link",
                    purpose:
                      "A link that opens straight into an ask screen — no login",
                    label: "Enable the full-page link",
                    blurb:
                      "Opens straight into a full-page ask screen — no login, and nobody has to expand a bubble first.",
                    missing:
                      "The full-page route is keyed by a chat-registry agent, so there is no collection address to copy yet. The switch above is real: it is what makes this collection answer a public caller.",
                  },
                ].map((row) => {
                  const channel = channels.channel(row.key);
                  return (
                    <ShareRow
                      accent
                      key={row.key}
                      id={row.id}
                      icon={row.icon}
                      title={row.title}
                      purpose={row.purpose}
                      state={{
                        label: channel.on ? "On" : "Off",
                        live: channel.on,
                      }}
                      open={isOpen(row.key)}
                      onToggle={toggle(row.key)}
                      control={
                        <Switch
                          label={row.label}
                          checked={channel.on}
                          disabled={channels.isLoading}
                          onChange={(next) => channel.setOn(next)}
                        />
                      }
                    >
                      <ShareChannel
                        channel={channel}
                        blurb={row.blurb}
                        missing={row.missing}
                      />
                    </ShareRow>
                  );
                })}
              </ShareGroup>

              <ShareGroup
                step={2}
                title="Who can get in"
                purpose="People you invite by email, and what each of them is allowed to do here. On this service an invitation is the grant — a listed address already has access."
              >
                <ShareRow
                  id="share-people"
                  icon="users"
                  title="People"
                  purpose={
                    people.isLoading
                      ? "Who you invited, and what each of them can do"
                      : `Who you invited, and what each of them can do · ${invited} invited`
                  }
                  open={isOpen("people")}
                  onToggle={toggle("people")}
                >
                  <SharePeople collectionId={collectionId} />
                </ShareRow>
              </ShareGroup>
            </div>

            <div className="grid gap-5 xl:sticky xl:top-4">
              <ShareGroup
                step={3}
                title="How it looks"
                purpose="Your wording and your colour, with a preview of exactly what the people you share with will see."
              >
                <ShareRow
                  id="share-appearance"
                  icon="spark"
                  title="Appearance"
                  purpose="Heading, greeting, brand colour and launcher icon"
                  open={isOpen("appearance")}
                  onToggle={toggle("appearance")}
                  control={
                    <Btn
                      size="sm"
                      disabled={appearance.reset.isPending}
                      onClick={async () => {
                        try {
                          await appearance.reset.mutateAsync();
                          appearance.rehydrate();
                          toast.success("Appearance put back to the defaults.");
                        } catch (error) {
                          toast.error(
                            apiMessage(
                              error,
                              "The appearance could not be reset.",
                            ),
                          );
                        }
                      }}
                    >
                      Reset
                    </Btn>
                  }
                >
                  <ShareAppearance appearance={appearance} />
                </ShareRow>

                <ShareRow
                  id="share-preview"
                  icon="chat"
                  title="Live preview"
                  purpose="What the people you share with see"
                  open={isOpen("preview")}
                  onToggle={toggle("preview")}
                  control={
                    <div
                      className="inline-flex overflow-hidden rounded-di-md border border-line"
                      role="tablist"
                      aria-label="Preview mode"
                    >
                      {(["bubble", "full"] as const).map((option) => (
                        <button
                          key={option}
                          type="button"
                          role="tab"
                          aria-selected={previewMode === option}
                          onClick={() => setPreviewMode(option)}
                          className={
                            previewMode === option
                              ? "cursor-pointer bg-accent-soft px-2.5 py-1 text-[11.5px] font-bold text-accent-softtext"
                              : "cursor-pointer bg-surface px-2.5 py-1 text-[11.5px] font-semibold text-ink-2 hover:bg-sunken"
                          }
                        >
                          {option === "bubble" ? "Bubble" : "Full page"}
                        </button>
                      ))}
                    </div>
                  }
                >
                  <SharePreview
                    appearance={appearance.draft}
                    mode={previewMode}
                  />
                </ShareRow>
              </ShareGroup>
            </div>
          </div>
        </>
      )}
    </>
  );
}

"use client";

import { useState } from "react";
import Link from "next/link";

import Icon from "@/components/Icon";
import {
  Btn,
  Card,
  EmptyNote,
  IconBtn,
  Modal,
  PageHead,
  Pill,
  SectionLabel,
} from "@/components/ui";
import {
  nextSort,
  SortableTh,
  sortRows,
  TableShell,
  type SortState,
} from "@/components/usage/UsageBits";
import {
  allProviders,
  providerGroups,
  useCredentialProviders,
  useCredentials,
  useDeleteCredential,
} from "@/data/credentials";
import { apiMessage } from "@/lib/apiError";
import { platformCredentialsLabel } from "@/lib/platformCopy";
import { ROUTES } from "@/lib/routes";
import { useToast } from "@/platform/components/ToastContext";

import { EYE, EYE_OFF, PENCIL, TRASH } from "./constants";
import CredentialFormModal from "./CredentialFormModal";

/**
 * Add Credentials — the account's own provider keys.
 *
 * A screen rather than a dialog, as the design artifact has it: the keys, what
 * each one is for, and — the part that is the point — a grid saying which key
 * every service is actually running on. A stored key is otherwise invisible,
 * and "whose key answered that" is the question someone opens this to settle.
 *
 * Keys are stored per **account**: the service's own words are that a stored
 * key "applies to every knowledge base the owner has", so nothing here takes a
 * collection and the screen never asks you to pick one.
 *
 * Deleting asks first. The old dialog removed a key on one click, and a key
 * cannot be read back to retype it — losing one to a mis-click means going to
 * the provider for a new one.
 */

type CredentialKey = "provider" | "mask" | "updated";

/** `Aug 12, 2026` — the artifact's date, in the reader's own locale rules. */
function stampedOn(timestamp: string): string {
  const at = new Date(timestamp);
  return Number.isNaN(at.getTime())
    ? timestamp.slice(0, 10)
    : at.toLocaleDateString("en-US", {
        month: "short",
        day: "numeric",
        year: "numeric",
      });
}

/* The glyphs the artifact's board uses that the product's icon set does not
   carry — reveal, hide and edit — traced from the artifact itself so the board
   is the same drawing, not an approximation of it. */

export default function CredentialsView() {
  const { toast } = useToast();
  const { data: credentials = [], isLoading } = useCredentials();
  const { data: providerOptions } = useCredentialProviders();
  const remove = useDeleteCredential();

  const [form, setForm] = useState<{ open: boolean; provider?: string }>({
    open: false,
  });
  const [deleting, setDeleting] = useState<string | null>(null);
  /**
   * Which rows have had their mask uncovered.
   *
   * The eye uncovers the **mask**, not the key: the service encrypts a key on
   * the way in and only ever answers with `sk-…4f2a`, so there is no plaintext
   * on this page to reveal. Dots by default all the same — the last four
   * characters are enough to tell two keys apart over someone's shoulder.
   */
  const [revealed, setRevealed] = useState<Record<string, boolean>>({});
  const [sort, setSort] = useState<SortState<CredentialKey> | null>(null);

  const groups = providerGroups(providerOptions);
  const providers = allProviders(providerOptions);
  const answerProviders = groups[0]?.providers ?? [];
  const credentialFor = (name: string) =>
    credentials.find(
      (credential) => credential.provider.toLowerCase() === name.toLowerCase(),
    );

  const rows = credentials.map((credential) => ({
    provider: credential.provider,
    id: credential.provider,
    mask: credential.mask,
    valid: credential.is_valid,
    group: answerProviders.some(
      (name) => name.toLowerCase() === credential.provider.toLowerCase(),
    )
      ? "Answer providers"
      : "Document & website reading",
    updated: credential.updated_at ?? "",
  }));
  const sorted = sortRows(rows, sort);

  const owned = new Set(
    credentials.map((credential) => credential.provider.toLowerCase()),
  );
  const answerKeys = answerProviders.filter((name) =>
    owned.has(name.toLowerCase()),
  ).length;

  const confirmDelete = async () => {
    if (!deleting) return;
    try {
      const response = await remove.mutateAsync(deleting);
      toast.success(
        response?.message ||
          `${deleting} credential removed — back on ${platformCredentialsLabel()}`,
      );
      setDeleting(null);
    } catch (error) {
      // Not surfaced anywhere else: this app's axios instance has no error-toast
      // interceptor, only 401 handling — so saying nothing here meant a failed
      // save looked like a silent no-op.
      toast.error(apiMessage(error, "That credential could not be saved."));
    }
  };

  return (
    <div>
      <Link
        href={ROUTES.home}
        className="mb-3 inline-flex items-center gap-1.5 text-[13px] font-semibold text-accent no-underline hover:underline"
      >
        <Icon name="caretLeft" className="size-3.5" />
        Back to all collections
      </Link>

      <PageHead
        title="Add Credentials"
        sub="Manage secure access keys for your AI providers and services."
        action={
          <Btn
            variant="primary"
            startIcon={<Icon name="plus" className="size-3.75" />}
            onClick={() => setForm({ open: true })}
          >
            Add New Credentials
          </Btn>
        }
      />

      <div className="mb-4 grid grid-cols-[repeat(auto-fit,minmax(210px,1fr))] gap-3">
        {[
          {
            label: "Active keys",
            value: credentials.length,
            foot: "Keys of your own in this workspace",
          },
          {
            label: "Answer providers",
            value: answerKeys,
            foot: "Providers that write your answers",
          },
          {
            label: "Tools configured",
            value: credentials.length - answerKeys,
            foot: "Document and website readers",
          },
        ].map((stat) => (
          <Card key={stat.label} className="flex flex-col gap-1 px-3.75 py-3">
            <span className="text-[11px] font-bold tracking-[0.05em] text-accent uppercase">
              {stat.label}
            </span>
            <span className="di-display text-[22px] leading-none font-extrabold text-ink tabular-nums">
              {isLoading ? "—" : stat.value}
            </span>
            <span className="text-[11.5px] text-ink-2">{stat.foot}</span>
          </Card>
        ))}
      </div>

      <div className="mb-4 flex items-start gap-2.5 rounded-di-md bg-accent-soft px-3 py-2.5 text-[12.5px] font-semibold text-accent-softtext">
        <Icon name="info" className="mt-px size-3.75 shrink-0" />
        <div>
          Anything you have not added a key for keeps running on{" "}
          {platformCredentialsLabel()}.
        </div>
      </div>

      <TableShell
        title="Configured Providers"
        note={`${credentials.length} provider${credentials.length === 1 ? "" : "s"} configured`}
      >
        {isLoading ? (
          <p className="m-0 py-10 text-center text-[13px] text-ink-2">
            Reading…
          </p>
        ) : !rows.length ? (
          <div className="flex flex-col items-center gap-1.5 px-4 py-12 text-center">
            <Icon name="key" className="size-8 text-ink-3" />
            <b className="di-display text-[14px] font-bold text-ink">
              No API keys configured yet
            </b>
            <span className="text-[12.5px] text-ink-2">
              Click &ldquo;Add New Credentials&rdquo; to get started.
            </span>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-[13px]">
              <thead>
                <tr className="border-b border-line bg-sunken text-ink-2">
                  <SortableTh
                    columnKey="provider"
                    label="Provider"
                    sort={sort}
                    onSort={(key) =>
                      setSort((current) => nextSort(current, key))
                    }
                  />
                  <SortableTh
                    columnKey="mask"
                    label="API key"
                    sort={sort}
                    onSort={(key) =>
                      setSort((current) => nextSort(current, key))
                    }
                  />
                  <SortableTh
                    columnKey="updated"
                    label="Updated"
                    sort={sort}
                    onSort={(key) =>
                      setSort((current) => nextSort(current, key))
                    }
                  />
                  <th className="p-3 text-right font-semibold">Actions</th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((row) => (
                  <tr
                    key={row.id}
                    className="border-b border-line-soft last:border-0"
                  >
                    <td className="p-3">
                      <b className="font-semibold text-ink">{row.provider}</b>
                      <div className="text-[11.5px] text-ink-2">
                        {row.group}
                      </div>
                    </td>
                    <td className="p-3">
                      <span className="flex items-center gap-2">
                        <code className="font-mono text-[12px] text-ink-2">
                          {revealed[row.id]
                            ? row.mask
                            : "•".repeat(Math.max(12, row.mask.length))}
                        </code>
                        <IconBtn
                          label={
                            revealed[row.id]
                              ? `Hide the ${row.provider} key`
                              : `Show the ${row.provider} key`
                          }
                          title={
                            revealed[row.id]
                              ? `Hide the stored ${row.provider} key`
                              : `Show what the service stored for ${row.provider} — the key itself is never sent back`
                          }
                          aria-pressed={Boolean(revealed[row.id])}
                          onClick={() =>
                            setRevealed((current) => ({
                              ...current,
                              [row.id]: !current[row.id],
                            }))
                          }
                        >
                          {revealed[row.id] ? EYE_OFF : EYE}
                        </IconBtn>
                      </span>
                    </td>
                    <td className="p-3 whitespace-nowrap text-ink-2">
                      {row.updated ? stampedOn(row.updated) : "—"}
                    </td>
                    <td className="p-3 text-right">
                      <div className="flex items-center justify-end gap-2">
                        {row.valid === false && (
                          <Pill tone="danger">Rejected</Pill>
                        )}
                        <IconBtn
                          label={`Edit the ${row.provider} credential`}
                          onClick={() =>
                            setForm({ open: true, provider: row.id })
                          }
                        >
                          {PENCIL}
                        </IconBtn>
                        <IconBtn
                          label={`Delete the ${row.provider} credential`}
                          className="text-danger-text hover:bg-danger-bg"
                          onClick={() => setDeleting(row.id)}
                        >
                          {TRASH}
                        </IconBtn>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </TableShell>

      <SectionLabel className="mb-2">
        Which key each service is using
      </SectionLabel>
      {/* The services listed are the catalogue's own answer, so when it has not
          been read there is nothing to list and the strip says that rather than
          drawing a row of providers this app decided on. */}
      {!providers.length ? (
        <EmptyNote>The service did not list any providers.</EmptyNote>
      ) : (
        <div className="flex gap-1.5 overflow-x-auto pb-1">
          {providers.map((name) => (
            <div
              key={name}
              className="flex min-w-[150px] flex-1 items-center gap-2 rounded-di-md border border-line-soft bg-sunken px-[9px] py-1.5"
            >
              <b className="text-[12.5px] font-semibold text-ink">{name}</b>
              <span className="flex-1" />
              {credentialFor(name) ? (
                <Pill tone="success">Your key</Pill>
              ) : (
                <Pill tone="neutral">Platform</Pill>
              )}
            </div>
          ))}
        </div>
      )}

      <CredentialFormModal
        open={form.open}
        editProvider={form.provider}
        onClose={() => setForm({ open: false })}
      />

      <Modal
        open={Boolean(deleting)}
        onClose={() => setDeleting(null)}
        title={
          deleting ? `Delete the ${deleting} credential?` : "Delete credential?"
        }
        width="520px"
        footer={
          <>
            <Btn onClick={() => setDeleting(null)}>Cancel</Btn>
            <Btn
              variant="dangerGhost"
              onClick={confirmDelete}
              disabled={remove.isPending}
            >
              {remove.isPending ? "Deleting…" : "Delete credential"}
            </Btn>
          </>
        }
      >
        <p className="m-0 text-[13.5px] text-ink-2">
          The {deleting ?? "provider"} key is removed from this workspace and
          that service goes back to running on {platformCredentialsLabel()}. A
          stored key cannot be read back, so adding it again means pasting it in
          full.
        </p>
      </Modal>
    </div>
  );
}

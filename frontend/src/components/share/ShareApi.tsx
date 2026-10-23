"use client";

import { useMemo, useState } from "react";

import Icon from "@/components/Icon";
import {
  Btn,
  Chip,
  EmptyNote,
  FieldHelp,
  IconBtn,
  Pill,
} from "@/components/ui";
import { useApiKeyMutations, useApiKeys } from "@/data/keys";
import { apiMessage } from "@/lib/apiError";
import { copyText } from "@/lib/clipboard";
import { publicHost } from "@/lib/routes";
import { useToast } from "@/platform/components/ToastContext";

import { API_LANGS, type ApiLang } from "./constants";

/**
 * The API channel: a key, and the three snippets that use it.
 *
 * The snippets are built from the route this product actually calls —
 * `POST /api/knowledge/v1/{knowledge_id}/chat` — with the same body the
 * Conversations screen sends, so a reader who pastes one gets the answers they
 * see in the app rather than a plausible-looking call that 422s.
 *
 * A created key is shown once. The list route returns only each key's prefix and
 * nothing on the service can produce the secret again, so it is revealed here at
 * the moment of creation, with that said plainly, instead of behind a reveal
 * control that could never work.
 */

const ago = (iso: string | null | undefined) => {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const days = Math.floor((Date.now() - then) / 86400000);
  if (days <= 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 30) return `${days} days ago`;
  return new Date(iso).toLocaleDateString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
};

export default function ShareApi({ collectionId }: { collectionId: string }) {
  const { toast } = useToast();
  const [lang, setLang] = useState<ApiLang>("py");
  const [created, setCreated] = useState<string | null>(null);
  const keys = useApiKeys();
  const mutations = useApiKeyMutations();

  // Only the keys that can read this collection: a key minted for another
  // collection cannot, and listing it here would invite pasting the wrong one.
  // A key with no `knowledge_id` reaches every collection the account owns, so
  // it does belong on this list.
  const usable = (keys.data ?? []).filter(
    (key) =>
      key.is_active && (!key.knowledge_id || key.knowledge_id === collectionId),
  );

  const snippets = useMemo(() => {
    const url = `${publicHost()}/api/knowledge/v1/${collectionId}/chat`;
    return {
      py: `import requests

r = requests.post(
    "${url}",
    headers={"x-api-key": "<YOUR_API_KEY>"},
    json={"query": "Which candidates can start immediately?",
          "stream": False},
    timeout=120,
)
d = r.json()
print(d["response"])

for source in d["data"]["knowledge"]["relevant_sources"]["data"]:
    print(source)`,
      js: `const r = await fetch(
  "${url}",
  { method: "POST",
    headers: { "Content-Type": "application/json",
               "x-api-key": "<YOUR_API_KEY>" },
    body: JSON.stringify({
      query: "Which candidates can start immediately?",
      stream: false }) },
);
const d = await r.json();
console.log(d.response);`,
      curl: `curl -X POST \\
  "${url}" \\
  -H "Content-Type: application/json" \\
  -H "x-api-key: <YOUR_API_KEY>" \\
  -d '{"query": "Which candidates can start immediately?",
       "stream": false}'`,
    } satisfies Record<ApiLang, string>;
  }, [collectionId]);

  const copy = async (text: string, what: string) => {
    const copied = await copyText(text);
    toast[copied ? "success" : "error"](
      copied
        ? `${what} copied.`
        : `The ${what.toLowerCase()} could not be copied.`,
    );
  };

  return (
    <div className="grid gap-3.5">
      <p className="text-[13px] text-ink-2">
        Ask this collection questions from your own code. A key can read answers
        and the documents behind them; it can never change settings.
      </p>

      <div
        className="inline-flex w-fit overflow-hidden rounded-di-md border border-line"
        role="tablist"
        aria-label="Snippet language"
      >
        {API_LANGS.map((option) => (
          <button
            key={option.key}
            type="button"
            role="tab"
            aria-selected={lang === option.key}
            onClick={() => setLang(option.key)}
            className={
              lang === option.key
                ? "cursor-pointer bg-accent-soft px-3 py-1.5 text-[12px] font-bold text-accent-softtext"
                : "cursor-pointer bg-surface px-3 py-1.5 text-[12px] font-semibold text-ink-2 hover:bg-sunken"
            }
          >
            {option.label}
          </button>
        ))}
      </div>

      <pre className="overflow-x-auto rounded-di-md border border-line bg-sunken p-3 font-mono text-[11.5px] leading-relaxed text-ink-2">
        {snippets[lang]}
      </pre>

      <div className="flex flex-wrap gap-2">
        <Btn
          size="sm"
          startIcon={<Icon name="copy" className="size-4" />}
          onClick={() => copy(snippets[lang], "Sample")}
        >
          Copy sample
        </Btn>
        <Btn
          size="sm"
          variant="primary"
          disabled={mutations.create.isPending}
          onClick={async () => {
            try {
              const key = await mutations.create.mutateAsync(collectionId);
              setCreated(key.api_key);
              toast.success("Key created — copy it now, it is shown once.");
            } catch (error) {
              toast.error(apiMessage(error, "The key could not be created."));
            }
          }}
        >
          Generate API key
        </Btn>
      </div>

      {created && (
        <div className="rounded-di-md border border-accent bg-accent-soft p-3">
          <div className="flex flex-wrap items-center gap-2">
            <Pill tone="accent">Shown once</Pill>
            <code className="font-mono text-[12px] break-all text-ink">
              {created}
            </code>
            <Btn
              size="sm"
              className="ml-auto"
              startIcon={<Icon name="copy" className="size-4" />}
              onClick={() => copy(created, "API key")}
            >
              Copy
            </Btn>
          </div>
          <FieldHelp>
            Store it somewhere safe. The service keeps only its prefix, so this
            value cannot be shown again — generate another key if it is lost.
          </FieldHelp>
        </div>
      )}

      {keys.isLoading ? (
        <EmptyNote>Loading keys…</EmptyNote>
      ) : usable.length ? (
        <div className="grid gap-2">
          {usable.map((key) => (
            <div
              key={key.key_prefix}
              className="flex flex-wrap items-center gap-2 rounded-di-md border border-line bg-sunken px-3 py-2"
            >
              <code className="font-mono text-[12px] text-ink">
                {key.key_prefix}…
              </code>
              {!key.knowledge_id && <Chip tone="muted">every collection</Chip>}
              <span className="text-[11.5px] text-ink-3">
                created {ago(key.created_at)}
              </span>
              <Btn
                size="sm"
                variant="dangerGhost"
                className="ml-auto"
                disabled={mutations.revoke.isPending}
                onClick={async () => {
                  try {
                    await mutations.revoke.mutateAsync(key.key_prefix);
                    toast.success("Key revoked.");
                  } catch (error) {
                    toast.error(
                      apiMessage(error, "The key could not be revoked."),
                    );
                  }
                }}
              >
                Revoke
              </Btn>
            </div>
          ))}
        </div>
      ) : (
        <EmptyNote>
          No keys yet. Generate one to let your own code ask this collection
          questions.
        </EmptyNote>
      )}

      <div className="flex items-start gap-2 text-[11.5px] text-ink-3">
        <IconBtn
          label="About keys"
          className="pointer-events-none border-0 bg-transparent"
        >
          <Icon name="info" className="size-4" />
        </IconBtn>
        <span>
          Keys belong to your account. Revoking one stops every caller using it,
          including any code you have already deployed.
        </span>
      </div>
    </div>
  );
}

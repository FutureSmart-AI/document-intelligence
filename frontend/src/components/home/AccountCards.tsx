"use client";

import { type ReactNode } from "react";
import Link from "next/link";

import Icon, { type IconName } from "@/components/Icon";
import { Chip, Pill } from "@/components/ui";
import { useCredentialProviders, useCredentials } from "@/data/credentials";
import { useUsageStatus } from "@/data/usage";
import { platformCredentialsLabel } from "@/lib/platformCopy";
import { ROUTES } from "@/lib/routes";
import { cn } from "@/platform/utils";

import { ACCOUNT_CARD_CLASS } from "./constants";

/**
 * The two account-level cards above the board: what has been spent, and the
 * keys it is spent through.
 *
 * Same pair, same place and same reading order as the NL2SQL board — a person
 * who runs both products should not have to look for them twice — drawn in this
 * product's tokens rather than that one's literal slate classes.
 *
 * Both link to a screen of their own. Neither is scoped to a collection — usage
 * is counted across the account and keys are stored against it — so neither
 * card carries one, exactly as the design artifact has them.
 */

const nfmt = new Intl.NumberFormat();

function CardBody({
  icon,
  tone,
  title,
  description,
  chip,
  children,
}: {
  icon: IconName;
  /** The icon plate's colour; the two cards are told apart by it. */
  tone: string;
  title: string;
  description: string;
  chip?: string;
  children?: ReactNode;
}) {
  return (
    <>
      <span
        className={cn(
          "flex size-12 shrink-0 items-center justify-center rounded-2xl",
          tone,
        )}
      >
        <Icon name={icon} className="size-6" />
      </span>
      <span className="flex w-full min-w-0 flex-col">
        <span className="flex w-full items-center justify-between gap-2">
          <b className="di-display text-[15px] font-bold text-ink transition-colors group-hover:text-accent">
            {title}
          </b>
          {chip && <Chip tone="muted">{chip}</Chip>}
        </span>
        <span className="mb-2 text-[13px] text-ink-2">{description}</span>
        {children}
      </span>
    </>
  );
}

export default function AccountCards() {
  const { data: status, isLoading } = useUsageStatus();
  const { data: credentials = [] } = useCredentials();
  const { data: providerOptions } = useCredentialProviders();

  const usage = status?.current_usage;
  const stats = [
    ["LLM tokens", usage?.llm_tokens_this_month ?? 0],
    ["Conversations", usage?.conversations_this_month ?? 0],
    ["Messages", usage?.messages_this_month ?? 0],
  ] as const;

  const providerCount =
    (providerOptions?.llm_providers.length ?? 0) +
    (providerOptions?.document_and_web_providers.length ?? 0);

  return (
    <>
      <div className="mb-6 grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Link href={ROUTES.usage} className={ACCOUNT_CARD_CLASS}>
          <CardBody
            icon="bolt"
            tone="bg-warn-bg text-warn-text"
            title="Usage & Limits"
            description="What your collections have used, against your account's limits"
            // Not "last 30 days": these counters are the service's own calendar
            // month, and a card that renamed the window would be describing a
            // number it did not compute.
            chip="This month"
          >
            <span className="mt-auto flex flex-row flex-wrap items-center gap-x-6 gap-y-1">
              {stats.map(([label, value]) => (
                <span key={label} className="flex items-center gap-2">
                  <span className="text-[10px] font-bold tracking-[0.06em] text-ink-3 uppercase">
                    {label}
                  </span>
                  <span className="text-[13px] font-bold text-ink-2">
                    {isLoading ? "—" : nfmt.format(value)}
                  </span>
                </span>
              ))}
            </span>
          </CardBody>
        </Link>

        <Link href={ROUTES.credentials} className={ACCOUNT_CARD_CLASS}>
          <CardBody
            icon="key"
            tone="bg-accent-soft text-accent"
            title="Add credentials"
            description="Use your own provider keys"
          >
            <span className="mt-auto">
              <Pill tone={credentials.length ? "success" : "neutral"}>
                {credentials.length
                  ? `${credentials.length} of ${providerCount || credentials.length} services on your own keys`
                  : `All services on ${platformCredentialsLabel()}`}
              </Pill>
            </span>
          </CardBody>
        </Link>
      </div>
    </>
  );
}

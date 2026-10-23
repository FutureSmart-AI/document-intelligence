#!/usr/bin/env bun
/**
 * Generate the knowledge service's route contract from its OpenAPI document.
 *
 * Document Intelligence talks to one service and nothing else, so "does this
 * route exist, with this verb and these query parameters" is a question a test
 * should be able to answer without a network call. This reads the published
 * spec and commits the answer;
 * `src/data/__tests__/apiContract.test.ts` checks every
 * endpoint the app can call against it.
 *
 * Usage:
 *   bun scripts/gen-knowledge-contract.ts
 *   bun scripts/gen-knowledge-contract.ts --check      # fail if the committed file is stale
 *   bun scripts/gen-knowledge-contract.ts --spec ./knowledge-openapi.json
 *
 * Env:
 *   KNOWLEDGE_OPENAPI_URL  override the spec URL (defaults to localhost:8000)
 */
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";

import prettier from "prettier";

import {
  endpointKey,
  paramContract,
  type EndpointContract,
} from "./openapiVocab";

const REPO_ROOT = join(dirname(new URL(import.meta.url).pathname), "..");
const OUT_FILE = join(REPO_ROOT, "src/generated/knowledgeContract.ts");
const DEFAULT_URL =
  process.env.KNOWLEDGE_OPENAPI_URL ?? "http://localhost:8000/openapi.json";

/** The service is mounted here; its spec paths are relative to it. */
const MOUNT = "/api/knowledge";

const METHODS = ["get", "post", "put", "patch", "delete"] as const;

interface SpecParameter {
  name?: string;
  in?: string;
  description?: string;
  schema?: unknown;
}
interface SpecOperation {
  parameters?: SpecParameter[];
}
interface Spec {
  info?: { version?: string };
  paths?: Record<string, Record<string, SpecOperation>>;
  components?: {
    schemas?: Record<
      string,
      { properties?: Record<string, { minLength?: number }> }
    >;
  };
}

/**
 * The validation rules the app has to enforce before it posts.
 *
 * Only what the service states about its own request bodies — a key shorter
 * than `minLength` is a 422 rather than a stored credential, and the form has
 * to say so before it sends. Read from the spec so the number is the service's,
 * not a copy of it that drifts.
 */
export interface KnowledgeConstraints {
  credentialApiKeyMinLength: number;
}

export function buildKnowledgeConstraints(spec: unknown): KnowledgeConstraints {
  const schemas = (spec as Spec)?.components?.schemas ?? {};
  const minLength =
    schemas.CredentialAddRequest?.properties?.api_key?.minLength;
  if (typeof minLength !== "number") {
    throw new Error(
      "CredentialAddRequest.api_key.minLength is missing from the spec",
    );
  }
  return { credentialApiKeyMinLength: minLength };
}

/** `{ "METHOD /api/knowledge/v1/…": ["query", "params"] }`, key-sorted. */
export type KnowledgeContract = Record<string, string[]>;

export function buildKnowledgeContract(spec: unknown): KnowledgeContract {
  const paths = (spec as Spec)?.paths ?? {};
  const contract: KnowledgeContract = {};
  for (const [path, operations] of Object.entries(paths)) {
    for (const method of METHODS) {
      const operation = operations?.[method];
      if (!operation) continue;
      const query = (operation.parameters ?? [])
        .filter((parameter) => parameter.in === "query" && parameter.name)
        .map((parameter) => parameter.name as string)
        .sort();
      contract[`${method.toUpperCase()} ${MOUNT}${path}`] = query;
    }
  }
  return Object.fromEntries(
    Object.keys(contract)
      .sort()
      .map((key) => [key, contract[key]]),
  );
}

/**
 * What each analytics filter may send, read from the service's own document.
 *
 * The Analytics screen renders the agent dashboard's page over the knowledge
 * service's `/health/*` routes, and that page asks its scope which values every
 * dropdown may offer. Those vocabularies are not written by hand next to the
 * controls: the same mistake on the NL2SQL screen — the dashboard's service
 * families offered as a `route` that service has never heard of — returned an
 * empty log for every choice, with no error to explain it.
 *
 * FastAPI publishes these enums as prose in `description` rather than as JSON
 * Schema `enum`, so `paramContract` parses them (and yields nothing for an
 * ordinary sentence). Extraction is shared with the NL2SQL generator so both
 * products read a spec the same way.
 */
export type KnowledgeHealthContract = Record<string, EndpointContract>;

export function buildKnowledgeHealthContract(
  spec: unknown,
): KnowledgeHealthContract {
  const paths = (spec as Spec)?.paths ?? {};
  const contract: KnowledgeHealthContract = {};
  for (const path of Object.keys(paths).sort()) {
    if (!path.includes("/health/")) continue;
    for (const operation of Object.values(paths[path] ?? {})) {
      const endpoint: EndpointContract = {};
      for (const parameter of operation.parameters ?? []) {
        // A path parameter is an address, not a vocabulary to choose from.
        if (parameter.in !== "query") continue;
        endpoint[String(parameter.name)] = paramContract(
          parameter as unknown as Record<string, unknown>,
        );
      }
      contract[endpointKey(path)] = endpoint;
    }
  }
  return contract;
}

async function loadSpec(local: string | undefined): Promise<unknown> {
  if (local) return JSON.parse(readFileSync(local, "utf8"));
  const res = await fetch(DEFAULT_URL);
  if (!res.ok) {
    throw new Error(`GET ${DEFAULT_URL} → ${res.status} ${res.statusText}`);
  }
  return res.json();
}

/**
 * Emitted key-sorted and `as const` so a rerun that changes nothing produces a
 * byte-identical file — which is what makes `--check` meaningful.
 */
async function render(
  contract: KnowledgeContract,
  health: KnowledgeHealthContract,
  constraints: KnowledgeConstraints,
  version: string,
): Promise<string> {
  const body = Object.entries(contract)
    .map(
      ([route, query]) =>
        `  ${JSON.stringify(route)}: ${JSON.stringify(query)},`,
    )
    .join("\n");
  const healthBody = Object.keys(health)
    .sort()
    .map((endpoint) => {
      const params = health[endpoint];
      const lines = Object.keys(params)
        .sort()
        .map((name) => {
          const p = params[name];
          const values = p.values
            ? `[${p.values.map((v) => JSON.stringify(v)).join(", ")}]`
            : "null";
          return (
            `    ${JSON.stringify(name)}: { ` +
            `default: ${JSON.stringify(p.default)}, ` +
            `min: ${JSON.stringify(p.min)}, ` +
            `max: ${JSON.stringify(p.max)}, ` +
            `values: ${values} },`
          );
        })
        .join("\n");
      return `  ${JSON.stringify(endpoint)}: {\n${lines}\n  },`;
    })
    .join("\n");

  const source = `/**
 * Every route the knowledge service publishes, with its query parameters.
 *
 * GENERATED by \`bun scripts/gen-knowledge-contract.ts\` from
 * ${DEFAULT_URL}
 * (spec version ${version}). Do not edit by hand — rerun the script.
 */
export const KNOWLEDGE_CONTRACT = {
${body}
} as const;

export type KnowledgeRoute = keyof typeof KNOWLEDGE_CONTRACT;

/**
 * The query parameters of the \`/health/*\` analytics routes: each one's
 * server-side default, its numeric bounds, and the closed set of values it
 * documents (\`null\` when it documents none).
 */
export const KNOWLEDGE_HEALTH_CONTRACT = {
${healthBody}
} as const;

export type KnowledgeHealthEndpoint = keyof typeof KNOWLEDGE_HEALTH_CONTRACT;

/** Request-body rules the service publishes, for the forms that must obey them. */
export const KNOWLEDGE_CONSTRAINTS = {
  credentialApiKeyMinLength: ${constraints.credentialApiKeyMinLength},
} as const;
`;
  return prettier.format(source, { parser: "typescript" });
}

const args = process.argv.slice(2);
const specIndex = args.indexOf("--spec");
const spec = await loadSpec(specIndex === -1 ? undefined : args[specIndex + 1]);
const contract = buildKnowledgeContract(spec);
const health = buildKnowledgeHealthContract(spec);
const constraints = buildKnowledgeConstraints(spec);
const version = (spec as Spec)?.info?.version ?? "unknown";
const rendered = await render(contract, health, constraints, version);

if (args.includes("--check")) {
  const current = readFileSync(OUT_FILE, "utf8");
  if (current !== rendered) {
    console.error(
      `${OUT_FILE} is stale — rerun \`bun scripts/gen-knowledge-contract.ts\`.`,
    );
    process.exit(1);
  }
  console.log(
    `${OUT_FILE} is up to date (${Object.keys(contract).length} routes).`,
  );
} else {
  writeFileSync(OUT_FILE, rendered);
  console.log(
    `Wrote ${OUT_FILE} — ${Object.keys(contract).length} routes from spec ${version}.`,
  );
}

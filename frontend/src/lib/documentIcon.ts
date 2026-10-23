import type { IconName } from "@/components/Icon";

/**
 * Which glyph a collection wears, from what it actually holds.
 *
 * A board of identical document marks says nothing about what is on it — the
 * design artifact's board never repeats one glyph across its cards, because the
 * mark is what makes a board scannable before a single name is read. So a
 * collection of resumes wears a person, one of contracts a signature, a crawled
 * site a globe, a folder of spreadsheets a table.
 *
 * Two signals, in order: what the collection is called, then what its documents
 * are. Both are already in hand — the name from the agent list, the documents
 * from the stats query — so no extra call is made to draw a card.
 */

/** The document as this product's data layer sees it. */
export interface DocumentLike {
  content_type?: string | null;
  file_name?: string | null;
  url?: string | null;
}

/**
 * Subjects worth their own mark, each with the words that name it.
 *
 * Matched as whole words against a lowercased string, longest list first is not
 * needed — the first entry that matches wins, so the order here is the priority
 * order: a "contract renewal report" is a contract, not a report.
 */
const SUBJECTS: Array<{ icon: IconName; words: string[] }> = [
  {
    icon: "docPerson",
    words: [
      "resume",
      "resumes",
      "cv",
      "cvs",
      "candidate",
      "candidates",
      "applicant",
      "applicants",
      "hiring",
      "recruit",
      "recruitment",
    ],
  },
  {
    icon: "docSigned",
    words: [
      "contract",
      "contracts",
      "agreement",
      "agreements",
      "nda",
      "ndas",
      "msa",
      "lease",
      "leases",
      "sow",
      "terms",
    ],
  },
  {
    icon: "invoice",
    words: [
      "invoice",
      "invoices",
      "receipt",
      "receipts",
      "bill",
      "bills",
      "billing",
      "purchase",
      "po",
      "expense",
      "expenses",
      "payment",
      "payments",
    ],
  },
  {
    icon: "ticket",
    words: [
      "ticket",
      "tickets",
      "support",
      "helpdesk",
      "issue",
      "issues",
      "incident",
      "incidents",
      "complaint",
      "complaints",
    ],
  },
  {
    icon: "docChart",
    words: [
      "report",
      "reports",
      "analysis",
      "analytics",
      "financial",
      "financials",
      "statement",
      "statements",
      "earnings",
      "audit",
      "audits",
    ],
  },
  {
    icon: "book",
    words: [
      "policy",
      "policies",
      "manual",
      "manuals",
      "handbook",
      "handbooks",
      "guide",
      "guides",
      "documentation",
      "docs",
      "sop",
      "sops",
      "knowledge",
    ],
  },
  {
    icon: "mail",
    words: [
      "email",
      "emails",
      "mail",
      "inbox",
      "thread",
      "threads",
      "correspondence",
    ],
  },
];

/** Extension groups, for a collection whose subject its name does not give. */
const EXTENSIONS: Array<{ icon: IconName; match: string[] }> = [
  { icon: "tbl", match: ["csv", "tsv", "xls", "xlsx", "xlsm", "ods"] },
  {
    icon: "image",
    match: ["png", "jpg", "jpeg", "gif", "webp", "bmp", "tif", "tiff", "svg"],
  },
  { icon: "text", match: ["txt", "md", "markdown", "json", "html", "htm"] },
];

const words = (value: string): string[] =>
  value
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter(Boolean);

/** The subject a string names, if it names one. */
function subjectIcon(value: string): IconName | null {
  const found = new Set(words(value));
  return (
    SUBJECTS.find((subject) => subject.words.some((word) => found.has(word)))
      ?.icon ?? null
  );
}

/** What one document is, from its source and its file name. */
export function documentIcon(document: DocumentLike): IconName {
  const type = String(document.content_type ?? "").toLowerCase();
  if (type === "url") return "globe";
  if (type === "clickup") return "link";

  const name = String(document.file_name ?? "");
  const subject = subjectIcon(name);
  if (subject) return subject;
  if (type === "plain_text") return "text";

  const extension = name.split(".").pop()?.toLowerCase() ?? "";
  return (
    EXTENSIONS.find((entry) => entry.match.includes(extension))?.icon ?? "file"
  );
}

/**
 * The mark for a whole collection.
 *
 * Its name decides when the name says what is in it — "Vendor Contracts" is a
 * contract collection however its files happen to be named. Otherwise the
 * documents decide, by majority: a collection whose largest group is under half
 * of it is genuinely mixed and says so with the layers mark instead of claiming
 * to be the kind that happens to lead. An empty collection keeps the plain
 * document mark — it is not mixed, it is simply not filled yet.
 */
export function collectionIcon(
  name: string,
  documents: DocumentLike[],
): IconName {
  const named = subjectIcon(name);
  if (named) return named;
  if (!documents.length) return "file";

  const counts = new Map<IconName, number>();
  for (const document of documents) {
    const icon = documentIcon(document);
    counts.set(icon, (counts.get(icon) ?? 0) + 1);
  }

  const [icon, count] = [...counts.entries()].sort((a, b) => b[1] - a[1])[0];
  if (counts.size > 1 && count / documents.length < 0.5) return "layers";
  return icon;
}

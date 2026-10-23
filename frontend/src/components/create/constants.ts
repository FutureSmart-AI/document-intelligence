import type { IconName } from "@/components/constants";

/**
 * The create flow's fixed vocabulary: its four steps, and nothing else.
 *
 * What used to live here — five kinds of collection, eight type labels — is
 * read from the service now (`GET /v1/field-templates`, `GET /v1/field-types`).
 * Both lists are the backend's to change, and a copy of them here is a copy
 * that goes stale quietly: a template the service added that nobody can pick,
 * or a type label that disagrees with the one Settings prints for the same
 * field.
 *
 * Colocated here rather than at the top of the screen, per the repo's
 * `no-restricted-syntax` rule.
 */

export interface CreateStepMeta {
  key: "describe" | "sample" | "documents";
  title: string;
  /** One line saying what this step is for, shown under its title in the rail. */
  sub: string;
}

/**
 * Three steps, all of them on screen from the first one.
 *
 * Seeing what is coming is the requirement, not decoration: the reason this
 * replaced a dialog is that a dialog cannot show you the last step while you
 * are on the first, so people could not tell whether "Continue" was about to
 * create something. Nothing is created until the last one.
 *
 * Confirming the fields used to be a step of its own. It stopped being one
 * when the field board moved onto the sample step: the same list, beside what
 * it pulled out of a real document, is a better place to confirm it than the
 * same list on its own — and a step whose only job was "look at this, then
 * look at it again next to the answer" is a click, not a decision.
 */
export const CREATE_STEPS: CreateStepMeta[] = [
  {
    key: "describe",
    title: "Describe",
    sub: "What these are, and what you want out of them",
  },
  {
    key: "sample",
    title: "Check the fields",
    sub: "Edit them, and run one document through them",
  },
  {
    key: "documents",
    title: "Add documents",
    sub: "Now, or from your own code later",
  },
];

/**
 * A glyph per kind, where we recognise the kind.
 *
 * Presentation only, and it has to degrade: the catalogue is the service's to
 * grow, so a key we have never seen gets the generic document glyph rather
 * than no card. The names are matched loosely because the service's keys are
 * singular or plural depending on the entry.
 */
export const ICON_FOR: Record<string, IconName> = {
  resume: "docPerson",
  resumes: "docPerson",
  cv: "docPerson",
  invoice: "invoice",
  invoices: "invoice",
  contract: "docSigned",
  contracts: "docSigned",
  ticket: "ticket",
  tickets: "ticket",
  support_ticket: "ticket",
  report: "docChart",
  reports: "docChart",
  email: "mail",
  emails: "mail",
  default: "file",
};

export const iconFor = (key: string): IconName => ICON_FOR[key] ?? "file";

/**
 * How many fields the sample step lists as waiting for a value before it
 * counts the rest instead — long enough to preview the shape of the answer,
 * short enough that the results column does not become the field list twice.
 */
export const WAITING_SHOWN = 8;

/**
 * What a field is read as when nothing has said otherwise.
 *
 * The type dropdown no longer offers "work it out": every row on the board
 * names a type, so a field the service left untyped has to be given one here
 * rather than rendering as whichever option happens to be first. Text is the
 * safe one — it returns what the document said, whatever that turns out to be
 * — and it is the wire name this service has always used for it.
 */
export const TEXT_TYPE = "str";

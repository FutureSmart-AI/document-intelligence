"use client";

import { useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import Icon from "@/components/Icon";
import { Btn, Card, FieldHelp } from "@/components/ui";
import { useCreateCollection, type CreateStep } from "@/data/createCollection";
import { useFieldTemplates, useFieldTypes } from "@/data/fieldCatalog";
import { ingestFiles, ingestPlainText, ingestWebsite } from "@/data/ingest";
import {
  generateSchema,
  previewSample,
  type GeneratedSchema,
  type RejectedField,
  type SamplePreview,
  type SuggestedField,
} from "@/data/schemaDraft";
import {
  DEFAULT_MAX_DEPTH,
  type MaxDepth,
  type ScrapeMethod,
} from "@/lib/constants";
import { quotaErrorOf } from "@/lib/quotaError";
import { collectionRoute, ROUTES } from "@/lib/routes";
import { useApiError } from "@/lib/useApiError";
import { useToast } from "@/platform/components/ToastContext";
import { cn } from "@/platform/utils";

import { CREATE_STEPS } from "./constants";
import CreatedSummary from "./CreatedSummary";
import {
  draftFromSuggestion,
  draftsFromCatalog,
  instructionsFrom,
  isUnfinished,
  mergeDrafts,
  slugify,
  toNewFields,
  toPreviewFields,
  validateDrafts,
  type FieldDraft,
} from "./fieldDraft";
import ReadingInstructions from "./ReadingInstructions";
import StepDescribe from "./StepDescribe";
import StepDocuments, { type DocumentSource } from "./StepDocuments";
import StepSample from "./StepSample";

/**
 * Creating a collection, as a screen rather than a dialog.
 *
 * All four steps are on screen from the first one, and the one you are on is
 * the only one you can edit. That is the whole reason this stopped being a
 * modal: a dialog can show you a form, but it cannot show you that confirming
 * the fields comes before adding any documents, so "Continue" read as "Create"
 * and people hesitated over it.
 *
 * ## Where each step's answer comes from
 *
 * Every field on this screen is the service's, and the list that leaves it is
 * the list that gets built. Nothing is guessed in the browser.
 *
 * 1. **Describe.** The kinds on offer are `GET /v1/field-templates`; picking
 *    one hands step two that template's published fields with no further call.
 *    Typing a description instead calls `POST /v1/enrichment/generate`, which
 *    proposes fields and saves nothing (`run.persisted: false`). A document may
 *    be attached here — it goes to a presigned URL and the schema call is given
 *    the URL, never the bytes — so the fields can be drawn from a real document
 *    as well as from the wording.
 * 2. **Confirm fields.** No call of its own beyond the type catalogue
 *    (`GET /v1/field-types`). The list is edited in the browser, and every time
 *    the service is asked again the board is rebuilt from that answer rather
 *    than added to — see `boardFrom`.
 * 3. **Try a sample.** `POST /v1/enrichment/preview` runs the current list
 *    against one document and answers with what it pulled out. Still nothing
 *    saved, and still no collection.
 * 4. **Add documents.** The collection is created *here* — and only here —
 *    by `useCreateCollection`, which writes the confirmed list in one call.
 *
 * Abandoning the screen before the last step therefore leaves nothing behind,
 * which is the property the old dialog could not offer.
 */
export default function CreateStepper() {
  const router = useRouter();
  const { toast } = useToast();
  const reportError = useApiError();

  const templates = useFieldTemplates();
  const fieldTypes = useFieldTypes();
  const types = useMemo(() => fieldTypes.data ?? [], [fieldTypes.data]);

  const [step, setStep] = useState(0);
  const [template, setTemplate] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [about, setAbout] = useState("");
  const [wanted, setWanted] = useState("");
  const [drafts, setDrafts] = useState<FieldDraft[]>([]);
  const [rejected, setRejected] = useState<RejectedField[]>([]);
  const [thinking, setThinking] = useState(false);

  /**
   * Whether the reading instructions are folded open.
   *
   * Held here rather than in the box, because steps two and three open the same
   * box: opening it on the field board and finding it shut again on the sample
   * would read as two different boxes that happen to say the same thing.
   */
  const [instructionsOpen, setInstructionsOpen] = useState(false);

  /**
   * The document the fields are drawn from and tried against.
   *
   * Held apart from the documents on the last step: this one is read for
   * signal and never added to the collection, and merging the two would make
   * "nothing is saved yet" untrue the moment somebody picked it.
   */
  const [sample, setSample] = useState<File | null>(null);
  const [preview, setPreview] = useState<SamplePreview | null>(null);
  const [previewFailure, setPreviewFailure] = useState<string | null>(null);
  const [previewing, setPreviewing] = useState(false);
  /** Which run is on screen — a second one has to read as a second one. */
  const [runs, setRuns] = useState(0);
  /**
   * The field list that produced the run on screen.
   *
   * Kept as the payload rather than as a flag, because the board is editable
   * on the sample step now: comparing what would be sent with what was sent is
   * the only definition of "out of date" that survives an edit being undone.
   */
  const [ranWith, setRanWith] = useState<string | null>(null);
  /** Suggested fields turned down on this run — not sent, not shown again. */
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());

  const [source, setSource] = useState<DocumentSource>("upload");
  const [files, setFiles] = useState<File[]>([]);
  const [text, setText] = useState("");
  const [url, setUrl] = useState("");
  const [method, setMethod] = useState<ScrapeMethod>("scrape");
  const [maxDepth, setMaxDepth] = useState<MaxDepth>(DEFAULT_MAX_DEPTH);

  const [progress, setProgress] = useState<CreateStep | null>(null);
  const [built, setBuilt] = useState<{
    collectionId: string;
    name: string;
    fields: string[];
    fieldsFailed: boolean;
  } | null>(null);

  /**
   * What the last template wrote into each box.
   *
   * Picking a different template has to be undoable by picking another one, and
   * must never throw away something typed by hand. Remembering exactly what we
   * put there is what separates the two: a box is ours to overwrite while it
   * still holds what we wrote, and theirs the moment they change it.
   */
  const filled = useRef<Record<string, string>>({});

  const create = useCreateCollection({ onStep: setProgress });
  const busy = create.isPending || thinking;

  const requiresSchema = (fieldType: string) =>
    types.find((entry) => entry.field_type === fieldType)?.requires_schema ??
    false;
  const problems = useMemo(
    () => validateDrafts(drafts, requiresSchema),
    // `types` is what `requiresSchema` closes over; recomputing on it is the
    // point, since the catalogue arrives after the first render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [drafts, types],
  );
  /**
   * The rows that are still being written, which are left out rather than
   * fixed — they carry a message of their own, but they do not hold the step.
   */
  const unfinished = useMemo(() => drafts.filter(isUnfinished), [drafts]);
  const problemCount = useMemo(() => {
    const started = new Set(unfinished.map((draft) => draft.id));
    return Object.keys(problems).filter((id) => !started.has(id)).length;
  }, [problems, unfinished]);

  const hasDocuments =
    source === "upload"
      ? files.length > 0
      : source === "plaintext"
        ? text.trim().length >= 6
        : url.trim().length > 0;

  const picked = (templates.data ?? []).find((entry) => entry.key === template);
  const templateName = picked?.display_name ?? "";

  const prefill = (
    key: string,
    value: string,
    current: string,
    set: (next: string) => void,
  ) => {
    // Theirs the moment they change it, and then it is not ours to touch.
    if (current.trim() && current !== filled.current[key]) return;
    // Still ours, so a kind that publishes nothing for this box clears what the
    // last kind put there rather than leaving resume instructions on invoices.
    filled.current[key] = value;
    set(value);
  };

  /**
   * Picking a kind fills step one in with that kind's own wording.
   *
   * All three boxes come from the catalogue entry — `default_collection_name`,
   * `default_service_description`, `default_extraction_instructions` — and not
   * from the card's own labels. `display_name` and `description` name the card
   * you clicked ("Resumes / CVs", "People applying for a role"); they are not
   * an answer to "what are these documents about?", and using them as one is
   * what left the instructions box empty on every template.
   */
  const pickTemplate = (key: string) => {
    const entry = (templates.data ?? []).find((item) => item.key === key);
    if (!entry) return;
    setTemplate(key);
    prefill(
      "name",
      entry.default_collection_name || entry.display_name,
      name,
      setName,
    );
    prefill(
      "about",
      entry.default_service_description || entry.description || "",
      about,
      setAbout,
    );
    prefill(
      "wanted",
      entry.default_extraction_instructions || "",
      wanted,
      setWanted,
    );
  };

  /**
   * The board, rebuilt from what the service has just said.
   *
   * Replacing rather than adding to what is on screen is the point: the two
   * sources here — the picked template's published fields and the schema call's
   * proposals — are both the service's current answer, so a field it no longer
   * proposes is a field that leaves the board. Merging the new answer onto the
   * old one kept rows alive that nothing had proposed since the brief was
   * reworded, and they were created with the collection all the same.
   *
   * The two catalogues are still merged with each other, by name, because the
   * schema call can propose a field the template already publishes and the
   * create route would happily write it twice.
   */
  const boardFrom = (schema: GeneratedSchema | null) =>
    mergeDrafts(
      draftsFromCatalog(picked?.fields ?? [], "template"),
      draftsFromCatalog(schema?.proposed_fields ?? [], "proposed"),
    );

  const back = () => setStep((current) => Math.max(0, current - 1));

  /**
   * The one call that proposes fields: the wording, and the sample document if
   * there is one.
   *
   * Shared by step one and by "re-read the fields from these instructions",
   * because they ask the same question — the second one just asks it again
   * after the instructions were edited.
   */
  const askForFields = async (
    description: string,
    instructions: string | undefined,
  ) =>
    generateSchema({
      collectionName: name.trim() || templateName || undefined,
      serviceDescription: description || (instructions ?? ""),
      extractionInstructions: instructions,
    });

  /**
   * Step one's answer: the field list, from whichever source the person used.
   *
   * A template alone needs no call at all — its fields arrived with the
   * catalogue, already named, described and typed, and asking the service to
   * re-derive them would spend a metered call to land in the same place. It is
   * the wording that needs asking about, and the two combine: the template's
   * fields first, then whatever the schema call proposes that the template does
   * not already publish.
   *
   * The service's optional defaults are not offered and not created. The config
   * PUT already carries `seed_optional_defaults: false`, so what the collection
   * gets is this board and the two protected fields — nothing else arrives
   * behind anybody's back.
   *
   * Picking a template fills the description box in, so "was anything
   * described?" cannot be the test for whether to call — it would make every
   * template path a paid one.
   */
  const buildFields = async () => {
    const fromTemplate = picked?.fields ?? [];
    const instructions = instructionsFrom(wanted);
    const description = about.trim();
    // Still word for word what the template wrote there, so it is the
    // template's own instructions and its published fields already answer
    // them. Only wording someone changed is worth a metered call.
    const ownWords = wanted.trim() !== (filled.current.wanted ?? "").trim();
    const ask = (Boolean(instructions) && ownWords) || !fromTemplate.length;

    if (!ask || (!instructions && !description)) {
      setDrafts(boardFrom(null));
      setRejected([]);
      return true;
    }

    setThinking(true);
    try {
      const schema = await askForFields(description, instructions);

      setDrafts(boardFrom(schema));
      setRejected(schema.rejected_fields);
      return true;
    } catch (error) {
      // A refused allowance is the one failure worth stopping on: the fields
      // cannot be worked out and retrying costs the same. Everything else falls
      // back to the template's fields, or to an empty board someone can fill in
      // by hand, rather than a dead end.
      if (quotaErrorOf(error)) {
        reportError(error, "");
        return false;
      }
      reportError(
        error,
        "We could not work the fields out from that description — add them yourself below, or go back and try different wording.",
      );
      setDrafts(boardFrom(null));
      setRejected([]);
      return true;
    } finally {
      setThinking(false);
    }
  };

  /**
   * "Re-read the fields from these instructions".
   *
   * The instructions are the only input anybody can still change once they are
   * past step one, so editing them has to be able to change the board — a box
   * you can type in that quietly does nothing is worse than no box. What comes
   * back *replaces* the board rather than being added to it, which is the only
   * reading of "re-read" that leaves the list in sync with the service: fields
   * the new instructions no longer call for go, instead of surviving as rows
   * nothing has proposed since. The toast says so, because rows typed by hand
   * go with them.
   */
  const rereadFields = async () => {
    const instructions = instructionsFrom(wanted);
    if (!instructions) {
      toast.error("Write how these documents should be read first.");
      return;
    }

    setThinking(true);
    try {
      const schema = await askForFields(about.trim(), instructions);
      setDrafts(boardFrom(schema));
      setRejected(schema.rejected_fields);
      // Two things worth saying: the board is now the service's answer and not
      // the old one plus it, and the sample on screen was run against the list
      // that just went away.
      toast.success(
        preview
          ? "Fields re-read — the list below is what the service proposes now. Run the sample again to see the difference."
          : "Fields re-read — the list below is what the service proposes now.",
      );
    } catch (error) {
      reportError(
        error,
        "We could not re-read the fields from those instructions — edit them and try again.",
      );
    } finally {
      setThinking(false);
    }
  };

  /** What a run would send right now, which is what staleness compares to. */
  const previewFields = useMemo(() => toPreviewFields(drafts), [drafts]);

  /**
   * A document arrives from the picker, and is read straight away.
   *
   * Picking it *is* the instruction — the column beside the board exists to
   * show what these fields read out of a document, and there is nothing to
   * decide between choosing one and seeing that. Only the first read of a
   * document is automatic: every run after it answers a change the reader
   * made to the fields, so it stays theirs to ask for with **Run again**.
   *
   * The counter restarts because this is a different document: "Run 4" on a
   * document being read for the first time would be counting the wrong thing.
   */
  const pickSample = (file: File) => {
    setSample(file);
    setPreview(null);
    setRanWith(null);
    setPreviewFailure(null);
    setRuns(0);

    // Nothing to run it against yet — the board is empty, and the disabled
    // Run button says the same thing.
    if (!previewFields.length) return;
    void runSample(file);
  };

  /** Step three's answer: this document, run against the list as it stands. */
  const runSample = async (file: File | null = sample) => {
    if (!file) return;
    const fields = previewFields;
    setPreviewing(true);
    setPreviewFailure(null);
    try {
      setPreview(await previewSample(fields, { file }));
      setRanWith(JSON.stringify(fields));
      setDismissed(new Set());
      setRuns((current) => current + 1);
    } catch (error) {
      setPreview(null);
      setRanWith(null);
      const status = (error as { response?: { status?: number } })?.response
        ?.status;
      if (status === 422)
        // Not a bug and not a broken field list: there was no text to read.
        setPreviewFailure(
          `We could not read any text out of ${file.name}. If it is a scan, it will need OCR — you can change how documents are processed in Settings once the collection exists.`,
        );
      else if (status === 502)
        // Distinct from a field not being found, and rendering it as one would
        // tell somebody their fields are wrong when they are not.
        setPreviewFailure(
          "The extraction itself failed on this document — that is not the same as a field not being found. Try running it again, or use a different document.",
        );
      else reportError(error, `${file.name} could not be run just now.`);
    } finally {
      setPreviewing(false);
    }
  };

  const acceptSuggestion = (field: SuggestedField) =>
    setDrafts((current) => mergeDrafts(current, [draftFromSuggestion(field)]));

  const dismissSuggestion = (fieldName: string) =>
    setDismissed((current) => new Set(current).add(fieldName));

  /**
   * The result on screen was read with a list that has since been edited.
   *
   * Said rather than hidden: the values stay up, dimmed, so the reworded field
   * can be compared against what the old one pulled out — and "Run again"
   * becomes the primary action until it is.
   */
  const sampleStale = Boolean(
    preview && ranWith !== null && ranWith !== JSON.stringify(previewFields),
  );

  const acceptedNames = useMemo(
    () => new Set(drafts.map((draft) => slugify(draft.name))),
    [drafts],
  );

  const next = async () => {
    if (step === 0) {
      if (await buildFields()) setStep(1);
      return;
    }
    if (step === 1) {
      // The board is edited on this step now, so this is where a broken field
      // is caught — the sample itself stays optional.
      if (problemCount) {
        toast.error(
          `Fix the ${problemCount} field${problemCount === 1 ? "" : "s"} marked below first.`,
        );
        return;
      }
      // An unfinished row is not a mistake to fix, so it does not stop this —
      // but it is about to disappear from what gets created, and that has to
      // be said out loud rather than found afterwards in Settings.
      if (unfinished.length) {
        const names = unfinished
          .map((draft) => slugify(draft.name))
          .filter(Boolean);
        toast.info(
          unfinished.length === 1
            ? `Leaving out ${names[0] ? `"${names[0]}"` : "the unfinished field"} — it has no description yet.`
            : `Leaving out ${unfinished.length} unfinished fields — they have no name or description yet.`,
        );
      }
      setStep(2);
      return;
    }
    void submit();
  };

  const openCollection = (collectionId: string) => {
    router.push(collectionRoute(ROUTES.knowledge, collectionId));
  };

  const submit = async () => {
    const trimmed = name.trim() || templateName;
    if (!trimmed) {
      toast.error("Give the collection a name.");
      setStep(0);
      return;
    }

    try {
      const { collectionId, knowledgeId, createdFields, fieldsFailed } =
        await create.mutateAsync({
          name: trimmed,
          about: about.trim(),
          structuredExtraction: true,
          fields: toNewFields(drafts),
        });

      if (hasDocuments) {
        try {
          if (source === "plaintext") await ingestPlainText(knowledgeId, text);
          else if (source === "url")
            await ingestWebsite(knowledgeId, url.trim(), { method, maxDepth });
          else {
            const { failed } = await ingestFiles(knowledgeId, files);
            if (failed.length) {
              const names = failed.map((entry) => entry.fileName).join(", ");
              toast.error(
                `${failed.length} file${failed.length === 1 ? "" : "s"} could not be sent: ${names}`,
              );
            }
          }
        } catch (error) {
          // The collection exists; only the documents failed. Knowledge is
          // where they get added, so say that rather than losing a collection
          // the account can use — except when the account is out of quota,
          // where the allowance that refused matters more than where to retry.
          if (quotaErrorOf(error)) reportError(error, "");
          else
            toast.error(
              `"${trimmed}" was created, but its documents were not sent. Add them from Knowledge.`,
            );
        }
      }

      if (fieldsFailed) {
        toast.error(
          `"${trimmed}" was created, but its fields were not. Add them in Settings → Extracted fields.`,
        );
      }

      setProgress(null);
      setBuilt({
        collectionId,
        name: trimmed,
        fields: createdFields,
        fieldsFailed,
      });
    } catch (error) {
      // Nothing else reports this: a swallowed error here is a Create button
      // that does nothing. A full account is refused right here, and
      // `useApiError` says which allowance ran out and links to the Limits tab.
      reportError(error, `"${trimmed}" could not be created.`);
      setProgress(null);
    }
  };

  if (built) return <CreatedSummary built={built} onOpen={openCollection} />;

  const canContinue =
    step === 0
      ? Boolean(about.trim() || wanted.trim() || template)
      : step === 1
        ? // Running the sample stays optional; a field that cannot be created
          // does not.
          problemCount === 0 && !busy
        : !busy;

  /**
   * One box, rendered on both of the steps that can open it. The same element
   * against the same state — two copies would drift the moment one of them was
   * typed in.
   */
  /**
   * What the board itself has to say: a type catalogue that would not load,
   * the fields the service turned down, and the promise that this list is the
   * list. They followed the board onto this step when it moved.
   */
  const boardNotes = (
    <>
      {fieldTypes.isError && (
        <FieldHelp className="mt-2">
          We could not load the list of field types, so the types above are the
          ones the service already chose. They will still be created exactly as
          shown.
        </FieldHelp>
      )}
      {rejected.length > 0 && (
        <div data-demo="create-rejected">
          <FieldHelp className="mt-2">
            Left out:{" "}
            {rejected
              .map((entry) =>
                entry.reason
                  ? `${entry.field_name} (${entry.reason})`
                  : entry.field_name,
              )
              .join(", ")}
            . Add any of them back with <b>Add a field</b> if you disagree.
          </FieldHelp>
        </div>
      )}
      <FieldHelp className="mt-2.5">
        This list is what gets created, exactly as it stands.
      </FieldHelp>
    </>
  );

  const instructionsBox = (
    <ReadingInstructions
      value={wanted}
      onChange={setWanted}
      open={instructionsOpen}
      onToggle={() => setInstructionsOpen((current) => !current)}
      onReread={rereadFields}
      busy={busy}
    />
  );

  return (
    <div className="mx-auto w-full max-w-350">
      <div className="mb-2 flex items-start gap-4">
        <div className="min-w-0 flex-1">
          <h1 className="m-0 mb-0.75 text-[19px] font-extrabold tracking-[-0.02em] text-ink">
            New document collection
          </h1>
          <p className="m-0 text-[12.5px] leading-relaxed text-ink-2">
            Tell us what these documents are and what you need out of them. You
            will see the fields we are going to ask for, and what they pull out
            of one of your own documents, before anything is created.
          </p>
        </div>
        <Btn onClick={() => router.push(ROUTES.home)} disabled={busy}>
          Cancel
        </Btn>
      </div>

      {/* Every step, from the first one. Seeing what is coming is the
          requirement, not decoration. */}
      <ol
        data-demo="create-steps"
        className="mb-3.5 flex list-none flex-wrap items-stretch overflow-hidden rounded-di-lg border border-line bg-surface p-0"
      >
        {CREATE_STEPS.map((meta, index) => {
          const state =
            index === step ? "active" : index < step ? "done" : "todo";
          return (
            <li
              key={meta.key}
              aria-current={state === "active" ? "step" : undefined}
              className={cn(
                "flex min-w-0 flex-1 basis-45 items-start gap-2.25 border-r border-line-soft px-3.25 py-2.25 last:border-r-0",
                state === "active" && "bg-accent-soft",
              )}
            >
              <span
                className={cn(
                  "flex size-6 flex-none items-center justify-center rounded-full border-[1.5px] text-[12px] font-bold",
                  state === "done" &&
                    "border-success-dot bg-success-dot text-white",
                  state === "active" &&
                    "border-accent bg-accent text-accent-contrast",
                  state === "todo" && "border-line-strong bg-sunken text-ink-3",
                )}
              >
                {state === "done" ? (
                  <Icon name="chk" className="size-3" />
                ) : (
                  index + 1
                )}
              </span>
              <span className="min-w-0">
                <b
                  className={cn(
                    "block text-[13px] leading-tight font-bold",
                    state === "todo" ? "text-ink-3" : "text-ink",
                  )}
                >
                  {meta.title}
                </b>
                <span
                  className={cn(
                    "mt-0.5 block text-[11.5px] leading-snug",
                    state === "active" ? "text-ink-2" : "text-ink-3",
                  )}
                >
                  {meta.sub}
                </span>
              </span>
            </li>
          );
        })}
      </ol>

      <Card className="px-4.5 py-3.5">
        {step === 0 && (
          <StepDescribe
            templates={templates.data ?? []}
            templatesLoading={templates.isLoading}
            templatesFailed={templates.isError}
            template={template}
            onPickTemplate={pickTemplate}
            name={name}
            onNameChange={setName}
            about={about}
            onAboutChange={setAbout}
            wanted={wanted}
            onWantedChange={setWanted}
          />
        )}

        {step === 1 && (
          <StepSample
            drafts={drafts}
            onDraftsChange={setDrafts}
            types={types}
            instructions={instructionsBox}
            file={sample}
            // Picking a document is the whole instruction: the column beside it
            // exists to show what these fields read out of it, and a second
            // click to make that happen is a step with nothing in it.
            onPick={pickSample}
            onClear={() => {
              setSample(null);
              setPreview(null);
              setRanWith(null);
              setPreviewFailure(null);
              setRuns(0);
              setDismissed(new Set());
            }}
            onRun={() => void runSample()}
            preview={preview}
            runs={runs}
            stale={sampleStale}
            running={previewing}
            failure={previewFailure}
            onAcceptSuggestion={acceptSuggestion}
            onDismissSuggestion={dismissSuggestion}
            acceptedNames={acceptedNames}
            dismissedNames={dismissed}
            notes={boardNotes}
            busy={busy}
          />
        )}

        {step === 2 && (
          <StepDocuments
            collectionName={name.trim() || templateName || "this collection"}
            fieldCount={drafts.length}
            source={source}
            onSourceChange={setSource}
            files={files}
            onFilesChange={setFiles}
            text={text}
            onTextChange={setText}
            url={url}
            onUrlChange={setUrl}
            method={method}
            onMethodChange={setMethod}
            maxDepth={maxDepth}
            onMaxDepthChange={setMaxDepth}
            busy={busy}
            progress={progress}
            hasDocuments={hasDocuments}
          />
        )}
      </Card>

      {/* Sticky, so the button that commits the step is reachable on every step
          and at every viewport. */}
      <div className="sticky bottom-0 z-3 mt-3 flex flex-wrap items-center gap-2.5 border-t border-line bg-bg px-0 py-2.5">
        <Btn onClick={back} disabled={step === 0 || busy}>
          ← Back
        </Btn>
        <span className="text-[12px] text-ink-2">
          {step === 0
            ? thinking
              ? "Working out the fields from what you wrote…"
              : "Next: the fields we would pull out. Nothing is created yet."
            : step === 1
              ? problemCount
                ? `Fix the ${problemCount} field${problemCount === 1 ? "" : "s"} marked above.`
                : unfinished.length
                  ? `${unfinished.length} unfinished field${unfinished.length === 1 ? "" : "s"} above will be left out — finish ${unfinished.length === 1 ? "it" : "them"} or carry on.`
                  : !preview
                    ? "Trying one document is optional — the fields are created as they stand."
                    : sampleStale
                      ? "Run it again to see what your changed fields read out of this document."
                      : `Run ${runs} · change anything on the left and run it again, as often as you like.`
              : "Nothing has been created yet."}
        </span>
        <div className="flex-1" />
        <Btn
          variant="primary"
          data-demo="create-submit"
          onClick={() => void next()}
          disabled={!canContinue || busy}
        >
          {step === 0
            ? thinking
              ? "Working it out…"
              : "Continue"
            : step === 1
              ? preview
                ? "These fields work — continue"
                : "Looks right — continue"
              : busy
                ? "Creating…"
                : hasDocuments
                  ? "Create and add my documents"
                  : "Create it empty"}
        </Btn>
      </div>
    </div>
  );
}

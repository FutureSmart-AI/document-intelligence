"use client";

import {
  Btn,
  Card,
  Chip,
  FieldHelp,
  FieldLabel,
  LinkBtn,
  Pill,
} from "@/components/ui";

/**
 * What was created, once the collection exists.
 *
 * Short by design now. The fields were confirmed two steps ago and tried
 * against a real document one step ago, so this is a receipt rather than a
 * reveal — the only thing here that could surprise anybody is the one case
 * where the fields did not get written, and that gets said plainly instead of
 * being left for somebody to find in Settings.
 */
export default function CreatedSummary({
  built,
  onOpen,
}: {
  built: {
    collectionId: string;
    name: string;
    /** The field names that were written, in the order they were sent. */
    fields: string[];
    fieldsFailed: boolean;
  };
  onOpen: (collectionId: string) => void;
}) {
  return (
    <div data-demo="created-summary" className="mx-auto w-full max-w-180">
      <h1 className="m-0 mb-1 text-[19px] font-extrabold tracking-[-0.02em] text-ink">
        &ldquo;{built.name}&rdquo; is ready
      </h1>
      <p className="m-0 mb-4 text-[13px] text-ink-2">
        {built.fieldsFailed
          ? "The collection exists, but the fields you confirmed were not saved with it."
          : "Every document you add will be read for the fields below. Change any of them under Settings → Extracted fields."}
      </p>

      <Card className="mb-4 px-4 py-3.5">
        {built.fieldsFailed ? (
          <>
            <Pill tone="warn">Fields not saved</Pill>
            <FieldHelp className="mt-2">
              It starts on the two fields every collection has. Add the ones you
              confirmed under <b>Settings → Extracted fields</b> — nothing has
              been read against the wrong schema yet, because no document has
              been processed.
            </FieldHelp>
          </>
        ) : (
          <>
            <FieldLabel>
              {built.fields.length} field
              {built.fields.length === 1 ? "" : "s"}, exactly as you confirmed
              them
            </FieldLabel>
            {built.fields.length ? (
              <div className="flex flex-wrap gap-1.5">
                {built.fields.map((field) => (
                  <Chip key={field}>{field}</Chip>
                ))}
              </div>
            ) : (
              <FieldHelp>
                None beyond the two every collection carries. Name the details
                you want under <b>Settings → Extracted fields</b>.
              </FieldHelp>
            )}
          </>
        )}
      </Card>

      <div className="flex flex-wrap items-center gap-2.5">
        <Btn variant="primary" onClick={() => onOpen(built.collectionId)}>
          Open collection
        </Btn>
        <LinkBtn onClick={() => onOpen(built.collectionId)}>
          See your documents as they finish
        </LinkBtn>
      </div>
    </div>
  );
}

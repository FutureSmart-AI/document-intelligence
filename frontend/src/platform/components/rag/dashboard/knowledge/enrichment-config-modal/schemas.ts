import { includes, isEmpty, lowerCase, trim } from "lodash-es";
import z from "zod";

export const enrichmentFieldSchema = z
  .object({
    id: z.number(),
    field_name: z.string().min(1, "Field name is required."),
    description: z.string().min(1, "Description is required."),
    /**
     * Empty when the service resolves the type from the description instead of
     * taking one (see `typeMode` on the enrichment page) — the payload then
     * omits `field_type` altogether rather than sending a guess. Every screen
     * that does offer a type picker fills this from a select, which cannot be
     * cleared, so nothing reaches the service without one by accident.
     */
    field_type: z.string(),
    field_type_label: z.string().nullable().optional(),
    level: z.enum(["document", "chunk"]),
    is_custom: z.boolean(),
    sample_values: z.string(),
    restrict_to_choices: z.boolean(),
    max_items: z.union([z.number(), z.string(), z.null()]).optional(),
    is_active: z.boolean(),
    is_required: z.boolean(),
    schema: z.union([z.string(), z.null()]).optional(),
  })
  .superRefine((value, ctx) => {
    // Validate schema is valid JSON if provided (non-empty string)
    if (
      value.schema &&
      typeof value.schema === "string" &&
      trim(value.schema)
    ) {
      try {
        JSON.parse(value.schema);
      } catch {
        ctx.addIssue({
          code: "custom",
          message: "Schema must be valid JSON.",
          path: ["schema"],
        });
      }
    }
  })
  .superRefine((value, ctx) => {
    if (value.restrict_to_choices && isEmpty(trim(value.sample_values))) {
      ctx.addIssue({
        code: "custom",
        message:
          "Provide at least one sample value when restricting to choices.",
        path: ["sample_values"],
      });
    }
  })
  .refine(
    (data) => {
      // For list fields, max_items can be null, empty string, 0 (no limit), or a positive number
      // Only invalid if it's a negative number
      const maxItems = data.max_items;
      if (
        (includes(data.field_type, "List") ||
          includes(data.field_type.toLowerCase(), "list")) &&
        maxItems !== null &&
        maxItems !== undefined &&
        maxItems !== "" &&
        typeof maxItems !== "string"
      ) {
        const num = typeof maxItems === "string" ? Number(maxItems) : maxItems;
        if (!Number.isNaN(num) && num < 0) {
          return false;
        }
      }
      return true;
    },
    {
      message:
        "Max items must be null (no limit) or a positive number for list fields.",
      path: ["max_items"],
    },
  );

export const enrichmentFormSchema = z.object({
  fields: z.array(enrichmentFieldSchema).superRefine((fields, ctx) => {
    const seenNames = new Map<string, number>();
    fields.forEach((field, index) => {
      const normalized = lowerCase(trim(field.field_name));
      if (seenNames.has(normalized)) {
        const firstIndex = seenNames.get(normalized)!;
        [
          { idx: firstIndex, path: ["field_name"] as const },
          { idx: index, path: ["field_name"] as const },
        ].forEach(({ idx, path }) => {
          ctx.addIssue({
            code: "custom",
            message: "Field names must be unique.",
            path: [idx, ...path],
          });
        });
      } else {
        seenNames.set(normalized, index);
      }
    });
  }),
});

export type EnrichmentFormData = z.infer<typeof enrichmentFormSchema>;

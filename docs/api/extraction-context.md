# Extraction context in tabular APIs and Knowledge CSV

Both `POST /v1/{knowledge_id}/documents/metadata/all` (cursor) and its v2
page-based counterpart return an additive `extraction_context` object keyed by
the returned document IDs. Existing columns, rows, values and pagination are
unchanged. Both Knowledge CSV exports append `Extraction context (JSON)` with
the same context per document. Existing CSV value cells keep their formatting.

```json
{
  "rows": [{"object_id":"old-A","loyalty_id":null,"damage":[],"total_due":null}],
  "extraction_context": {
    "old-A": {
      "status":"recorded",
      "event_key":"successful-enrichment-event-uuid",
      "processed_at":"2026-10-20T13:49:22+00:00",
      "processed_fields":["damage","loyalty_id"],
      "definition_history":"unknown"
    }
  }
}
```

`status` is `recorded` or `unknown`. Recorded context comes only from the latest
successful enrichment event's complete, nonempty `field_data`. It lists
document-level field names, including fields that returned no value. In the
example, loyalty/damage were requested; total_due was not in that recorded
attempt. Null and the explicit empty array remain separate API values. This
context does not certify source absence or extraction accuracy.

Unknown context has null `event_key`, `processed_at` and `processed_fields`.
No-log, failed-only, empty/malformed/partial historical records remain unknown.
A later failed retry does not replace the last success. A later success without
usable context makes history unknown, rather than reusing an older snapshot.
Only the current page's document IDs and collection are queried. A failed or
two-second context read returns unknown; metadata retrieval remains available.

`definition_history` is always `unknown`: existing events do not preserve the
description, type or nested schema used at extraction time. A field name appearing
in `processed_fields` does **not** establish that its current definition was used.
For example, changing `total_due` from invoice total to VAT without reprocessing
must preserve the old1246.8; that value is not certified against the new meaning.
Historical context is read only; there is no migration, inferred backfill, schema
version framework or automatic reprocessing.

For consumers of older API deployments, a missing context entry must be treated
as unknown. CSV values stay unchanged, including numeric and nested-value cells;
read the companion JSON when processing availability matters. The existing UI
continues using its enrichment-log field-name display behavior.

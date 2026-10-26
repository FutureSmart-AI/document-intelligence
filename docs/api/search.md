---
title: Search API
covers: [backend/routers/search.py]
updated: 2026-10-20
---

# Search

`POST /v1/{knowledge_id}/search` returns chunks from that knowledge base and
requires its `use` permission. Dense and hybrid retrieval share the same hard
document-scope contract.

| Body field | Default | Meaning |
| --- | --- | --- |
| `query` | null | Text for retrieval. Without a query, supply document IDs to fetch their chunks. |
| `num_chunks` | 3 | Number of chunks to return, from1 through100. |
| `object_ids` | null | Hard allowed document IDs. Omitted/null searches the knowledge base; an empty array returns no chunks. |
| `mode` | `hybrid` | `hybrid` (dense + sparse fusion) or `non_hybrid` (dense). |

```json
{
  "query": "What is the acknowledgement time?",
  "num_chunks": 3,
  "object_ids": ["00000000-0000-4000-8000-000000000001"],
  "mode": "hybrid"
}
```

A200 response has a `chunks` array. Each retrieved chunk includes its `id`,
`object_id`, `knowledge_id`, `chunk_text`, `relevance_score` and retrieval tag
alongside stored payload fields. Known document scope may yield relevant text
that does not answer the question; retrieval ranking is not answerability.

A supplied document list never broadens because no chunks matched. Unknown,
fully deleted and explicitly empty scopes stay empty, rather than searching
other documents. A valid selected non-answering document can return its own
chunks, but no chunk from an excluded document. Knowledge-base isolation still
applies even when an object ID belongs to another knowledge base.

Only the four fields above are supported. Unrecognized fields, including
`filters`, `metadata_filter`, `top_k` or misspelled `object_id`, return422 with
validation details. This endpoint does not implement a metadata query language;
previously ignored filter syntax must not be interpreted as an applied filter.
Invalid search mode with a query returns400. No query and no document selection
returns400; an explicit empty selection returns an empty `chunks` array.

Public hard scope is separate from internal agent-selected relevance hints.
The internal chat adapter explicitly opts into its existing relevance expansion;
public callers cannot enable that option through the request body. The chat
endpoint has no new document-scope field in this change. Models, prompts,
embeddings and unscoped retrieval behavior are unchanged.

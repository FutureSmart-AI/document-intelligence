# Maintaining the improvement history

Update this history in the same change that alters a research-driven behavior,
evaluation conclusion or scope decision. Record a nonreproducing finding and a
decision not to implement a capability just as carefully as a fix.

1. Preserve the original source/run and literal verdict. Never overwrite an old
   Fail with Pass because code merged, CI passed, or a different fixture worked.
2. Separate original benchmark verdict, evidence availability, current replay,
   implementation, deployment and independent acceptance. Keep Partial literal;
   do not silently normalize it into Pass or Fail. An unset result stays unset.
3. Add an entry in `records.json` and the corresponding `history.md` section:
   the benchmark it came from (Structured Document Extraction, Managed RAG or
   none; never merge the two), research source and publication status, recorded date/timezone, failure,
   verified or unknown cause, engineering decision, code/tests and validation,
   measured limits and any unfinished gates. Use the template below.
4. Link the actual published scenario/result URL when verified. A tool review,
   earlier platform study or category page is not a replacement for that run.
   Do not guess evidence permalinks. Withhold unreleased research and retain its
   task/fixture/report/commit mapping only in private `internal/` records.
5. Pin source bytes/schema/model/config and compare complete results. For a
   changing database, capture reference SQL on the same data state. Replays with
   new schemas or fixtures are new evidence, not reconstructed historical runs.
6. Describe test doubles, omitted contracts, failures, retries, unknowns and
   source changes. Preserve the first failure. A request refusal is not a pass
   for a downstream contract it never exercised. No retry-until-pass reporting.
7. Public material may contain approved published findings and authorized
   summaries of our own engineering work. Exclude private ClickUp discussions,
   unreleased report text, original unapproved research data, competitor
   intelligence, credentials, private endpoints, signed artifact URLs and
   identifying runtime payloads. Re-check screenshots and generated files.
8. Keep public source snapshots free of private Git history and `internal/`.
   Publication/licensing decisions are separate from this documentation.
9. A reference application must remain installable with a usable product UI and
   direct API. Do not chase a score by hardcoding benchmark answers, dropping
   failed cases or adding every commercial feature.

`records.json` is an explicit public allowlist, not a render of the private task
cache. The private baseline owns original verdicts that are not approved for
release. Later contributors must complete publication review before adding them.
There is no new scheduled automation or automatic benchmark run for this history.

## Entry template

```text
ID / title:
Date and timezone (or unknown):
Original research source / run / published URL (or withheld):
Original literal verdict (or unset / not publicly verifiable):
Observed failure:
Root cause: verified / hypothesis / unknown:
Engineering decision, including declined capability:
Implementation/source/test references (or no code change):
Validation: fixture/schema/model/version/environment; actual result; reviewer:
Status: released / source_accepted / candidate_unaccepted /
        nonreproducing_current / evaluation_only / deferred:
Limits, unaccepted gates and benchmark verdict unchanged:
```

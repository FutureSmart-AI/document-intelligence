---
title: tests/fixtures — ingestion test files, and what each one tests
covers: [tests/fixtures/]
updated: 2026-08-24
---

# tests/fixtures

Real files for exercising document ingestion end to end: the sizes, page counts, formats,
visual content and malformed shapes an upload has to cope with — plus a small website for
the weblink source.

Every file is listed below with a link to open it and a note on what it tests. If a file and
this page ever disagree, this page is the claim and the file is the evidence — run `--verify`
to find out which one moved.

```bash
python tests/fixtures/generate_fixtures.py --verify     # rebuild and re-measure: 36 checks
```

[Docs home](../../docs/README.md) ·
[Development and testing guide](../../docs/development.md#backend-and-worker) ·
[Corpus provenance](sources/SOURCES.md) ·
[Generator](generate_fixtures.py)

---

## Conventions for adding a fixture

Four rules, so the set stays usable and safe to upload anywhere.

**1. English only.** Every source text and every generated file is in English. A reviewer
should be able to read any fixture and judge whether an answer drawn from it is correct,
without a translation step.

The single exception is [`accept_unicode_mixed.txt`](files/accept_unicode_mixed.txt), whose
three-line header is in Hindi, Japanese and German. That file's entire purpose is character
encoding, which cannot be tested in one script — its meaning is given in section A below, so
it is still readable by someone who speaks none of the three. Do not treat it as licence to
add non-English content elsewhere.

**2. No information about how the service works, inside a fixture.** Fixtures get uploaded to
real knowledge bases during testing, and anything inside one can end up indexed, retrieved,
and quoted back by a chat answer. So fixture *content* is public-domain literature and
neutral facts about that literature — never limits, parameter names, file paths, endpoints,
or internal behaviour. [`accept_table_layout.pdf`](files/accept_table_layout.pdf) is a
reading list for exactly this reason. This page may describe behaviour freely; the files may
not carry it.

**3. The name states the expected outcome.** `accept_`, `reject_`, `workerfail_` — so a
directory listing is already a summary. Name a file for what the service actually does with
it, not for what you expect it to do before checking.

**4. Rebuilds must stay byte-stable.** Anything that records a creation time gets a fixed
one. See [Rebuilding](#rebuilding).

## The limits being tested

| Limit | Value | Applies to |
|---|---|---|
| Maximum file size | 5 MB | every upload |
| Maximum pages | 10 | PDFs, and DOCX files that record a page count |
| Maximum characters | 100,000 | plain text, Markdown, HTML |
| Minimum characters | 10 | checked during processing, not at upload |
| Crawl depth | 0 to 2 | weblinks |

**The order the checks run in matters.** Size is checked first, then page count, then the
character cap. A 6 MB text file breaks two rules at once and will always report the size
one, because that check runs first and stops there.

## Every accepted content type has a fixture

Eleven content types are accepted. All eleven are covered, and `--verify` fails if any one
loses its fixture — adding a format is a small change that nothing would otherwise prompt
anyone to reflect here, so a gap would surface only when someone uploads that format and it
breaks.

| Content type | Extensions | Fixture |
|---|---|---|
| `application/pdf` | `.pdf` | [accept_declaration_1page.pdf](files/accept_declaration_1page.pdf) |
| `application/vnd...wordprocessingml.document` | `.docx` | [accept_declaration_no_pagecount.docx](files/accept_declaration_no_pagecount.docx) |
| `text/plain` | `.txt` | [accept_declaration.txt](files/accept_declaration.txt) |
| `text/markdown` | `.md` | [accept_constitution.md](files/accept_constitution.md) |
| `text/html` | `.html` | [accept_declaration.html](files/accept_declaration.html) |
| `text/html` | `.htm` | [accept_declaration.htm](files/accept_declaration.htm) |
| `application/octet-stream` | `.md` **only** | [accept_octet_stream.md](files/accept_octet_stream.md) |
| `audio/mpeg` | `.mp3` | [workerfail_silent_tone.mp3](files/workerfail_silent_tone.mp3) |
| `audio/mpeg` | `.aac` | [workerfail_silent_tone.aac](files/workerfail_silent_tone.aac) |
| `audio/wav` | `.wav` | [workerfail_silent_tone.wav](files/workerfail_silent_tone.wav) |
| `audio/x-wav` | `.wav` | same file — two spellings of one format |
| `audio/ogg` | `.ogg` | [workerfail_silent_tone.ogg](files/workerfail_silent_tone.ogg) |
| `video/mpeg` | `.mpeg` | [workerfail_silent_tone.mpeg](files/workerfail_silent_tone.mpeg) |

**`application/octet-stream` is a trap.** It is accepted at the door, but it is only readable
when the filename ends in `.md`. Any other extension is rejected later, during processing.
Browsers and SDKs fall back to octet-stream whenever they cannot guess a type, so this is a
live path rather than a curiosity. Both halves have a fixture:
[`accept_octet_stream.md`](files/accept_octet_stream.md) works,
[`workerfail_octet_stream_not_md.csv`](files/workerfail_octet_stream_not_md.csv) does not.

## The audio fixtures hold a tone, not speech

All five audio and video fixtures are a one-second 440 Hz tone. They exist to prove the
*container* is accepted: type-checking and transcription are separate steps, and a format the
service claims to support can still be turned away at the door with nothing to catch it.

What they cannot test is transcription, because generating real speech reproducibly needs a
text-to-speech engine this repository does not have. Each one transcribes to an empty string
and ends up failed — useful as a `workerfail_` fixture, but it means **a green run over these
files says nothing about whether audio ingestion actually works.** For that you need a real
recording, and the honest place for one is a live test with its own audio.

Two practical notes: a live test over these makes a real transcription API call (cheap at one
second, not free), and rebuilding them needs `imageio-ffmpeg`. Without it the generator logs
a warning, leaves the committed audio untouched, and builds everything else.

## Three outcomes, not two

It is tempting to sort fixtures into "should work" and "should fail". There is a third
outcome, and it is the one that catches people out:

| Prefix | Outcome |
|---|---|
| `accept_` | 200, the document is queued and indexes |
| `reject_` | 400 with a specific message, nothing stored |
| `workerfail_` | 200 at upload, then the document ends up `failed` with an explanation |

The third exists because text extraction was deliberately moved out of the upload request. It
used to run there purely to enforce the minimum length, which meant every page and every
minute of audio was paid for twice — once to check the file, once to index it. It also
rejected scanned PDFs outright, since a scan has no text layer to measure.

So **a test that only asserts on the upload response will pass on files that never index.**
For `workerfail_` fixtures the assertion has to poll the document's status endpoint until it
settles.

---

## A — Accepted: format coverage

| File | What it tests | Expected |
|---|---|---|
| [accept_declaration_1page.pdf](files/accept_declaration_1page.pdf) | Ordinary single-page PDF with a text layer | 200 → indexed |
| [accept_constitution_10page.pdf](files/accept_constitution_10page.pdf) | **Exactly** 10 pages — the boundary is inclusive | 200 → indexed |
| [accept_constitution_10page.docx](files/accept_constitution_10page.docx) | DOCX recording a 10-page count | 200 → indexed |
| [accept_declaration_no_pagecount.docx](files/accept_declaration_no_pagecount.docx) | Valid DOCX with **no** page count recorded | 200 → page check skipped entirely |
| [accept_declaration.txt](files/accept_declaration.txt) | Plain text, ~10,600 characters | 200 → indexed |
| [accept_constitution.md](files/accept_constitution.md) | Markdown with real headings | 200 → indexed |
| [accept_declaration.html](files/accept_declaration.html) | HTML | 200 → indexed |
| [accept_declaration.htm](files/accept_declaration.htm) | The `.htm` spelling of the same type | 200 → indexed |
| [accept_octet_stream.md](files/accept_octet_stream.md) | Octet-stream with a `.md` filename — the one such path that works | 200 → indexed |
| [accept_text_at_char_limit.txt](files/accept_text_at_char_limit.txt) | **Exactly** 100,000 characters once trimmed | 200 → the cap is inclusive |
| [accept_min_length_10chars.txt](files/accept_min_length_10chars.txt) | **Exactly** 10 characters — the minimum | 200 → indexed |
| [accept_filename with spaces & (special)#chars.txt](<files/accept_filename with spaces & (special)%23chars.txt>) | A filename needing sanitisation before it can become a storage key | 200 → check the resulting key |
| [accept_unicode_mixed.txt](files/accept_unicode_mixed.txt) | Typographic punctuation plus three non-Latin scripts | 200 → no mojibake in the stored chunks |

[`accept_declaration_no_pagecount.docx`](files/accept_declaration_no_pagecount.docx) is worth
a second look. The library that generates these files writes a page count of 1 by default, so
a naively generated DOCX tests the *recorded one page* path, not the missing-page-count path.
The generator strips that property on purpose. Word always records a count; files produced by
software often do not, and the page check is skipped when it is absent.

[`accept_unicode_mixed.txt`](files/accept_unicode_mixed.txt) is the one file that is not
purely English, and section 1 of the conventions above explains why. Its body is Tagore's own
prose with its genuine curly quotes and em dashes. The three-line header is **authored**, not
sourced — Project Gutenberg's Indian texts are all romanised, so there was no Devanagari to
borrow. It reads:

| Line | Language | Meaning |
|---|---|---|
| यह एक परीक्षण फ़ाइल है। | Hindi | "This is a test file." |
| これはテストファイルです。 | Japanese | "This is a test file." |
| Dies ist eine Testdatei für die Zeichenkodierung — äöüß. | German | "This is a test file for character encoding." |

Three scripts and an umlaut in one file turn a latin-1 misread from a subtle corruption into
an obvious one.

## A2 — Accepted: Indian-context corpus

The same format coverage over a completely different body of writing. A retrieval test on a
single-subject corpus can look healthy while the index quietly returns whatever sits nearest
the centre of that one topic; two unrelated subjects make the failure visible.

**These are Indian works in English** — Tagore's own English translation of his poetry,
Arnold's English verse translation of the Gita, and Jacobs' English retellings of Indian folk
tales. Indian subject matter, English text, so they can be read, quoted and asserted on
directly.

| File | What it tests | Source |
|---|---|---|
| [accept_gitanjali_3page.pdf](files/accept_gitanjali_3page.pdf) | Multi-page PDF of poetry — short lines, heavy punctuation | Tagore, *Gitanjali* (1912) |
| [accept_bhagavad_gita.docx](files/accept_bhagavad_gita.docx) | DOCX recording 4 pages; verse layout | Arnold, *The Song Celestial* (1885) |
| [accept_indian_fairy_tales.txt](files/accept_indian_fairy_tales.txt) | ~40,000 characters of narrative prose | Jacobs, *Indian Fairy Tales* (1892) |
| [accept_gitanjali.md](files/accept_gitanjali.md) | Markdown with generated headings | Tagore |
| [accept_bhagavad_gita.html](files/accept_bhagavad_gita.html) | HTML extraction over verse | Arnold |

Provenance and copyright status: [sources/SOURCES.md](sources/SOURCES.md).

## A3 — Accepted: visual content

Text extraction is blind to pictures. These files make that blindness measurable instead of
leaving it as an assumption.

| File | What it tests | Expected |
|---|---|---|
| [accept_text_and_chart.pdf](files/accept_text_and_chart.pdf) | Text layer **and** an embedded bar chart — the ordinary report or datasheet | 200 → prose retrievable, the chart's numbers **not** *(needs re-verification — see note below)* |
| [accept_docx_with_image.docx](files/accept_docx_with_image.docx) | DOCX with an embedded image | 200 → the picture is silently dropped *(needs re-verification — see note below)* |
| [accept_table_layout.pdf](files/accept_table_layout.pdf) | A ruled 6×3 table | 200 → cells arrive as loose fragments with no row or column structure |
| [workerfail_scanned_no_text_layer.pdf](files/workerfail_scanned_no_text_layer.pdf) | Image-only PDF — see section C | 200 at upload, then failed |

They form a deliberate ladder, and the middle rungs are the dangerous ones:

- The **scan** fails loudly. Zero characters extracted, the document ends up failed, somebody
  notices.
- The **chart** and the **image in the DOCX** fail *silently*. Plenty of text is extracted,
  the document indexes, everything looks fine — and the content of the figure is simply gone.
  Ask "which source text is the longest?" and the answer is in the picture, which means it is
  not in the index. `--verify` asserts exactly this: the prose comes back, the chart's labels
  do not.
- The **table** is subtlest. The cells *are* extracted, so the data is present, but the grid
  that gave it meaning is not. "Gitanjali" and "1912" both survive; that they belong together
  does not necessarily.

**These two "silent" rows predate auto-detection and are now questionable, not confirmed wrong.**
`fs-agent-workers` shipped per-page PDF auto-shift 2026-09-01 (`classify_pdf_target_mode`) and
document-level DOCX auto-shift 2026-09-02 (`classify_docx_target_mode`) — both apply under the
default mode (`auto`, formerly `text_based`), which is what these fixtures get when uploaded with
no `document_processing_mode` specified, same as everything else in this set. Mechanically:
`accept_docx_with_image.docx` almost certainly has `doc.inline_shapes` count > 0, so it should now
auto-shift to `visual` and actually attempt to read the picture (success depends on whether the
LlamaParse tier can); `accept_text_and_chart.pdf`'s outcome depends on the chart's actual image
coverage ratio landing in the auto-shift's visual band, which nobody has measured against this
specific file. Neither claim has been re-verified against a live run since those features shipped
— treat "silently dropped" as this page's *pre-auto-detection* claim until `--verify` or a live
upload confirms the current behavior one way or the other, and update this row (and
`generate_fixtures.py --verify`'s corresponding check) once it has.

The chart's numbers are measured from the corpus at build time and the table's rows are
bibliographic facts, so both state something true and can be checked against the sources.

## A4 — The weblink source: [files/site/](files/site/)

A weblink is not a file — it is fetched over HTTP, so it cannot be a fixture in the same
sense. What *can* be committed is a website with a known link structure, served locally, so a
crawl has a right answer to be measured against.

```bash
cd tests/fixtures/files/site && python -m http.server 8899
# then ingest http://localhost:8899/index.html with a crawl depth of 0, 1, or 2
```

| Depth | Pages reachable | Which |
|---|---|---|
| 0 | 1 | [index.html](files/site/index.html) |
| 1 | 4 | index + [declaration](files/site/declaration.html), [gitanjali](files/site/gitanjali.html), [tales](files/site/tales.html) |
| 2 | 6 | the above + [constitution](files/site/constitution.html), [tales-more](files/site/tales-more.html) |
| — | never | [deep.html](files/site/deep.html) sits at depth 3 |

[`deep.html`](files/site/deep.html) exists to be *absent*. Depth limits are the kind of thing
that looks correct because nothing crashes, and a crawl that quietly went one level too deep
costs real money at scale. If `deep.html` turns up in an indexed corpus, the limit was not
honoured. `--verify` re-derives every number in that table by walking the link graph, so
adding a link to a page cannot silently invalidate the test.

### Weblink request scenarios

These need no fixture file — they are request shapes:

| Request | Expected |
|---|---|
| A crawl depth of `3`, or `-1` | 400 `max_depth must be between 0 and 2 (inclusive)` |
| A weblink under the restricted documentation host | 400 `Weblink contains a restricted URL.` |
| No source at all — no file, text, weblink or file URL | 400 `No valid data provided. Please provide either a file, text, or weblink.` |
| **Several sources at once** (a file *and* a weblink) | accepted today — see below |

That last row is a genuine mismatch between the documentation and the behaviour. The
ingestion docs describe the rule as "exactly one" source, but the check that runs is "at
least one", and each source supplied is then processed in turn. Worth confirming which was
intended before writing a test that asserts either.

## B — Rejected at upload (400)

The messages below are returned verbatim. They are worth asserting on exactly, because a
change in wording is a change in the contract for anyone showing the message to a user.

| File | What it tests | Expected message |
|---|---|---|
| [reject_11page.pdf](files/reject_11page.pdf) | 11-page PDF, one over the cap | `Document has 11 pages, which exceeds the 10-page limit.` |
| [reject_11page.docx](files/reject_11page.docx) | DOCX recording 11 pages | `Document has 11 pages, which exceeds the 10-page limit.` |
| [reject_text_over_char_limit.txt](files/reject_text_over_char_limit.txt) | 100,001 characters, one over the cap | `Document has 100,001 characters, which exceeds the 100,000-character limit.` |
| [reject_markdown_over_char_limit.md](files/reject_markdown_over_char_limit.md) | The same, as Markdown | `Document has 100,001 characters, which exceeds the 100,000-character limit.` |
| [reject_corrupt.pdf](files/reject_corrupt.pdf) | Starts like a PDF, then has no cross-reference table or trailer | `Could not process the provided PDF file. It may be corrupt.` |
| [reject_not_a_docx.docx](files/reject_not_a_docx.docx) | A `.docx` extension on plain text — not a zip archive at all | `File is not a valid DOCX document.` |
| [reject_unsupported_type.csv](files/reject_unsupported_type.csv) | Sent as `text/csv`, which is not an accepted type | `Unsupported file type` |
| `reject_oversize_6mb.txt` | 6 MB — **not committed**, see below | `File too large. Maximum size is 5MB.` |

### The 6 MB fixture is not in the repository

Committing a multi-MB file to test a 5 MB limit would make every clone of this repository
permanently heavier, forever, because git keeps it in history even after deletion. Build it
when you need it:

```bash
python tests/fixtures/generate_fixtures.py --include-oversize
```

It is in `.gitignore` so it cannot be committed by accident.

## C — Accepted at upload, failed during processing

Upload returns 200. Poll the document's status endpoint until it reports `failed`, then
assert on the reason.

| File | What it tests | Expected |
|---|---|---|
| [workerfail_empty.txt](files/workerfail_empty.txt) | Zero bytes | failed — below the 10-character minimum |
| [workerfail_too_short_9chars.txt](files/workerfail_too_short_9chars.txt) | 9 characters, one under the minimum | failed — below the 10-character minimum |
| [workerfail_scanned_no_text_layer.pdf](files/workerfail_scanned_no_text_layer.pdf) | Image-only PDF: the words are pixels, extraction yields nothing | failed — unless the request opts into richer parsing or OCR |
| [workerfail_silent_tone.wav](files/workerfail_silent_tone.wav) | A 440 Hz tone, no speech | failed — transcription returns an empty string |
| [workerfail_silent_tone.mp3](files/workerfail_silent_tone.mp3) | The same tone as `audio/mpeg` | failed — same reason |
| [workerfail_silent_tone.aac](files/workerfail_silent_tone.aac) | The same tone via the `.aac` extension | failed — same reason |
| [workerfail_silent_tone.ogg](files/workerfail_silent_tone.ogg) | The same tone as `audio/ogg` | failed — same reason |
| [workerfail_silent_tone.mpeg](files/workerfail_silent_tone.mpeg) | The same tone as `video/mpeg` | failed — same reason |
| [workerfail_octet_stream_not_md.csv](files/workerfail_octet_stream_not_md.csv) | Octet-stream with a non-`.md` filename | failed — no reader matches |

The exact wording of the minimum-length failure comes from the processing pipeline rather
than from this service, so it is not quoted here — assert that the status is `failed` and
treat the message as informative.

### The CSV pair is one file with two outcomes

[`reject_unsupported_type.csv`](files/reject_unsupported_type.csv) and
[`workerfail_octet_stream_not_md.csv`](files/workerfail_octet_stream_not_md.csv) hold
**identical bytes**. What differs is the content type the client sends, and that alone
decides which of two very different things happens:

- as `text/csv` — rejected in milliseconds, nothing stored;
- as `application/octet-stream` — accepted, stored, queued, and only then found to be
  unreadable, because that type is only readable with a `.md` filename.

Worth knowing because browsers and SDKs guess content types inconsistently. Note also that
the type check only runs when the client sends a content type at all — one that sends none
skips it.

## D — Retrieval fixtures

The accepted files double as the corpus for testing search, chunking and chat, because their
contents are real and quotable. Facts that appear in exactly one place:

| Question | Where the answer lives | Answer in the source |
|---|---|---|
| Minimum age for a Representative? | `accept_constitution_*` | "the Age of twenty five Years, and been seven Years a citizen" |
| Minimum age for a Senator? | `accept_constitution_*` | "the Age of thirty Years, and been nine Years a Citizen" |
| What counts as treason? | `accept_constitution_*` | "levying War against them, or in adhering to their Enemies, giving them Aid and Comfort" |
| What rights are named as unalienable? | `accept_declaration.*` | "Life, Liberty and the pursuit of Happiness" |
| Who wrote *Gitanjali*, and when? | [accept_table_layout.pdf](files/accept_table_layout.pdf) | Rabindranath Tagore, 1912 — but read out of a table, not a sentence |
| Which source text is the longest? | [accept_text_and_chart.pdf](files/accept_text_and_chart.pdf) | in the **chart**, so the honest expected answer is *the index cannot say* |

Two properties make these good test queries. The wording is fixed, so an assertion can match
a substring rather than judging a paraphrase. And the same fact appears in the PDF, DOCX,
TXT, MD and HTML versions of the same text — so if a query answers correctly from the TXT and
not from the DOCX, the problem is in extraction, not in retrieval. That is a much sharper
signal than "search seems worse today".

The last two rows are the interesting ones, because they have no plain-prose equivalent. They
are how you find out whether the pipeline is losing tables and figures.

---

## Rebuilding

```bash
python tests/fixtures/generate_fixtures.py            # rebuild the committed set
python tests/fixtures/generate_fixtures.py --verify   # rebuild, then run all 36 checks
```

`--verify` re-counts pages, re-reads the DOCX page property, re-measures every character
boundary, confirms the visual fixtures extract — and fail to extract — what they claim, walks
the fixture site's link graph, and checks that every accepted content type still has a
fixture. Run it after touching the generator, and after any change to the ingestion limits.

Requires `reportlab`, `python-docx`, `pillow` and `pdfminer.six`. `imageio-ffmpeg` is
optional and only needed to re-encode the audio; without it those files are left as committed
and everything else still builds.

**Rebuilding is byte-stable.** PDFs and DOCX files normally embed the moment they were
created, which meant every rebuild rewrote a dozen binaries whether or not anything had
changed — and a real edit then arrived in a diff full of identical-looking noise. All
timestamps are pinned to 2000-01-01, and the audio encoder is run in its reproducible mode
(it otherwise stamps its own version and, for Ogg, a random stream serial). So `git status`
after a rebuild is empty unless the content genuinely moved. If you do see churn, something
changed.

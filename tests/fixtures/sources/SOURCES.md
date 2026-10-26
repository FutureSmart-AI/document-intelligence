---
title: Fixture corpus — provenance
covers: [tests/fixtures/sources/]
updated: 2026-08-24
---

# Where the fixture text comes from

Every fixture in `tests/fixtures/files/` is built from the six texts in this folder. They
were chosen for two reasons: they are unambiguously in the public domain, and they are real
prose. Real prose matters more than it sounds — a corpus of `lorem ipsum` or repeated
`aaaa` would sail through the size and page checks but tell you nothing about whether
chunking, embedding, and retrieval actually work, because there is nothing in it to
retrieve.

Half the corpus is Indian and half is American. That is not decoration: a retrieval test
over a single-domain corpus can look healthy while the index is quietly returning
whatever is nearest the centre of one topic. Two unrelated bodies of writing make a wrong
answer visible — a query about Tagore that returns the Constitution is obviously wrong in
a way that a query about the Constitution returning a different article is not.

## Indian texts

| File | Work | Why it is public domain | Retrieved from |
|---|---|---|---|
| `gitanjali-tagore.txt` | *Gitanjali* — Rabindranath Tagore's own English prose translation (1912), the work that won the 1913 Nobel Prize in Literature | Published 1912; Tagore died 1941, so it is out of copyright both in India (life + 60 years) and in the United States | Project Gutenberg eBook #7164 |
| `song-celestial-bhagavad-gita.txt` | *The Song Celestial*, Sir Edwin Arnold's 1885 verse translation of the Bhagavad Gita | Published 1885; translator died 1904 | Project Gutenberg eBook #2388 |
| `indian-fairy-tales-jacobs.txt` | *Indian Fairy Tales*, collected by Joseph Jacobs (1892), first ~150,000 characters | Published 1892; collector died 1916 | Project Gutenberg eBook #7128 |

**A caveat worth stating plainly:** all three are in English or romanised, because Project
Gutenberg's plain-text editions of Indian works are. The corpus therefore gives Indian
*subject matter*, not Indian *scripts*. If a test needs real Devanagari — for encoding,
tokenisation, or embedding quality on Indic scripts — this corpus will not supply it, and
the three-script header in `accept_unicode_mixed.txt` is authored rather than sourced. See
the README's note on that file.

## American texts

| File | Work | Why it is public domain | Retrieved from |
|---|---|---|---|
| `declaration-of-independence.txt` | The Declaration of Independence (1776) | A United States federal government work; also long out of copyright | Project Gutenberg eBook #1 |
| `us-constitution.txt` | The Constitution of the United States, with amendments | Same | Project Gutenberg eBook #5 |
| `federalist-papers-excerpt.txt` | The Federalist Papers (1787–88), first ~299,000 characters | Published 1787–88, long out of copyright | Project Gutenberg eBook #1404 |

## What was changed on the way in

- The Project Gutenberg header and footer were removed, keeping only the text between the
  `*** START OF THE PROJECT GUTENBERG EBOOK ***` and `*** END ... ***` markers. Project
  Gutenberg's own licence says that once its header is stripped from a work that is itself
  in the public domain in the United States, the text carries no Gutenberg restrictions.
- Line endings were normalised from CRLF to LF.
- `federalist-papers-excerpt.txt` was truncated at a paragraph boundary near 300,000
  characters. The full work is about 1.2 MB, and nothing in the fixture set needs more than
  three times the 100,000-character limit.
- `indian-fairy-tales-jacobs.txt` was truncated the same way near 150,000 characters.

Nothing else was edited. The wording is the source wording, so a fixture can be quoted in a
search assertion and checked against the original.

## Rebuilding the corpus

The corpus is committed rather than downloaded at test time, so the fixtures can be rebuilt
offline and so a change to an upstream file cannot silently change what the tests assert. If
it ever needs refreshing:

```bash
for id in 1 5 1404 7164 2388 7128; do
  curl -o "pg$id.txt" "https://www.gutenberg.org/cache/epub/$id/pg$id.txt"
done
# then strip the header/footer as described above
```

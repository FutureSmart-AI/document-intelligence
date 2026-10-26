# Synthetic invoice sample collection

18 fictional supplier invoices for the Document Intelligence "try a sample collection" experience. Six vendors, three
layouts (header band, classic, compact), three currencies (USD, EUR, INR), one to four line items each, with and
without tax. The buyer on every invoice is Cedar Lane Retail, the made-up shop of the Database Agent sample.

**Everything here is invented.** No real company, person or address appears; emails use the reserved
`example.com` / `example.org` / `example.net` domains, and each invoice says it is a sample in its footer.

| Path | What it is |
|---|---|
| `build.py` | The single source of truth: the invoices are written out in it. Regenerates everything below. |
| `pdf/*.pdf` | The 18 invoices, one page each. **This is what gets ingested.** |
| `html/*.html` | The rendered source of each PDF. |
| `ground_truth.json` | Per invoice, the values the `invoice` field template should extract. |
| `questions.json` | Candidate suggested questions, answers computed from `ground_truth.json`. |
| `sample.json` | The provisioning manifest (`scripts/provision_sample.py`). Only questions verified against the hosted sample go in its `suggested_questions`. |

Rebuild: `python build.py` (needs Chrome; `CHROME=/path/to/chrome` to point at one).

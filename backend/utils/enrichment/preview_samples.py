"""Bundled example documents for the preview run's ``sample_key`` (CC6, Q6).

Step 3 of the create-collection wizard lets a user try their fields without
uploading anything of their own ("use a sample document"). These are those
documents.

Two rules they all follow, and both matter:

- **Synthetic. No real people, no real companies, no real customer data.** Every
  name, address, email, amount and identifier below is invented. A bundled sample
  is shown to every user of the product, so a real resume or a real invoice
  leaking in here would be a privacy incident, not a mistake in a test fixture.
- **Plain text, held in this module.** No S3 object, no file on disk, no parser
  call — the preview run's whole point is that it stores nothing and costs only
  the LLM call. A sample skips the document-reading step entirely.

Adding one later is a new entry in ``PREVIEW_SAMPLES`` and nothing else; the
endpoint reads the dict, so the key list it advertises stays in sync on its own.
Keys deliberately match ``FieldTemplate`` values (utils/models/enums.py) so the
step-1 document-type picker and the step-3 sample picker line up.
"""

from typing import Dict, List, Optional, TypedDict


class PreviewSample(TypedDict):
    """One bundled example: what to call it, and the text a preview runs against."""
    file_name: str
    display_name: str
    text: str


# Key -> sample. Keys match FieldTemplate values (resume / invoice /
# support_ticket) so a wizard that has already picked a document type can offer
# the matching sample without a second mapping table.
PREVIEW_SAMPLES: Dict[str, PreviewSample] = {
    "resume": {
        "file_name": "sample_resume.txt",
        "display_name": "Example resume",
        "text": """ANANYA RAO
Senior Backend Engineer
ananya.rao@example.invalid | +91 90000 00000 | Pune, India

SUMMARY
Backend engineer with 7 years building payment and data-ingestion services.
Comfortable owning a service end to end, from schema design to on-call.

EXPERIENCE

Northwind Data Systems - Senior Backend Engineer
March 2022 - present, Pune
- Led the rewrite of the invoice ingestion pipeline; cut median processing time
  from 40 seconds to 6 seconds per document.
- Designed the multi-tenant Postgres schema now serving 1,200 customer accounts.
- Mentored three junior engineers.

Bluepeak Software - Backend Engineer
July 2019 - February 2022, Bengaluru
- Built and maintained the billing reconciliation service (Python, Celery).
- Introduced contract tests between the billing and ledger services.

Corvus Analytics - Software Engineer
August 2017 - June 2019, Bengaluru
- Maintained an internal reporting API used by the finance team.

EDUCATION
B.E. Computer Science, Deccan Institute of Technology, 2017

SKILLS
Python, Go, Kubernetes, PostgreSQL, AWS, Celery, Redis, Terraform

LANGUAGES
English, Hindi, Marathi

NOTICE PERIOD
60 days
""",
    },
    "invoice": {
        "file_name": "sample_invoice.txt",
        "display_name": "Example invoice",
        "text": """TAX INVOICE

Harborlight Supplies Pvt Ltd
14 Marine Approach Road, Kochi 682001, India
GSTIN: 32AAAAA0000A1Z5

Invoice number: HLS-2026-00418
Invoice date: 12 March 2026
Due date: 11 April 2026
Purchase order reference: PO-48213
Payment terms: Net 30

BILL TO
Fairmount Interiors LLP
Unit 7, Ashgrove Business Park
Bengaluru 560103, India
GSTIN: 29BBBBB1111B1Z4

LINE ITEMS
Description                       Qty   Unit price      Amount
Oak veneer panel, 2400x1200         18     4,250.00     76,500.00
Brushed steel handle, 160mm         60        310.00     18,600.00
Delivery and handling                1      2,400.00      2,400.00

Subtotal                                              97,500.00
CGST 9%                                                8,775.00
SGST 9%                                                8,775.00
Total due (INR)                                      115,050.00

Remit to: Harborlight Supplies Pvt Ltd
Account 000011112222, IFSC EXAM0000123, Example Bank, Kochi

Late payments accrue interest at 1.5% per month.
""",
    },
    "support_ticket": {
        "file_name": "sample_support_ticket.txt",
        "display_name": "Example support ticket",
        "text": """SUPPORT TICKET #SR-77412

Opened: 2026-02-04 09:18 IST
Channel: Email
Priority: High
Status: Resolved
Product area: Data export
Assigned to: Tier 2 - Reporting

Reported by: Devika Menon, Operations Lead, Fairmount Interiors LLP
Contact: devika.menon@example.invalid

SUBJECT
Scheduled CSV export arrives empty since Monday

DESCRIPTION
Our nightly export of completed orders has produced a 0-byte file every night
since 2026-02-02. The job reports success in the dashboard. Running the same
export manually from the UI works and returns 4,812 rows, so the data is there.
This blocks the finance handover every morning.

RESOLUTION
The scheduled job was still pointing at a saved view that was deleted on
2026-02-01. Repointed the schedule at the current "Completed orders" view and
re-ran the last three nights. Confirmed with the customer on 2026-02-05.

Time to first response: 41 minutes
Time to resolution: 28 hours
Customer satisfaction rating: 4 of 5
""",
    },
}


def available_sample_keys() -> List[str]:
    """The keys a caller may send as ``sample_key``, for error messages and docs."""
    return sorted(PREVIEW_SAMPLES.keys())


def get_sample(sample_key: str) -> Optional[PreviewSample]:
    """One bundled sample, or None if the key is unknown."""
    return PREVIEW_SAMPLES.get((sample_key or "").strip().lower())

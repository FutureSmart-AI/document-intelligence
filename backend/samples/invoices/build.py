"""Build the synthetic invoice sample: html/, pdf/, ground_truth.json, questions.json.

Every vendor, customer, address, invoice and amount here is invented. The buyer on
every invoice is Cedar Lane, the made-up shop of the Database Agent's sample data.
Emails use reserved example domains. Nothing is random: the invoices are written out
below, and the answers to the suggested questions are computed from them, so a
rebuild is byte-for-byte reproducible apart from the PDF renderer's metadata.

    python build.py              # html + pdf (needs Chrome or Chromium)
    python build.py --no-pdf     # html + json only
    CHROME=/path/to/chrome python build.py
"""

import argparse
import html
import json
import os
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent

CUSTOMER = {
    "name": "Cedar Lane Retail Inc.",
    "lines": ["Accounts Payable", "1200 Larkspur Avenue, Suite 400", "Portland, OR 97205, USA", "ap@cedarlane.example.com"],
}

VENDORS = {
    "bramblecloud": {
        "name": "Bramblecloud Hosting Ltd.",
        "lines": ["44 Wharf Road", "Dublin D02 X285, Ireland", "billing@bramblecloud.example.net"],
        "tax_label": "VAT", "tax_rate": 0.0, "layout": "band",
    },
    "lumenridge": {
        "name": "Lumen Ridge Analytics LLC",
        "lines": ["310 Mercer Street, Floor 6", "Seattle, WA 98109, USA", "invoices@lumenridge.example.com"],
        "tax_label": "Sales tax", "tax_rate": 0.0, "layout": "classic",
    },
    "copperleaf": {
        "name": "Copperleaf Office Supply Co.",
        "lines": ["9 Industrial Park Drive", "Beaverton, OR 97005, USA", "orders@copperleaf.example.org"],
        "tax_label": "Sales tax", "tax_rate": 0.0, "layout": "compact",
    },
    "harborpine": {
        "name": "Harbor & Pine Consulting",
        "lines": ["2nd Floor, 17 Residency Road", "Bengaluru 560025, India", "accounts@harborpine.example.com"],
        "tax_label": "GST", "tax_rate": 0.18, "layout": "classic",
    },
    "tessellate": {
        "name": "Tessellate Design Studio GmbH",
        "lines": ["Kastanienallee 12", "10435 Berlin, Germany", "rechnung@tessellate.example.org"],
        "tax_label": "USt", "tax_rate": 0.19, "layout": "band",
    },
    "quillmark": {
        "name": "Quillmark Software Licensing",
        "lines": ["800 Congress Avenue", "Austin, TX 78701, USA", "billing@quillmark.example.net"],
        "tax_label": "Sales tax", "tax_rate": 0.0, "layout": "compact",
    },
}

# (vendor, number, currency, issue, due, terms, [(description, category, qty, unit_price)])
INVOICES = [
    ("bramblecloud", "BCH-2026-0418", "EUR", "2026-04-30", "2026-05-30", "net 30", [
        ("Managed Kubernetes cluster, April", "cloud infrastructure", 1, 2400.00),
        ("Object storage, 18 TB-month", "cloud infrastructure", 18, 21.50),
        ("Premium support plan", "support", 1, 450.00)]),
    ("bramblecloud", "BCH-2026-0519", "EUR", "2026-05-31", "2026-06-30", "net 30", [
        ("Managed Kubernetes cluster, May", "cloud infrastructure", 1, 2400.00),
        ("Object storage, 21 TB-month", "cloud infrastructure", 21, 21.50),
        ("Premium support plan", "support", 1, 450.00)]),
    ("bramblecloud", "BCH-2026-0620", "EUR", "2026-06-30", "2026-07-30", "net 30", [
        ("Managed Kubernetes cluster, June", "cloud infrastructure", 1, 2650.00),
        ("Object storage, 24 TB-month", "cloud infrastructure", 24, 21.50),
        ("Premium support plan", "support", 1, 450.00),
        ("Data egress overage", "cloud infrastructure", 1, 312.40)]),
    ("lumenridge", "LRA-1107", "USD", "2026-03-15", "2026-04-14", "net 30", [
        ("Customer churn model, discovery phase", "consulting", 40, 165.00),
        ("Analytics dashboard licences (10 seats)", "software licenses", 10, 49.00)]),
    ("lumenridge", "LRA-1152", "USD", "2026-06-15", "2026-07-15", "net 30", [
        ("Customer churn model, build and validation", "consulting", 96, 165.00),
        ("Analytics dashboard licences (10 seats)", "software licenses", 10, 49.00)]),
    ("lumenridge", "LRA-1189", "USD", "2026-09-15", "2026-10-15", "milestone-based", [
        ("Churn model go-live milestone", "consulting", 1, 9800.00),
        ("Analytics dashboard licences (14 seats)", "software licenses", 14, 49.00),
        ("Team training workshop", "training", 1, 1200.00)]),
    ("copperleaf", "CO-55120", "USD", "2026-02-03", "2026-02-03", "due on receipt", [
        ("Printer paper, A4, 20 boxes", "office supplies", 20, 38.75),
        ("Toner cartridges, black", "office supplies", 8, 64.90),
        ("Desk organisers", "office supplies", 15, 12.40)]),
    ("copperleaf", "CO-55388", "USD", "2026-05-12", "2026-05-12", "due on receipt", [
        ("Ergonomic office chairs", "furniture", 6, 289.00),
        ("Printer paper, A4, 10 boxes", "office supplies", 10, 38.75)]),
    ("copperleaf", "CO-55701", "USD", "2026-08-21", "2026-09-05", "net 15", [
        ("Standing desks", "furniture", 4, 545.00),
        ("Monitor arms", "furniture", 8, 89.50),
        ("Toner cartridges, colour set", "office supplies", 4, 182.00)]),
    ("harborpine", "HPC/26-27/014", "INR", "2026-04-10", "2026-05-10", "net 30", [
        ("Warehouse process review, 6 days", "consulting", 6, 72000.00),
        ("Travel and accommodation", "travel", 1, 38500.00)]),
    ("harborpine", "HPC/26-27/039", "INR", "2026-07-08", "2026-08-07", "net 30", [
        ("Inventory system rollout support, 10 days", "consulting", 10, 72000.00)]),
    ("harborpine", "HPC/26-27/061", "INR", "2026-10-05", "2026-11-04", "net 30", [
        ("Peak-season staffing plan, 4 days", "consulting", 4, 72000.00),
        ("Travel and accommodation", "travel", 1, 21750.00)]),
    ("tessellate", "TDS-2026-031", "EUR", "2026-03-20", "2026-04-19", "net 30", [
        ("Brand refresh, concept phase", "design services", 1, 6500.00),
        ("Stock image licences", "software licenses", 12, 35.00)]),
    ("tessellate", "TDS-2026-058", "EUR", "2026-06-12", "2026-07-12", "milestone-based", [
        ("Brand refresh, final identity", "design services", 1, 9200.00),
        ("Packaging design, 3 product lines", "design services", 3, 1450.00)]),
    ("tessellate", "TDS-2026-077", "EUR", "2026-09-30", "2026-10-30", "net 30", [
        ("Holiday campaign artwork", "design services", 1, 4800.00)]),
    ("quillmark", "QSL-88412", "USD", "2026-01-31", "2026-03-02", "net 30", [
        ("Point-of-sale software, annual licence (12 stores)", "software licenses", 12, 1180.00),
        ("Implementation support", "support", 16, 140.00)]),
    ("quillmark", "QSL-89037", "USD", "2026-07-31", "2026-08-30", "net 30", [
        ("Point-of-sale software, 3 additional stores (prorated)", "software licenses", 3, 590.00),
        ("Inventory add-on module, annual", "software licenses", 1, 4200.00)]),
    ("quillmark", "QSL-89590", "USD", "2026-10-15", "2026-11-14", "net 30", [
        ("Premium support upgrade, annual", "support", 1, 3600.00),
        ("Cloud backup for POS data, annual", "cloud infrastructure", 1, 1980.00)]),
]

SYMBOL = {"USD": "$", "EUR": "€", "INR": "₹"}


def money(value: float, currency: str) -> str:
    return f"{SYMBOL[currency]}{value:,.2f}"


def esc(text) -> str:
    return html.escape(str(text))


def long_date(iso: str) -> str:
    import datetime as dt
    d = dt.date.fromisoformat(iso)
    return d.strftime("%d %B %Y").lstrip("0")


def totals(items, rate):
    subtotal = round(sum(q * p for _, _, q, p in items), 2)
    tax = round(subtotal * rate, 2)
    return subtotal, tax, round(subtotal + tax, 2)


STYLE = """
body{font-family:Helvetica,Arial,sans-serif;color:#1f2933;margin:0;font-size:12.5px}
.page{padding:36px 44px}
h1{margin:0;font-size:28px;letter-spacing:.04em}
.muted{color:#52606d}.small{font-size:11px}
table{width:100%;border-collapse:collapse;margin-top:18px}
th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:#52606d;border-bottom:2px solid #1f2933;padding:6px 4px}
td{padding:7px 4px;border-bottom:1px solid #e4e7eb;vertical-align:top}
td.n,th.n{text-align:right}
.tot{margin-left:auto;width:300px;margin-top:12px}
.tot td{border:0;padding:3px 4px}.tot tr.grand td{font-weight:700;font-size:15px;border-top:2px solid #1f2933}
.cols{display:flex;justify-content:space-between;gap:24px;margin-top:22px}
.band{background:#0f3d3e;color:#fff;padding:26px 44px;display:flex;justify-content:space-between;align-items:flex-end}
.band .muted{color:#cde3e2}
.compact{font-size:11.5px}.compact .page{padding:26px 34px}
.fiction{margin-top:26px;font-size:10px;color:#9aa5b1}
"""


def render(inv) -> str:
    vendor_key, number, currency, issue, due, terms, items = inv
    v = VENDORS[vendor_key]
    subtotal, tax, total = totals(items, v["tax_rate"])
    rows = "".join(
        f"<tr><td>{esc(d)}</td><td class='n'>{q:g}</td><td class='n'>{money(p, currency)}</td>"
        f"<td class='n'>{money(q * p, currency)}</td></tr>" for d, _, q, p in items)
    vendor_block = f"<b>{esc(v['name'])}</b><br>" + "<br>".join(esc(x) for x in v["lines"])
    customer_block = "<span class='muted small'>BILL TO</span><br><b>" + esc(CUSTOMER["name"]) + "</b><br>" + \
        "<br>".join(esc(x) for x in CUSTOMER["lines"])
    meta = (f"<div><span class='muted'>Invoice no.</span> <b>{esc(number)}</b><br>"
            f"<span class='muted'>Issue date</span> {long_date(issue)}<br>"
            f"<span class='muted'>Due date</span> {long_date(due)}<br>"
            f"<span class='muted'>Terms</span> {esc(terms.capitalize())}<br>"
            f"<span class='muted'>Currency</span> {currency}</div>")
    tax_row = (f"<tr><td>{esc(v['tax_label'])} ({v['tax_rate'] * 100:g}%)</td><td class='n'>{money(tax, currency)}</td></tr>"
               if v["tax_rate"] else f"<tr><td>{esc(v['tax_label'])}</td><td class='n'>{money(0, currency)}</td></tr>")
    body = (f"<table><tr><th>Description</th><th class='n'>Qty</th><th class='n'>Unit price</th><th class='n'>Amount</th></tr>"
            f"{rows}</table><table class='tot'><tr><td>Subtotal</td><td class='n'>{money(subtotal, currency)}</td></tr>"
            f"{tax_row}<tr class='grand'><td>Total due</td><td class='n'>{money(total, currency)}</td></tr></table>"
            "<p class='fiction'>Sample document: every company, person and amount on this invoice is fictional.</p>")
    if v["layout"] == "band":
        page = (f"<div class='band'><div><h1>INVOICE</h1><div class='muted'>{esc(v['name'])}</div></div>{meta}</div>"
                f"<div class='page'><div class='cols'><div>{customer_block}</div><div class='small'>{vendor_block}</div></div>{body}</div>")
    else:
        page = (f"<div class='page'><div class='cols'><div>{vendor_block}</div><div><h1>INVOICE</h1>{meta}</div></div>"
                f"<div class='cols'><div>{customer_block}</div></div>{body}</div>")
    klass = "compact" if v["layout"] == "compact" else ""
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>{esc(number)}</title>"
            f"<style>{STYLE}</style></head><body class='{klass}'>{page}</body></html>")


def filename(inv) -> str:
    return f"{inv[0]}-{inv[1].replace('/', '-').lower()}"


def ground_truth(inv, i: int) -> dict:
    vendor_key, number, currency, issue, due, terms, items = inv
    v = VENDORS[vendor_key]
    _, _, total = totals(items, v["tax_rate"])
    categories = list(dict.fromkeys(c for _, c, _, _ in items))
    return {
        "id": f"i{i:02d}",
        "file": f"pdf/{filename(inv)}.pdf",
        "layout": v["layout"],
        "fields": {
            "vendor_name": v["name"],
            "customer_name": CUSTOMER["name"],
            "invoice_number": number,
            "total_amount": total,
            "currency": currency,
            "issue_date": issue,
            "due_date": due,
            "payment_terms": terms,
            "line_item_categories": categories,
            "line_items": [{"description": d, "quantity": q, "unit_price": p, "amount": round(q * p, 2)}
                           for d, _, q, p in items],
        },
    }


def build_questions(gt) -> list:
    f = [g["fields"] for g in gt]
    by_vendor = defaultdict(list)
    for x in f:
        by_vendor[x["vendor_name"]].append(x)
    eur = [{"invoice_number": x["invoice_number"], "vendor": x["vendor_name"], "total": x["total_amount"]}
           for x in f if x["currency"] == "EUR"]
    cloud = [{"invoice_number": x["invoice_number"], "vendor": x["vendor_name"]}
             for x in f if "cloud infrastructure" in x["line_item_categories"]]
    due_q4 = [{"invoice_number": x["invoice_number"], "vendor": x["vendor_name"], "due_date": x["due_date"]}
              for x in f if x["due_date"] >= "2026-10-01"]
    usd_by_vendor = {name: round(sum(x["total_amount"] for x in xs), 2)
                     for name, xs in by_vendor.items() if xs[0]["currency"] == "USD"}
    receipt = [x["invoice_number"] for x in f if x["payment_terms"] == "due on receipt"]
    return [
        {"id": "q01", "question": "Which invoices are billed in euros, and what is each one's total?",
         "answer_source": "structured_fields", "expected": {"invoices": eur, "count": len(eur)}},
        {"id": "q02", "question": "Which invoices include cloud infrastructure charges?",
         "answer_source": "structured_fields", "expected": {"invoices": cloud, "count": len(cloud)}},
        {"id": "q03", "question": "Which invoices are due on or after 1 October 2026?",
         "answer_source": "structured_fields", "expected": {"invoices": due_q4, "count": len(due_q4)}},
        {"id": "q04", "question": "Which invoices were due on receipt?",
         "answer_source": "structured_fields", "expected": {"invoices": receipt, "count": len(receipt)}},
        {"id": "q05", "question": "What is the total billed by each US-dollar vendor? Show it as a chart.",
         "answer_source": "structured_fields", "expected": {"totals_usd": usd_by_vendor}},
        {"id": "q06", "question": "What did Lumen Ridge Analytics bill for, invoice by invoice?",
         "answer_source": "document_text",
         "expected": {"invoices": [{"invoice_number": x["invoice_number"],
                                    "items": [li["description"] for li in x["line_items"]]}
                                   for x in by_vendor["Lumen Ridge Analytics LLC"]]}},
    ]


def find_chrome():
    for candidate in [os.environ.get("CHROME"), r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                      shutil.which("google-chrome"), shutil.which("chromium"), shutil.which("chrome")]:
        if candidate and Path(candidate).exists():
            return candidate
    return None


def print_pdf(chrome: str, src: Path, dst: Path) -> None:
    subprocess.run([chrome, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={dst}", src.resolve().as_uri()], check=True, capture_output=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-pdf", action="store_true")
    args = parser.parse_args()
    chrome = None if args.no_pdf else find_chrome()
    if not args.no_pdf and not chrome:
        raise SystemExit("Chrome not found: set CHROME or pass --no-pdf")
    html_dir, pdf_dir = HERE / "html", HERE / "pdf"
    html_dir.mkdir(exist_ok=True)
    pdf_dir.mkdir(exist_ok=True)
    gt = []
    for i, inv in enumerate(INVOICES, 1):
        base = filename(inv)
        src = html_dir / f"{base}.html"
        src.write_text(render(inv), encoding="utf-8")
        if chrome:
            print_pdf(chrome, src, pdf_dir / f"{base}.pdf")
        gt.append(ground_truth(inv, i))
    meta = {"template": "invoice", "count": len(gt), "customer": CUSTOMER["name"]}
    (HERE / "ground_truth.json").write_text(json.dumps({"meta": meta, "invoices": gt}, indent=2, ensure_ascii=False) + "\n",
                                            encoding="utf-8")
    qs = build_questions(gt)
    (HERE / "questions.json").write_text(json.dumps({"meta": {"computed_from": "ground_truth.json"}, "questions": qs},
                                                    indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"built {len(gt)} invoices, {len(qs)} questions, pdf={'yes' if chrome else 'no'}")


if __name__ == "__main__":
    main()

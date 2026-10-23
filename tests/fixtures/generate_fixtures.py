#!/usr/bin/env python3
"""
Rebuild every file under ``tests/fixtures/files/`` from the public-domain corpus in
``tests/fixtures/sources/``.

Why a generator instead of hand-made files
------------------------------------------
Several fixtures only mean something because of an *exact* number: a PDF with exactly
ten pages, a text file with exactly 100,000 characters. Those numbers are the
boundaries the service checks in ``utils/validation/file_validation_utils.py``. A file
that drifted to 99,998 characters would still look fine in a diff and would silently
stop testing the thing it was made to test. Generating them from a script means the
boundary is written down as code, and ``--verify`` can re-measure it at any time using
the very same readers the service uses.

Usage
-----
    python tests/fixtures/generate_fixtures.py              # rebuild the committed set
    python tests/fixtures/generate_fixtures.py --verify     # rebuild, then re-measure
    python tests/fixtures/generate_fixtures.py --include-oversize
        # additionally write the 6 MB file, which is deliberately NOT committed

Requires: reportlab, python-docx, pillow (and pdfminer.six for --verify).
Optional: imageio-ffmpeg, only to re-encode the audio fixtures — without it those files
are left as committed and everything else still builds.
"""

from __future__ import annotations

import argparse
import logging
import math
import shutil
import struct
import wave
import zipfile
from datetime import datetime
from io import BytesIO
from pathlib import Path

# ---------------------------------------------------------------------------
# The limits enforced by the service. Mirrored here, with the source of truth
# named, so a change on either side shows up as a mismatch rather than a
# quietly wrong fixture.
# utils/validation/file_validation_utils.py
# ---------------------------------------------------------------------------
MAX_PDF_PAGES = 10            # applies to PDF and to DOCX that declare a page count
MIN_LENGTH = 10               # characters; enforced in the worker, not at upload
MAX_FILE_SIZE = 5 * 1024 * 1024
MAX_TEXT_CHARS = 100_000      # text/plain, text/markdown, text/html, octet-stream only

ROOT = Path(__file__).resolve().parent
SOURCES = ROOT / "sources"
FILES = ROOT / "files"

# A fixed timestamp for everything that would otherwise record "now".
#
# Generated documents embed a creation time — PDFs in their metadata, DOCX files in both
# the core properties and every zip entry. With a live clock, rebuilding produces a
# byte-different file even when nothing changed, so a real edit arrives in a diff of a
# dozen identical-looking binaries and nobody can tell which one actually moved. Pinning
# the clock makes a rebuild a no-op unless the content genuinely changed.
#
# 2000-01-01 rather than the Unix epoch because the zip format cannot store a year before
# 1980, and because a date that is obviously a placeholder reads better than one that
# looks like a real authoring date.
EPOCH = datetime(2000, 1, 1, 0, 0, 0)
ZIP_EPOCH = (2000, 1, 1, 0, 0, 0)

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
log = logging.getLogger("fixtures")


# ---------------------------------------------------------------------------
# Corpus helpers
# ---------------------------------------------------------------------------
def source(name: str) -> str:
    """Read one public-domain source text. See sources/SOURCES.md for provenance."""
    return (SOURCES / name).read_text(encoding="utf-8")


def exact_chars(n: int, corpus: str) -> str:
    """
    Return exactly ``n`` characters of real prose, with no leading or trailing
    whitespace.

    The trailing-whitespace part matters more than it looks. The service measures
    ``len(text.strip())``, so a file padded out to 100,001 characters that happens to
    end in a newline would be measured as 100,000 and *pass* — testing the opposite of
    what its name claims. Stripping first and then slicing removes that trap.
    """
    body = corpus.strip()
    while len(body) < n:                       # repeat the corpus if we need more text
        body = body + "\n\n" + corpus.strip()

    out = body[:n]
    if out != out.strip():
        # The slice landed on a space or a newline. Trim that tail and put the missing
        # characters back as a full stop, so the file still ends on real punctuation and
        # the length stays exact.
        trimmed = out.rstrip()
        out = trimmed + "." * (n - len(trimmed))

    assert len(out) == n, f"wanted {n} chars, got {len(out)}"
    assert out == out.strip(), "fixture must not start or end with whitespace"
    return out


# ---------------------------------------------------------------------------
# Writers, one per format
# ---------------------------------------------------------------------------
def write_text(name: str, text: str) -> None:
    path = FILES / name
    path.write_text(text, encoding="utf-8")
    log.info("wrote %-52s %8d chars", name, len(text))


def write_pdf(name: str, pages: list[str], title: str) -> None:
    """
    Write a text-layer PDF with exactly ``len(pages)`` pages.

    Page count is controlled explicitly — one ``showPage()`` per entry — rather than
    left to text reflow, because reflow depends on font metrics and would make the
    ten-page boundary fragile.
    """
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.units import inch
    from reportlab.pdfgen import canvas

    path = FILES / name
    c = canvas.Canvas(str(path), pagesize=LETTER, invariant=1)
    c.setTitle(title)
    width, height = LETTER
    for page_no, body in enumerate(pages, start=1):
        text_obj = c.beginText(inch, height - inch)
        text_obj.setFont("Helvetica-Bold", 12)
        text_obj.textLine(f"{title} — page {page_no} of {len(pages)}")
        text_obj.setFont("Helvetica", 9)
        text_obj.textLine("")
        for line in _wrap(body, 95)[:60]:      # cap lines so nothing overflows the page
            text_obj.textLine(line)
        c.drawText(text_obj)
        c.showPage()                            # <- this, and only this, makes a page
    c.save()
    log.info("wrote %-52s %8d pages", name, len(pages))


def write_scanned_pdf(name: str, text: str, title: str) -> None:
    """
    Write an image-only PDF: the words are pixels, so no text layer exists.

    This is a real support case, not a contrived one. The old validator extracted text
    at upload time and rejected such a file as "too short" — a scan of a perfectly good
    document. That is precisely why the length check moved to the worker.
    """
    from PIL import Image, ImageDraw
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    img = Image.new("RGB", (1275, 1650), "white")     # 150 dpi Letter
    draw = ImageDraw.Draw(img)
    y = 60
    draw.text((60, y), title, fill="black")
    y += 40
    for line in _wrap(text, 90)[:70]:
        draw.text((60, y), line, fill="black")
        y += 20

    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    path = FILES / name
    c = canvas.Canvas(str(path), pagesize=LETTER, invariant=1)
    c.setTitle(title)
    c.drawImage(ImageReader(buf), 0, 0, width=LETTER[0], height=LETTER[1])
    c.showPage()
    c.save()
    log.info("wrote %-52s %8s", name, "1 page (image only)")


def _chart_png(rows: list[tuple[str, int]], title: str, unit: str) -> BytesIO:
    """
    Draw a labelled bar chart as a PNG.

    The numbers are real — they are measured from the corpus at build time — so the
    picture agrees with the text around it. A chart of invented figures would still
    exercise the image path, but it could not be used to check whether anything the
    model reads off the picture is correct.
    """
    from PIL import Image, ImageDraw

    width, height, pad, bar_h = 900, 90 + 60 * len(rows), 40, 32
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)
    draw.text((pad, 20), title, fill="black")

    biggest = max(v for _, v in rows) or 1
    y = 60
    for label, value in rows:
        bar_w = int((width - 2 * pad - 260) * value / biggest)
        draw.rectangle([pad + 260, y, pad + 260 + bar_w, y + bar_h], fill=(40, 90, 160))
        draw.text((pad, y + 10), label[:36], fill="black")
        draw.text((pad + 270 + bar_w, y + 10), f"{value:,} {unit}", fill="black")
        y += 60

    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


def write_pdf_with_image(name: str, text: str, title: str, chart: BytesIO) -> None:
    """
    Write a PDF that has both a real text layer and an embedded picture.

    This is the ordinary case for a report or a datasheet, and it is different from both
    fixtures either side of it: unlike a plain text PDF there is content the text
    extractor cannot see, and unlike the scanned fixture the extractor still returns
    plenty of text — so nothing fails, and the loss is silent. That silence is the point.
    A test can assert that the prose is retrievable and that the figure's numbers are not.
    """
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.units import inch
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    path = FILES / name
    c = canvas.Canvas(str(path), pagesize=LETTER, invariant=1)
    c.setTitle(title)
    width, height = LETTER

    text_obj = c.beginText(inch, height - inch)
    text_obj.setFont("Helvetica-Bold", 13)
    text_obj.textLine(title)
    text_obj.setFont("Helvetica", 9)
    text_obj.textLine("")
    for line in _wrap(text, 95)[:28]:
        text_obj.textLine(line)
    c.drawText(text_obj)

    c.drawImage(ImageReader(chart), inch, inch, width=width - 2 * inch,
                height=3.2 * inch, preserveAspectRatio=True, anchor="sw")
    c.showPage()
    c.save()
    log.info("wrote %-52s %8s", name, "text + image")


def write_pdf_with_table(name: str, title: str, header: list[str],
                         rows: list[list[str]], note: str) -> None:
    """
    Write a PDF containing a real ruled table.

    Tables are worth their own fixture because extraction flattens them. ``pdfminer``
    returns the cells in layout order with no notion of which row or column they came
    from, so "10" and "50 MB" arrive as loose fragments. Whether a downstream answer
    about a table is right is therefore a genuinely open question, and this file is how
    to ask it.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    path = FILES / name
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(str(path), pagesize=LETTER, title=title, invariant=1)
    table = Table([header] + rows, hAlign="LEFT", colWidths=[2.1 * inch, 1.3 * inch, 2.9 * inch])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2850A0")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#EEF2FA")]),
    ]))
    doc.build([
        Paragraph(title, styles["Heading1"]),
        Paragraph(note, styles["BodyText"]),
        Spacer(1, 12),
        table,
    ])
    log.info("wrote %-52s %8s", name, f"table {len(rows)}x{len(header)}")


def write_docx(name: str, paragraphs: list[str], title: str, declared_pages: int | None = None,
               image: BytesIO | None = None) -> None:
    """
    Write a DOCX. If ``declared_pages`` is given, inject ``docProps/app.xml`` so the file
    advertises that page count.

    Word writes that property; python-docx does not. The service reads it and, when it
    is absent, skips the page check entirely — so both cases are worth having as
    fixtures. See ``get_docx_page_count_from_bytes``.
    """
    from docx import Document

    path = FILES / name
    doc = Document()
    doc.add_heading(title, level=1)
    for para in paragraphs:
        doc.add_paragraph(para)
    if image is not None:
        from docx.shared import Inches
        doc.add_picture(image, width=Inches(6))

    # Pin the timestamps. Left alone, python-docx stamps "now" into the core properties
    # and the zip entries, so every rebuild produces a different file even when nothing
    # about it changed — which turns `git status` into noise and hides real changes among
    # a dozen fake ones.
    doc.core_properties.created = EPOCH
    doc.core_properties.modified = EPOCH
    doc.core_properties.revision = 1
    doc.save(path)
    _normalise_zip_timestamps(path)

    if declared_pages is None:
        # python-docx copies its own default app.xml, which claims Pages=1. Left alone,
        # this fixture would test the "declared 1 page" path, not the "no page count"
        # path it is named for — so the property has to be removed deliberately.
        _set_docx_page_count(path, None)
    else:
        _set_docx_page_count(path, declared_pages)
    log.info("wrote %-52s %8s", name,
             "no page count" if declared_pages is None else f"{declared_pages} pages")


def _normalise_zip_timestamps(path: Path) -> None:
    """Rewrite a zip so every entry carries ``ZIP_EPOCH`` instead of the build time."""
    tmp = path.with_suffix(".tmp")
    with zipfile.ZipFile(path) as src, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in sorted(src.infolist(), key=lambda i: i.filename):
            info = zipfile.ZipInfo(item.filename, date_time=ZIP_EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = item.external_attr
            dst.writestr(info, src.read(item.filename))
    shutil.move(tmp, path)


def _set_docx_page_count(path: Path, pages: int | None) -> None:
    """
    Rewrite the .docx zip so it declares ``pages``, or — when ``pages`` is None — so it
    declares no page count at all.

    A .docx is just a zip of XML parts, and the page count lives in one of them
    (``docProps/app.xml``). Editing a zip in place is not a thing, so the file is copied
    part by part into a new archive with that one part rewritten.

    For the None case the part is kept but its ``<Pages>`` element is removed, rather
    than the whole part being deleted. Deleting it would leave ``[Content_Types].xml``
    and ``_rels/.rels`` pointing at a part that no longer exists — a broken document,
    which is a different test from a valid document that simply never recorded its page
    count.
    """
    if pages is None:
        import re as _re
        tmp = path.with_suffix(".tmp")
        with zipfile.ZipFile(path) as src, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as dst:
            for item in src.infolist():
                data = src.read(item.filename)
                if item.filename == "docProps/app.xml":
                    data = _re.sub(rb"<Pages>.*?</Pages>", b"", data)
                dst.writestr(item, data)
        shutil.move(tmp, path)
        return

    app_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/'
        'extended-properties" xmlns:vt="http://schemas.openxmlformats.org/'
        'officeDocument/2006/docPropsVTypes">'
        f"<Application>Microsoft Office Word</Application><Pages>{pages}</Pages>"
        "</Properties>"
    )
    content_types_entry = (
        '<Override PartName="/docProps/app.xml" ContentType="application/vnd.'
        'openxmlformats-officedocument.extended-properties+xml"/>'
    )
    rel_entry = (
        '<Relationship Id="rIdApp" Type="http://schemas.openxmlformats.org/'
        'officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
    )

    tmp = path.with_suffix(".tmp")
    with zipfile.ZipFile(path) as src, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == "[Content_Types].xml":
                text = data.decode("utf-8")
                if "docProps/app.xml" not in text:
                    text = text.replace("</Types>", content_types_entry + "</Types>")
                data = text.encode("utf-8")
            elif item.filename == "_rels/.rels":
                text = data.decode("utf-8")
                if "extended-properties" not in text:
                    text = text.replace("</Relationships>", rel_entry + "</Relationships>")
                data = text.encode("utf-8")
            elif item.filename == "docProps/app.xml":
                continue                        # replaced below
            dst.writestr(item, data)
        # Fixed timestamp here too — writestr with a plain name would stamp "now".
        dst.writestr(zipfile.ZipInfo("docProps/app.xml", date_time=ZIP_EPOCH), app_xml)
    shutil.move(tmp, path)


def write_html(name: str, title: str, sections: list[tuple[str, str]]) -> None:
    parts = [
        "<!doctype html>", '<html lang="en">', "<head>", '<meta charset="utf-8">',
        f"<title>{title}</title>", "</head>", "<body>", f"<h1>{title}</h1>",
    ]
    for heading, body in sections:
        parts.append(f"<h2>{heading}</h2>")
        for para in body.strip().split("\n\n"):
            parts.append(f"<p>{para.strip()}</p>")
    parts += ["</body>", "</html>"]
    write_text(name, "\n".join(parts))


def write_site(corpus: dict[str, str]) -> None:
    """
    Write a small linked website under ``files/site/`` for testing the weblink source.

    A weblink is not a file, so it cannot be a fixture in the same sense — the service
    fetches it over HTTP. What *can* be committed is a site with a known link structure,
    served locally, so that a crawl has a right answer to be measured against:

        depth 0  ->  index.html only                       (1 page)
        depth 1  ->  index + the 3 pages it links to       (4 pages)
        depth 2  ->  the above + the 2 pages those link to (6 pages)

    Without a fixed structure, "did the crawl respect max_depth?" can only be answered
    with a shrug, because a real website's page count changes underneath the test.
    ``deep.html`` sits at depth 3 precisely so that it must *not* be fetched.
    """
    site = FILES / "site"
    site.mkdir(exist_ok=True)

    def page(name: str, title: str, body: str, links: list[tuple[str, str]]) -> None:
        html = [
            "<!doctype html>", '<html lang="en">', "<head>", '<meta charset="utf-8">',
            f"<title>{title}</title>", "</head>", "<body>", f"<h1>{title}</h1>",
        ]
        for para in _wrap(body, 100)[:40]:
            if para:
                html.append(f"<p>{para}</p>")
        if links:
            html.append("<ul>")
            html += [f'<li><a href="{href}">{label}</a></li>' for href, label in links]
            html.append("</ul>")
        html += ["</body>", "</html>"]
        (site / name).write_text("\n".join(html), encoding="utf-8")

    declaration = corpus["declaration"]
    gitanjali = corpus["gitanjali"]
    tales = corpus["tales"]
    constitution = corpus["constitution"]

    page("index.html", "Fixture Site — Home",
         "The root of a deliberately small website used to test crawl depth. It links to "
         "three pages, each holding a passage from the public-domain fixture corpus.",
         [("declaration.html", "Declaration of Independence"),
          ("gitanjali.html", "Gitanjali"),
          ("tales.html", "Indian Fairy Tales")])
    page("declaration.html", "Declaration of Independence (extract)",
         declaration[:3000], [("constitution.html", "United States Constitution")])
    page("gitanjali.html", "Gitanjali (extract)", gitanjali[:3000],
         [("tales-more.html", "More Indian Fairy Tales")])
    page("tales.html", "Indian Fairy Tales (extract)", tales[:3000], [])
    page("constitution.html", "United States Constitution (extract)",
         constitution[:3000], [("deep.html", "Depth-3 page — must not be crawled at depth 2")])
    page("tales-more.html", "Indian Fairy Tales, continued", tales[3000:6000], [])
    # Deliberately says nothing about the crawler's configuration — a fixture page can end
    # up indexed in a real knowledge base, so it describes only its own position.
    page("deep.html", "Depth 3 — out of reach",
         "This page sits three links away from the home page. It is the furthest page in "
         "this site, and it exists to be left out: any crawl that stops within two links "
         "of the home page will never reach it.", [])

    log.info("wrote %-52s %8s", "site/ (7 pages)", "depth 0-3")


def write_wav(name: str, seconds: float = 1.0, freq: float = 440.0) -> None:
    """
    Write a structurally valid mono 16-bit WAV holding a pure tone.

    A tone, not speech: the point of this fixture is the *empty transcript* path. The
    upload is accepted (WAV is a supported type), transcription returns nothing, and the
    worker fails it on the minimum-length rule. Recording real speech is not something a
    generator can do reproducibly.
    """
    path = FILES / name
    rate = 16_000
    frames = b"".join(
        struct.pack("<h", int(12_000 * math.sin(2 * math.pi * freq * (i / rate))))
        for i in range(int(rate * seconds))
    )
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(frames)
    log.info("wrote %-52s %8s", name, f"{seconds:g}s tone")


def write_audio_variants(base_wav: str) -> None:
    """
    Re-encode the WAV fixture into the other audio containers the service accepts.

    ``SUPPORTED_FILE_TYPES`` lists five audio/video content types, and until now only
    ``audio/wav`` had a fixture. The gap mattered because the *type* check and the
    *transcription* are separate steps: a container the service claims to accept can
    still be refused at the door, and nothing would have caught that.

    Encoding needs ffmpeg, which is not a dependency of this service. It is fetched
    through the ``imageio-ffmpeg`` wheel (a self-contained static binary, no system
    install) if that happens to be present. If it is not, the committed files are left
    exactly as they are and this step is skipped — the audio fixtures rarely change, so
    a missing encoder should not stop anyone rebuilding the other ninety per cent.
    """
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        log.warning("ffmpeg unavailable (pip install imageio-ffmpeg) — "
                    "keeping the committed audio fixtures as they are")
        return

    import subprocess

    src = FILES / base_wav
    # (filename, ffmpeg args) — one per container the service says it accepts.
    variants = [
        ("workerfail_silent_tone.mp3", ["-c:a", "libmp3lame", "-b:a", "64k"]),
        ("workerfail_silent_tone.ogg", ["-c:a", "libvorbis", "-q:a", "3"]),
        ("workerfail_silent_tone.aac", ["-c:a", "aac", "-b:a", "64k"]),
        ("workerfail_silent_tone.mpeg", ["-c:a", "mp2", "-b:a", "64k", "-f", "mpeg"]),
    ]
    # Without these, ffmpeg writes the encoder version into the file and — for Ogg — a
    # randomly chosen stream serial number, so the same input produced a different file
    # every run. "bitexact" is ffmpeg's own term for reproducible output.
    bitexact = ["-fflags", "+bitexact", "-flags", "+bitexact", "-flags:a", "+bitexact"]

    for name, args in variants:
        out = FILES / name
        result = subprocess.run(
            [exe, "-y", "-loglevel", "error", "-i", str(src), *args, *bitexact, str(out)],
            capture_output=True, text=True)
        if result.returncode != 0:
            log.error("ffmpeg failed for %s: %s", name, result.stderr.strip()[:200])
            continue
        log.info("wrote %-52s %8.1f KB", name, out.stat().st_size / 1024)


def _wrap(text: str, width: int) -> list[str]:
    """Wrap prose to ``width`` columns, keeping blank lines between paragraphs."""
    import textwrap
    lines: list[str] = []
    for para in text.split("\n\n"):
        para = " ".join(para.split())
        if not para:
            continue
        lines.extend(textwrap.wrap(para, width) or [""])
        lines.append("")
    return lines


def _split_into(text: str, parts: int) -> list[str]:
    """Split prose into ``parts`` roughly equal chunks, cutting on whitespace."""
    text = text.strip()
    size = max(1, len(text) // parts)
    out, start = [], 0
    for i in range(parts):
        if i == parts - 1:
            end = len(text)
        else:
            end = text.rfind(" ", start, start + size)
            if end <= start:                   # rfind returns -1 when there is no space
                end = min(start + size, len(text))
        out.append(text[start:end].strip())
        start = end
    return out


# ---------------------------------------------------------------------------
# The fixture set
# ---------------------------------------------------------------------------
def build(include_oversize: bool = False) -> None:
    FILES.mkdir(parents=True, exist_ok=True)
    declaration = source("declaration-of-independence.txt")
    constitution = source("us-constitution.txt")
    federalist = source("federalist-papers-excerpt.txt")
    gitanjali = source("gitanjali-tagore.txt")
    gita = source("song-celestial-bhagavad-gita.txt")
    tales = source("indian-fairy-tales-jacobs.txt")

    log.info("--- A. accepted: happy path -------------------------------------")
    write_pdf("accept_declaration_1page.pdf", [declaration], "Declaration of Independence")
    write_pdf("accept_constitution_10page.pdf", _split_into(constitution, MAX_PDF_PAGES),
              "United States Constitution")
    write_docx("accept_declaration_no_pagecount.docx", declaration.split("\n\n")[:40],
               "Declaration of Independence", declared_pages=None)
    write_docx("accept_constitution_10page.docx", constitution.split("\n\n")[:60],
               "United States Constitution", declared_pages=MAX_PDF_PAGES)
    write_text("accept_declaration.txt", declaration.strip())
    write_text("accept_constitution.md", _as_markdown(constitution, "United States Constitution"))
    write_html("accept_declaration.html", "Declaration of Independence",
               [("Full text", declaration)])
    write_text("accept_filename with spaces & (special)#chars.txt", declaration.strip())
    write_text("accept_unicode_mixed.txt", _unicode_variant(gitanjali))

    log.info("--- A1b. accepted: one fixture per supported content type -------")
    # SUPPORTED_FILE_TYPES lists eleven content types. The ones not already covered
    # above get a file here, so every branch of get_file_text has something to read.
    write_html("accept_declaration.htm", "Declaration of Independence (.htm)",
               [("Full text", declaration)])
    # application/octet-stream is accepted, but get_file_text only routes it to the text
    # reader when the filename ends in .md. This is that one working path; its failing
    # twin is workerfail_octet_stream_not_md.csv in section D.
    write_text("accept_octet_stream.md", _as_markdown(gitanjali, "Gitanjali — Rabindranath Tagore"))

    log.info("--- A2. accepted: Indian-context corpus --------------------------")
    write_pdf("accept_gitanjali_3page.pdf", _split_into(gitanjali, 3), "Gitanjali — Rabindranath Tagore")
    write_docx("accept_bhagavad_gita.docx", gita.split("\n\n")[:45],
               "The Song Celestial — Bhagavad Gita", declared_pages=4)
    write_text("accept_indian_fairy_tales.txt", tales.strip()[:40_000].rsplit("\n\n", 1)[0])
    write_text("accept_gitanjali.md", _as_markdown(gitanjali, "Gitanjali — Rabindranath Tagore"))
    write_html("accept_bhagavad_gita.html", "The Song Celestial — Bhagavad Gita",
               [("Extract", gita[:12_000])])

    log.info("--- A3. accepted: visual content ---------------------------------")
    # Chart data is measured from the corpus, so the picture states something true.
    chart = _chart_png(
        [("Declaration of Independence", len(declaration.strip())),
         ("US Constitution", len(constitution.strip())),
         ("Gitanjali (Tagore)", len(gitanjali.strip())),
         ("Bhagavad Gita (Song Celestial)", len(gita.strip())),
         ("Indian Fairy Tales", len(tales.strip())),
         ("Federalist Papers (excerpt)", len(federalist.strip()))],
        "Fixture corpus — characters per source text", "chars")
    write_pdf_with_image("accept_text_and_chart.pdf", constitution,
                         "United States Constitution — with a corpus chart", chart)
    chart.seek(0)
    write_docx("accept_docx_with_image.docx", gitanjali.split("\n\n")[:20],
               "Gitanjali — with a corpus chart", declared_pages=3, image=chart)
    # Table content is drawn from the corpus itself — bibliographic facts and measured
    # lengths. Fixtures get uploaded to real knowledge bases during testing, so they
    # carry nothing about how this service works internally; a public-domain reading
    # list is just as good for testing table extraction and leaks nothing.
    write_pdf_with_table(
        "accept_table_layout.pdf",
        "The fixture corpus at a glance",
        ["Work", "First published", "Author or editor"],
        [["The Declaration of Independence", "1776", "Second Continental Congress"],
         ["The Constitution of the United States", "1787", "Constitutional Convention"],
         ["The Federalist Papers", "1787-88", "Hamilton, Madison and Jay"],
         ["The Song Celestial", "1885", "translated by Sir Edwin Arnold"],
         ["Indian Fairy Tales", "1892", "edited by Joseph Jacobs"],
         ["Gitanjali", "1912", "Rabindranath Tagore"]],
        "Six public-domain works, listed oldest first. Every fact in this table can be "
        "checked against the works themselves, so a question answered from it has a "
        "right answer — and the answer is only available in table form.")

    log.info("--- A4. weblink source: a site with a known link structure -------")
    write_site({"declaration": declaration, "constitution": constitution,
                "gitanjali": gitanjali, "tales": tales})

    log.info("--- B. accepted: exactly on the boundary -------------------------")
    write_text("accept_text_at_char_limit.txt", exact_chars(MAX_TEXT_CHARS, federalist))
    write_text("accept_min_length_10chars.txt", exact_chars(MIN_LENGTH, constitution))

    log.info("--- C. rejected at upload with 400 -------------------------------")
    write_pdf("reject_11page.pdf", _split_into(constitution, MAX_PDF_PAGES + 1),
              "United States Constitution")
    write_docx("reject_11page.docx", constitution.split("\n\n")[:60],
               "United States Constitution", declared_pages=MAX_PDF_PAGES + 1)
    write_text("reject_text_over_char_limit.txt", exact_chars(MAX_TEXT_CHARS + 1, federalist))
    write_text("reject_markdown_over_char_limit.md", exact_chars(MAX_TEXT_CHARS + 1, federalist))
    # Sent as text/csv, which is not in SUPPORTED_FILE_TYPES. validate_file_content_type
    # is called from routers/document_management.py:164, so this is a 400 at upload —
    # but note the guard is `if file and file.content_type`, which is why the
    # octet-stream twin of this file in section D behaves completely differently.
    write_text("reject_unsupported_type.csv", _as_csv(constitution))
    (FILES / "reject_corrupt.pdf").write_bytes(
        b"%PDF-1.7\n% this file starts like a PDF and then stops being one\n"
        + b"the cross-reference table and trailer are missing entirely.\n" * 20
    )
    log.info("wrote %-52s %8s", "reject_corrupt.pdf", "corrupt")
    (FILES / "reject_not_a_docx.docx").write_bytes(
        b"This file has a .docx extension but is plain text, not a zip archive.\n"
        b"get_docx_page_count_from_bytes raises BadZipFile on it.\n"
    )
    log.info("wrote %-52s %8s", "reject_not_a_docx.docx", "not a zip")

    log.info("--- D. accepted at upload, expected to fail in the worker --------")
    write_text("workerfail_empty.txt", "")
    write_text("workerfail_too_short_9chars.txt", exact_chars(MIN_LENGTH - 1, constitution))
    write_scanned_pdf("workerfail_scanned_no_text_layer.pdf", declaration,
                      "Declaration of Independence (scanned)")
    write_wav("workerfail_silent_tone.wav")
    write_audio_variants("workerfail_silent_tone.wav")
    # Same bytes as reject_unsupported_type.csv, but meant to be sent as
    # application/octet-stream — a supported content type, so validate_file_content_type
    # lets it through. get_file_text then routes octet-stream to the text reader *only*
    # when the filename ends in .md; this one does not, so it raises deep in the worker.
    # The content type a client happens to send is what decides which of the two
    # outcomes you get, from one identical file.
    write_text("workerfail_octet_stream_not_md.csv", _as_csv(constitution))

    if include_oversize:
        log.info("--- E. oversize (not committed) ---------------------------------")
        path = FILES / "reject_oversize_6mb.txt"
        block = federalist.strip() + "\n\n"
        with path.open("w", encoding="utf-8") as fh:
            written = 0
            target = MAX_FILE_SIZE + 1024 * 1024
            while written < target:
                fh.write(block)
                written += len(block.encode("utf-8"))
        log.info("wrote %-52s %8.1f MB", path.name, path.stat().st_size / 1024 / 1024)
    else:
        log.info("skipping the oversize fixture (pass --include-oversize to build it)")


def _as_markdown(text: str, title: str) -> str:
    """Turn a plain source text into Markdown with real headings."""
    lines = [f"# {title}", ""]
    for para in text.strip().split("\n\n"):
        para = " ".join(para.split())
        if not para:
            continue
        # Section headings in these texts are short, capitalised lines.
        if len(para) < 60 and para.upper() == para:
            lines += [f"## {para.title()}", ""]
        else:
            lines += [para, ""]
    return "\n".join(lines).strip()


def _as_csv(text: str) -> str:
    """One row per paragraph — a genuinely tabular view of the same source text."""
    rows = ["section,characters,text"]
    for i, para in enumerate(text.strip().split("\n\n")[:50], start=1):
        para = " ".join(para.split()).replace('"', '""')
        rows.append(f'{i},{len(para)},"{para}"')
    return "\n".join(rows)


def _unicode_variant(text: str) -> str:
    """
    Gitanjali, which already carries genuine typographic punctuation, with a short
    authored header in three scripts on top.

    Two different kinds of content, deliberately labelled as such. The body is Tagore's
    text unchanged apart from normalising any stray ASCII quotes — its curly quotes and
    em dashes are the source's own, which makes it honest evidence that a real document
    round-trips without mojibake.

    The header is three sentences meaning "this is a test file", in Hindi, Japanese and
    German. Those *are* authored, because Project Gutenberg's plain-text editions of
    Indian works are all romanised — there is no Devanagari to borrow. Three scripts and
    an umlaut in one file is what turns a latin-1 misread from a subtle corruption into
    an obvious one, so it is worth the small piece of invention. The translations are
    given in the README so nobody has to take them on trust.
    """
    body = (text.strip()
            .replace('"', "“", 1)
            .replace('"', "”")
            .replace("'", "’")
            .replace("--", "—")
            .replace("...", "…"))
    header = (
        "यह एक परीक्षण "
        "फ़ाइल है।\n"          # Hindi
        "これはテストファイルです。\n"  # Japanese
        "Dies ist eine Testdatei für die Zeichenkodierung — äöüß.\n"
    )
    return header + "\n" + body


# ---------------------------------------------------------------------------
# Verification — re-measure with the service's own readers
# ---------------------------------------------------------------------------
# Every content type in SUPPORTED_FILE_TYPES (utils/files/parsing_utils.py) mapped to the
# fixture that exercises it. Mirrored here rather than imported because importing it would
# pull in the whole service — FastAPI, LangChain, LlamaParse — just to read a dict.
TYPE_COVERAGE = {
    "application/pdf": "accept_declaration_1page.pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        "accept_declaration_no_pagecount.docx",
    "text/plain": "accept_declaration.txt",
    "text/markdown": "accept_constitution.md",
    "text/html": "accept_declaration.html",
    "application/octet-stream": "accept_octet_stream.md",
    "audio/mpeg": "workerfail_silent_tone.mp3",
    "audio/wav": "workerfail_silent_tone.wav",
    "audio/x-wav": "workerfail_silent_tone.wav",     # same bytes, second MIME spelling
    "audio/ogg": "workerfail_silent_tone.ogg",
    "video/mpeg": "workerfail_silent_tone.mpeg",
}
# Extensions the service maps to a type but which differ from the primary fixture's.
EXTENSION_COVERAGE = {
    ".htm": "accept_declaration.htm",
    ".aac": "workerfail_silent_tone.aac",
    ".mpeg": "workerfail_silent_tone.mpeg",
}


def _verify_type_coverage() -> tuple[int, int]:
    """
    Confirm every accepted content type and extension still has a fixture on disk.

    This is the check that stops the set rotting. Adding a format to
    ``SUPPORTED_FILE_TYPES`` is a one-line change, and nothing about it prompts anyone to
    come back here — so the gap would only surface when someone uploads that format and it
    breaks. Failing loudly at build time is much cheaper.

    Returns ``(failures, checks)``.
    """
    failures = checks = 0
    for label, fixture in ([(t, f) for t, f in sorted(TYPE_COVERAGE.items())]
                           + [(f"extension {e}", f)
                              for e, f in sorted(EXTENSION_COVERAGE.items())]):
        exists = (FILES / fixture).exists()
        checks += 1
        failures += 0 if exists else 1
        log.log(logging.INFO if exists else logging.ERROR,
                "%-7s %-58s covered by %s", "ok" if exists else "MISSING", label, fixture)
    return failures, checks


def _verify_site() -> int:
    """
    Walk the fixture site the way a crawler would and confirm the page-count per depth.

    The whole value of the site fixture is that "crawl to depth 1" has one correct
    answer. That answer is a property of the link graph, so it is re-derived here by
    breadth-first search rather than written down twice — if someone adds a link, this
    catches it immediately instead of the crawl test failing mysteriously later.

    Returns ``(failures, checks)``.
    """
    import re as _re

    site = FILES / "site"
    expected_per_depth = {0: 1, 1: 4, 2: 6}     # see write_site's docstring

    failures = 0
    checks = 0
    for depth, expected in expected_per_depth.items():
        seen, frontier = {"index.html"}, ["index.html"]
        for _ in range(depth):
            nxt = []
            for page in frontier:
                html = (site / page).read_text(encoding="utf-8")
                for href in _re.findall(r'href="([^"]+)"', html):
                    if href not in seen:
                        seen.add(href)
                        nxt.append(href)
            frontier = nxt
        ok = len(seen) == expected
        checks += 1
        failures += 0 if ok else 1
        log.log(logging.INFO if ok else logging.ERROR,
                "%-7s %-52s depth %d reaches %d pages (expected %d)",
                "ok" if ok else "MISMATCH", "site/", depth, len(seen), expected)

    # deep.html must be out of reach at the maximum depth the API permits.
    reachable_at_2 = {"index.html"}
    frontier = ["index.html"]
    for _ in range(2):
        nxt = []
        for page in frontier:
            for href in _re.findall(r'href="([^"]+)"',
                                    (site / page).read_text(encoding="utf-8")):
                if href not in reachable_at_2:
                    reachable_at_2.add(href)
                    nxt.append(href)
        frontier = nxt
    ok = "deep.html" not in reachable_at_2
    checks += 1
    failures += 0 if ok else 1
    log.log(logging.INFO if ok else logging.ERROR,
            "%-7s %-52s %s", "ok" if ok else "MISMATCH", "site/deep.html",
            "unreachable at max_depth=2, as intended")
    return failures, checks


def verify() -> int:
    """Re-measure each fixture and report anything that drifted. Returns a exit code."""
    from pdfminer.pdfpage import PDFPage

    def pdf_pages(p: Path) -> int:
        with p.open("rb") as fh:
            return len(list(PDFPage.get_pages(BytesIO(fh.read()), check_extractable=False)))

    def docx_pages(p: Path):
        import xml.etree.ElementTree as ET
        with zipfile.ZipFile(p) as z:
            if "docProps/app.xml" not in z.namelist():
                return None
            ns = {"ep": "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"}
            with z.open("docProps/app.xml") as fh:
                el = ET.parse(fh).find("ep:Pages", ns)
                return int(el.text) if el is not None and el.text else None

    def chars(p: Path) -> int:
        return len(p.read_text(encoding="utf-8").strip())

    expectations = [
        ("accept_declaration_1page.pdf", pdf_pages, 1),
        ("accept_constitution_10page.pdf", pdf_pages, MAX_PDF_PAGES),
        ("reject_11page.pdf", pdf_pages, MAX_PDF_PAGES + 1),
        ("workerfail_scanned_no_text_layer.pdf", pdf_pages, 1),
        ("accept_constitution_10page.docx", docx_pages, MAX_PDF_PAGES),
        ("reject_11page.docx", docx_pages, MAX_PDF_PAGES + 1),
        ("accept_declaration_no_pagecount.docx", docx_pages, None),
        ("accept_text_at_char_limit.txt", chars, MAX_TEXT_CHARS),
        ("reject_text_over_char_limit.txt", chars, MAX_TEXT_CHARS + 1),
        ("reject_markdown_over_char_limit.md", chars, MAX_TEXT_CHARS + 1),
        ("accept_min_length_10chars.txt", chars, MIN_LENGTH),
        ("workerfail_too_short_9chars.txt", chars, MIN_LENGTH - 1),
        ("workerfail_empty.txt", chars, 0),
    ]

    failures = 0
    checks = 0
    for name, measure, expected in expectations:
        actual = measure(FILES / name)
        ok = actual == expected
        checks += 1
        failures += 0 if ok else 1
        log.log(logging.INFO if ok else logging.ERROR,
                "%-7s %-52s expected %-8s got %s",
                "ok" if ok else "MISMATCH", name, expected, actual)

    # --- visual content: the point of these files is what is *not* extractable -------
    from pdfminer.high_level import extract_text

    visual_checks = [
        ("accept_text_and_chart.pdf", "prose is extractable",
         lambda t: len(t.strip()) > 500, True),
        ("accept_text_and_chart.pdf", "chart labels are NOT extractable",
         lambda t: "Gitanjali (Tagore)" in t, False),
        ("accept_table_layout.pdf", "table cells are extractable as loose text",
         lambda t: "Rabindranath Tagore" in t and "1912" in t, True),
        ("workerfail_scanned_no_text_layer.pdf", "scan yields no text at all",
         lambda t: len(t.strip()) == 0, True),
    ]
    for name, what, predicate, expected in visual_checks:
        actual = predicate(extract_text(str(FILES / name)))
        ok = actual == expected
        checks += 1
        failures += 0 if ok else 1
        log.log(logging.INFO if ok else logging.ERROR,
                "%-7s %-52s %s", "ok" if ok else "MISMATCH", name, what)

    with zipfile.ZipFile(FILES / "accept_docx_with_image.docx") as z:
        has_media = any("word/media/" in n for n in z.namelist())
    checks += 1
    failures += 0 if has_media else 1
    log.log(logging.INFO if has_media else logging.ERROR,
            "%-7s %-52s %s", "ok" if has_media else "MISMATCH",
            "accept_docx_with_image.docx", "carries an embedded image part")

    site_failures, site_checks = _verify_site()
    failures += site_failures
    checks += site_checks

    type_failures, type_checks = _verify_type_coverage()
    failures += type_failures
    checks += type_checks

    oversized = [p for p in FILES.iterdir() if p.stat().st_size > MAX_FILE_SIZE]
    for p in oversized:
        if p.name != "reject_oversize_6mb.txt":
            log.error("MISMATCH %s is over the %d MB limit but is not the oversize fixture",
                      p.name, MAX_FILE_SIZE // 1024 // 1024)
            failures += 1
            checks += 1

    log.info("verify: %d checks, %d failed", checks, failures)
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--verify", action="store_true", help="re-measure fixtures after building")
    ap.add_argument("--include-oversize", action="store_true",
                    help="also build the oversize file (not committed)")
    args = ap.parse_args()

    build(include_oversize=args.include_oversize)
    return verify() if args.verify else 0


if __name__ == "__main__":
    raise SystemExit(main())

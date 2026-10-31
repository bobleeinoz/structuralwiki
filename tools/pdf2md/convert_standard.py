#!/usr/bin/env python3
"""
Convert a standards PDF (e.g. AS 4100-2020) to Markdown with Docling.

Features
  * Auto-detects "Section N" / "Appendix X" start pages (skips the table of contents)
  * Converts one Markdown file per section/appendix
  * Tables      -> Markdown tables (Docling TableFormer, ACCURATE mode)
  * Equations   -> LaTeX ($$ ... $$) via Docling formula enrichment
  * Figures     -> PNG files referenced from the Markdown
  * Strips repeated page furniture (licence watermark, page headers/footers)

Usage
  # list detected sections and their page ranges
  python convert_standard.py AS4100-2020.pdf --list

  # convert Sections 1-3 only (sample)
  python convert_standard.py AS4100-2020.pdf -o out --sections 1-3

  # convert everything (sections + appendices + front matter)
  python convert_standard.py AS4100-2020.pdf -o out --sections all

  # convert selected items, e.g. section 5 and appendices H-I
  python convert_standard.py AS4100-2020.pdf -o out --sections 5,H,I

  # faster run without LaTeX equations (equations come out as plain text/images)
  python convert_standard.py AS4100-2020.pdf -o out --sections all --no-formulas

Requirements:  pip install docling pymupdf
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pymupdf as fitz  # PyMuPDF

# --------------------------------------------------------------------------- #
# 1. Section detection
# --------------------------------------------------------------------------- #
HEADING_RE = re.compile(r"^(Section|Appendix)\s+([0-9]{1,2}|[A-Z])\b(.*)$")
TOC_LEADER_RE = re.compile(r"\.{6,}")


@dataclass
class Part:
    kind: str          # "Section" | "Appendix" | "Front"
    key: str           # "1", "2", ... "A", "B" ... or "front"
    title: str
    start: int         # 1-based PDF page, inclusive
    end: int = 0       # 1-based PDF page, inclusive

    @property
    def slug(self) -> str:
        if self.kind == "Section":
            return f"section-{int(self.key):02d}"
        if self.kind == "Appendix":
            return f"appendix-{self.key.lower()}"
        return "front-matter"


def detect_parts(pdf_path: Path) -> list[Part]:
    """Find the first page of every Section / Appendix.

    A heading counts only if it is among the first few text lines of a page,
    the page is not a contents page (dotted leaders), and the number is the
    next expected one (so cross-references such as "Section 4 of AS/NZS 1170.0"
    are ignored)."""
    doc = fitz.open(pdf_path)
    parts: list[Part] = []
    next_sec, next_app = 1, "A"
    for pno in range(doc.page_count):
        text = doc[pno].get_text("text")
        if len(TOC_LEADER_RE.findall(text)) >= 3:
            continue  # table of contents
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        # ignore licence watermark lines
        lines = [ln for ln in lines if not _is_furniture(ln)]
        for i, ln in enumerate(lines[:8]):
            m = HEADING_RE.match(ln)
            if not m:
                continue
            kind, key, rest = m.group(1), m.group(2), m.group(3).strip()
            if kind == "Section" and key.isdigit() and int(key) == next_sec:
                title = rest or (lines[i + 1] if i + 1 < len(lines) else "")
                parts.append(Part(kind, key, title, pno + 1))
                next_sec += 1
                break
            if kind == "Appendix" and key == next_app:
                title = rest
                # appendix titles usually follow "(normative)/(informative)"
                for nxt in lines[i + 1:i + 4]:
                    if not nxt.startswith("("):
                        title = (title + " " + nxt).strip()
                        break
                parts.append(Part(kind, key, title, pno + 1))
                next_app = chr(ord(next_app) + 1)
                break
    if not parts:
        raise SystemExit("No 'Section 1' heading detected - check the PDF.")
    # front matter = everything before Section 1
    if parts[0].start > 1:
        parts.insert(0, Part("Front", "front", "Front matter", 1, parts[0].start - 1))
    for a, b in zip(parts, parts[1:]):
        a.end = b.start - 1
    parts[-1].end = doc.page_count
    return parts


def select_parts(parts: list[Part], spec: str) -> list[Part]:
    """spec: 'all' | '1-3' | '1,2,5' | 'A-C' | 'front' | mixed '1-3,H'."""
    if spec.lower() == "all":
        return parts
    wanted: set[str] = set()
    for tok in spec.split(","):
        tok = tok.strip()
        if "-" in tok:
            a, b = (t.strip() for t in tok.split("-", 1))
            if a.isdigit():
                wanted |= {str(i) for i in range(int(a), int(b) + 1)}
            else:
                wanted |= {chr(c) for c in range(ord(a.upper()), ord(b.upper()) + 1)}
        else:
            wanted.add(tok.upper() if not tok.isdigit() else tok)
    sel = [p for p in parts if p.key.upper() in {w.upper() for w in wanted}]
    if not sel:
        raise SystemExit(f"Nothing matched --sections {spec!r}")
    return sel


# --------------------------------------------------------------------------- #
# 2. Docling conversion
# --------------------------------------------------------------------------- #
def build_converter(formulas: bool, images_scale: float, artifacts_path: Path | None = None):
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import (
        PdfPipelineOptions,
        TableFormerMode,
    )
    from docling.document_converter import DocumentConverter, PdfFormatOption

    opts = PdfPipelineOptions()
    if artifacts_path:                       # pre-downloaded models (offline use)
        opts.artifacts_path = str(artifacts_path)
    opts.do_ocr = False                      # born-digital PDF: text layer is good
    opts.do_table_structure = True
    opts.table_structure_options.mode = TableFormerMode.ACCURATE
    opts.table_structure_options.do_cell_matching = True
    opts.do_formula_enrichment = formulas    # equations -> LaTeX
    opts.generate_picture_images = True      # figures -> PNG
    opts.generate_page_images = False
    opts.images_scale = images_scale
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )


def convert_part(converter, pdf: Path, part: Part, out_dir: Path) -> Path:
    from docling_core.types.doc import ImageRefMode

    res = converter.convert(pdf, page_range=(part.start, part.end))
    doc = res.document
    md_path = out_dir / f"{part.slug}.md"
    img_dir = out_dir / "images" / part.slug
    img_dir.mkdir(parents=True, exist_ok=True)
    doc.save_as_markdown(
        md_path,
        artifacts_dir=img_dir,
        image_mode=ImageRefMode.REFERENCED,
    )
    md = md_path.read_text(encoding="utf-8")
    md = clean_markdown(md, out_dir)
    header = (
        f"<!-- Source: {pdf.name}, PDF pages {part.start}-{part.end}. "
        f"Converted with Docling. -->\n\n"
    )
    md_path.write_text(header + md, encoding="utf-8")
    return md_path


# --------------------------------------------------------------------------- #
# 3. Markdown clean-up
# --------------------------------------------------------------------------- #
FURNITURE_PATTERNS = [
    r"Copyrighted material licensed to .*",
    r"No further reproduction or distribution is permitted\.?",
    r"AS 4100:2020(\s*\(?Reprint\)?)?",
    r"Australian Standard® AS 4100:2020",
    r"Standards Australia",
    r"\d{1,3}",                              # bare page numbers
]
_FURN_RE = re.compile(r"^\s*(?:" + "|".join(FURNITURE_PATTERNS) + r")\s*$")


def _is_furniture(line: str) -> bool:
    return bool(_FURN_RE.match(line))


def clean_markdown(md: str, out_dir: Path) -> str:
    out = []
    for ln in md.splitlines():
        if _is_furniture(ln):
            continue
        out.append(ln)
    md = "\n".join(out)
    # make image paths relative to the .md file
    md = md.replace(str(out_dir.resolve()) + "/", "").replace(str(out_dir) + "/", "")
    # collapse 3+ blank lines
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip() + "\n"


# --------------------------------------------------------------------------- #
# 4. CLI
# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf", type=Path)
    ap.add_argument("-o", "--out-dir", type=Path, default=Path("output"))
    ap.add_argument("--sections", default="1-3",
                    help="'all', '1-3', '5,H,I', 'front' ... (default 1-3)")
    ap.add_argument("--list", action="store_true", help="only list detected sections")
    ap.add_argument("--no-formulas", action="store_true",
                    help="skip LaTeX equation recognition (much faster)")
    ap.add_argument("--images-scale", type=float, default=2.0,
                    help="figure resolution multiplier (default 2 = ~144 dpi)")
    ap.add_argument("--artifacts-path", type=Path, default=None,
                    help="folder of pre-downloaded Docling models "
                         "(create with: docling-tools models download -o <dir> layout tableformer code_formula picture_classifier)")
    ap.add_argument("--skip-existing", action="store_true",
                    help="do not re-convert parts whose .md already exists")
    a = ap.parse_args(argv)

    parts = detect_parts(a.pdf)
    if a.list:
        for p in parts:
            print(f"{p.slug:15s} pages {p.start:3d}-{p.end:3d}  {p.title}")
        return 0

    chosen = select_parts(parts, a.sections)
    a.out_dir.mkdir(parents=True, exist_ok=True)
    converter = build_converter(not a.no_formulas, a.images_scale, a.artifacts_path)

    written = []
    for p in chosen:
        target = a.out_dir / f"{p.slug}.md"
        if a.skip_existing and target.exists():
            print(f"skip {p.slug} (exists)")
            written.append((p, target))
            continue
        t0 = time.time()
        print(f"converting {p.slug} (pages {p.start}-{p.end}) ...", flush=True)
        path = convert_part(converter, a.pdf, p, a.out_dir)
        print(f"  -> {path}  [{time.time() - t0:.0f}s]", flush=True)
        written.append((p, path))

    write_index(a.out_dir, parts)
    return 0


def write_index(out_dir: Path, parts: list[Part]) -> None:
    lines = ["# Contents\n"]
    for p in parts:
        f = out_dir / f"{p.slug}.md"
        name = f"{p.kind} {p.key}" if p.kind != "Front" else "Front matter"
        label = f"{name} — {p.title}" if p.kind != "Front" else name
        if f.exists():
            lines.append(f"- [{label}]({f.name}) (PDF p. {p.start}–{p.end})")
        else:
            lines.append(f"- {label} (PDF p. {p.start}–{p.end}) — *not yet converted*")
    (out_dir / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())

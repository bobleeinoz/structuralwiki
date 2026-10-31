# pdf2md — standards PDF → Markdown (Docling)

Converts a standards PDF (tested on AS 4100:2020 reprint) to one Markdown file per
Section / Appendix, with tables (Markdown), equations (LaTeX `$$…$$`) and figures (PNG).

```bash
python -m venv venv && ./venv/bin/pip install -r requirements.txt

# show detected sections and page ranges
./venv/bin/python convert_standard.py AS4100-2020-reprint.pdf --list

# sample: Sections 1–3
./venv/bin/python convert_standard.py AS4100-2020-reprint.pdf -o out --sections 1-3

# the rest, skipping what is already done
./venv/bin/python convert_standard.py AS4100-2020-reprint.pdf -o out --sections all --skip-existing
```

Options: `--sections 5,H,I`, `--no-formulas` (much faster, no LaTeX),
`--images-scale 3` (sharper figures), `--artifacts-path DIR` (offline models).

First run downloads Docling's models from huggingface.co (~1–2 GB).
Output: `out/section-01.md …`, `out/images/<part>/*.png`, `out/README.md` (contents).

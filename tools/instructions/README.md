# Build order from a LEGO instruction PDF

Turns an official building-instructions PDF into the step JSON the Build tab imports
(`app/buildorders/<set>.json`). Needs `pdfplumber`, `pillow`, `numpy`, `scipy` and poppler's `pdftoppm`.

How it works
1. **Text layer** (`extract.py`): step numerals, "Nx" callout labels and bag markers are told apart by
   font size (2017+ books: steps 26pt, sub-steps 16/22pt, bags 30/36pt; 2014 books: 38 / 30,18 / 36),
   the parts-inventory pages are found by their 6–7 digit element ids. Each callout label is handed
   the picture above it (embedded image objects, sliced when one image holds two parts, or the
   callout-box span when the picture is tiled into strips). Books that draw every glyph twice are
   de-duplicated; a MediaBox that doesn't start at the origin is corrected for.
2. **Repeats** (`build_steps.py`): a sub-assembly built N times is marked with a start "Nx" (16/18/24pt)
   and a 40pt end "Nx"; per-copy callouts inside are multiplied, unless a single main step already
   carries the full callout (2014 layout).
3. **Identification** (`match.py`, `assign.py`): every distinct callout picture is matched against the
   inventory pictures (same renderer, same angle) by shape + colour after keying out the background, and
   assigned to element ids under the inventory's quantity constraints. `verify.py` / `leftovers.py` /
   `closeup.py` draw review sheets; `overrides-*.json` fix the few the matcher gets wrong.

Example
    python3 build_steps.py 6187644.pdf out_75174 --overrides overrides-6187644.json --source "pdf 6187644"
    python3 build_steps.py 6091463.pdf out_b2 --main 38 --sub 30,18 --bag 34,36 --book 2
    python3 build_steps.py 6091462.pdf out_b1 --main 38 --sub 30,18 --bag 34,36 --book 1 --inventory-from out_b2/extract.json
    python3 build_steps.py 6366005.pdf out_bag1 --bags 1      # one bag only
    python3 build_steps.py 6282798.pdf out --extract-json out/extract.json --overrides ov.json \
        --leftovers-step "Minifigures & extras (not in numbered steps)"   # re-run on a saved extraction (NB: books with repeated
                                                                          # sub-assemblies lose the ×N on re-runs — run without it)
                                                                          # and sweep unplaced pieces into a last step

Results so far: 75174 254/277 pieces placed (the rest are minifigure parts), 75305 469/470, 75249
551/577, 75054 ~1070/1134 (2014 book; more look-alike greys, treat as approximate), 75948 880/918
placed + 38 in the extras step, 75954 769/875 (its callout boxes lay pieces diagonally, so the composite
slicer cuts badly; the overrides file maps the composites by hand and sets `null` where even that is unclear). Books with no text
layer (pre-2010 scans) would need OCR and are not handled.

## Scanned books (no text layer)

`ocr_extract.py` handles pre-2010 books that are flat 72 ppi scans: it finds the cyan callout boxes by
colour, OCRs the "Nx" labels and the big step numeral below each box (tesseract, per-blob, upscaled),
and reads the inventory page's element ids, snapping each against the set's published inventory
(`inventory-<set>.txt`, `qty|part|element|colour`, e.g. from brickmissing.com) because 6-pixel digits
mis-read. It writes the same extract.json, so the rest of the pipeline is unchanged:

    python3 ocr_extract.py 4562894.pdf out/ocr.json --pages 3-64 --inv-pages 65 --inventory inventory-7671.txt
    python3 build_steps.py 4562894.pdf out --extract-json out/ocr.json --min-score 1.2

Expect it to be approximate: the inventory page loses some entries at that resolution and tiny parts
(pins, axles) crop badly, so a third or so of the pieces come out as "could not be identified" in the
step note rather than as a wrong part. 7671: 217 of 339 pieces identified; 8016: 155 of 207.

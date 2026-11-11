# Bundled build orders

Build orders for the step-by-step Build tab. A file named `<set_num>.json`, `.mpd` or `.ldr`
(Rebrickable set number, e.g. `75954-1.json`) is imported automatically the first time that set's
build order is requested. If the bundled file for a set is later replaced by one with a different
name (e.g. an LDraw model superseded by the book's own steps), the set is re-imported on its next
visit; the Build tab's import form can replace a build order at any time.

`.json` files are the LEGO instruction books parsed with `tools/instructions/` (step numbers, pages
and bags as printed, element ids from the book's own parts list; see the README there). Pieces the
book never shows in a numbered step — minifigure parts and spares — are collected in a final
"Minifigures & extras" step so every inventory piece can be marked.

| File | Steps | Source |
|---|---|---|
| 7671-1.json AT-AP Walker | book 4562894 (scanned, OCR) | LEGO instructions |
| 8016-1.json Hyena Droid Bomber | book 4583956 (scanned, OCR) | LEGO instructions |
| 75054-1.json AT-AT | books 6091462 + 6091463 | LEGO instructions |
| 75174-1.json Desert Skiff Escape | book 6187644 | LEGO instructions |
| 75249-1.json Resistance Y-wing | book 6296571 | LEGO instructions |
| 75305-1.json Scout Trooper Helmet | book 6366005 (`pl-75305-bag-1.json`: bag 1 only) | LEGO instructions |
| 75183-1.json Darth Vader Transformation | book 6200554 (268 placed + 16 minifig/extras) | LEGO instructions |
| 75948-1.json Hogwarts Clock Tower | book 6282798 (all 918 pieces placed) | LEGO instructions |
| 75954-1.json Hogwarts Great Hall | book 6263128 (769 placed; ~50 pieces in 17 steps flagged "could not be identified") | LEGO instructions |
| 75953-1.mpd Hogwarts Whomping Willow | LDraw model by Stefan Frenz [smf], CCAL 2.0 — book 1 (6263122) still needed to parse the book instead | https://forums.ldraw.org/thread-24235.html |

The LDraw model's step numbers are the modeller's, not the book's, and it omits stickers and
minifigures (75953 uses 40232 for the owl) — the importer reports those as unmatched or guessed parts.

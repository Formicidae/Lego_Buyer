"""Storage bins: Rebrickable's part categories grouped into about a dozen physical bins, in an order that
follows the kind of part (bricks, slopes, plates, tiles, Technic, ...) instead of the alphabet.

The "Piece type" sort walks the bins in order, so a list can be worked through one bin at a time. The
grouping below is only a suggestion: /bins lets it be renamed, reordered and regrouped, and the result is
stored in the settings table as [{"name": ..., "cats": [category names in order]}].
"""
import json
import re

from . import db

SETTING = "bins"
MINIFIG = "Minifigure"  # whole minifigures (sourced as a unit) are filed like a category

# (bin name, patterns tried in order against the category name). First bin with a match wins, so
# "Technic Bricks" lands with the bricks before the Technic catch-all sees it.
DEFAULT = [
    ("Bricks", [r"^bricks$"]),
    ("Bricks: special, round & Technic", [r"^bricks special", r"^bricks round", r"^technic bricks"]),
    ("Slopes, curves & wedges", [r"^bricks sloped", r"^bricks curved", r"^bricks wedged"]),
    ("Plates", [r"^plates$"]),
    ("Plates: special", [r"^plates special"]),
    ("Plates: wedge, round & dishes", [r"^plates angled", r"^plates round"]),
    ("Tiles", [r"^tiles$", r"^tiles special", r"^tiles round", r"^tiles"]),
    ("Technic pins, axles & bushes", [r"^technic pins", r"^technic axles", r"^technic bushes"]),
    ("Technic beams, connectors & gears", [r"^technic beams$", r"^technic beams", r"^technic connectors", r"^technic gears", r"^technic", r"^pneumatics", r"^mechanical"]),
    ("Hinges, bars & clips", [r"^hinges", r"^bars", r"^supports", r"^tubes", r"^string"]),
    ("Panels, windows & windscreens", [r"^panels", r"^windows", r"^windscreens", r"^containers"]),
    ("Wheels & vehicle parts", [r"^wheels", r"^transportation"]),
    ("Minifigures & accessories", [r"^minifig accessories", r"^tools", r"^minifig", r"^minidoll", r"^flags", r"^energy"]),
    ("Everything else", [r""]),
]


def _default_place(cat):
    """-> (default bin name, rank inside it) for a category name."""
    low = (cat or "").lower()
    for name, pats in DEFAULT:
        for i, pat in enumerate(pats):
            if re.search(pat, low):
                return name, i
    return DEFAULT[-1][0], 0


def saved():
    try:
        cfg = json.loads(db.setting_get(SETTING) or "null")
    except ValueError:
        cfg = None
    return cfg if isinstance(cfg, list) and cfg else None


def _usage():
    rows = db.q("SELECT c.name AS name, SUM(p.quantity) AS pieces, COUNT(*) AS lots FROM set_parts p "
                "JOIN part_categories c ON c.id = p.part_cat_id WHERE p.is_spare = 0 GROUP BY c.name")
    return {r["name"]: (r["pieces"] or 0, r["lots"]) for r in rows}


def layout():
    """The bins in order, each with every known category filed in it:
    {"custom": bool, "bins": [{"name", "cats": [{"name", "pieces", "lots"}]}]}"""
    cfg = saved()
    names = [b["name"] for b in cfg] if cfg else [n for n, _ in DEFAULT]
    placed = {}  # category -> (bin index, rank)
    if cfg:
        for bi, b in enumerate(cfg):
            for ci, c in enumerate(b.get("cats") or []):
                placed.setdefault(c, (bi, ci))
    cats = {r["name"] for r in db.q("SELECT name FROM part_categories")} | {MINIFIG}
    for c in cats:
        if c not in placed:
            dname, rank = _default_place(c)
            # a category this layout has never seen: its suggested bin if that still exists, else the last one
            placed[c] = (names.index(dname) if dname in names else len(names) - 1, 1000 + rank)
    use = _usage()
    bins = [{"name": n, "cats": []} for n in names]
    for c in sorted(cats, key=lambda c: (placed[c][0], placed[c][1], c)):
        if placed[c][0] < len(bins):
            pieces, lots = use.get(c, (0, 0))
            bins[placed[c][0]]["cats"].append({"name": c, "pieces": pieces, "lots": lots})
    return {"custom": bool(cfg), "bins": bins}


def save(bins):
    """bins: [{"name", "cats": [names]}] in the wanted order. Raises ValueError on a bad layout."""
    if not isinstance(bins, list) or not 1 <= len(bins) <= 40:
        raise ValueError("Between 1 and 40 bins, please.")
    out, seen_names, seen_cats = [], set(), set()
    for b in bins:
        name = str((b or {}).get("name") or "").strip()[:60]
        if not name:
            raise ValueError("Every bin needs a name.")
        if name.lower() in seen_names:
            raise ValueError(f"Two bins are called “{name}”.")
        seen_names.add(name.lower())
        cats = []
        for c in (b.get("cats") or []):
            c = str(c)[:80]
            if c and c not in seen_cats:
                seen_cats.add(c)
                cats.append(c)
        out.append({"name": name, "cats": cats})
    db.setting_set(SETTING, json.dumps(out))
    return layout()


def reset():
    db.setting_set(SETTING, None)
    return layout()

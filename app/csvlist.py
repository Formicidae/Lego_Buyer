"""Parts lists from a Rebrickable parts CSV (any set page -> Inventory -> Export Parts -> Rebrickable CSV).

That export exists for everything on the site, including B-models (4488-1-b1) and sub-sets that the
Rebrickable API does not serve, so it is the way to get those into the app. Columns: Part, Color (a
Rebrickable colour id), Quantity, Is Spare. Each row is looked up on Rebrickable for its name, picture
and element id, so the list gets the same pictures, prices and sourcing as a set.
"""
import csv
import io
import re
import time

from . import rebrickable

PREFIX = "csv-"
_COLS = {"part": "part", "part_num": "part", "partid": "part", "color": "color", "color_id": "color", "colorid": "color",
         "quantity": "qty", "qty": "qty", "is spare": "spare", "is_spare": "spare", "spare": "spare"}


def slugify(name):
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return s[:48] or "list"


def parse(data: bytes):
    """-> [(part_num, color_id, quantity, is_spare)]; raises ValueError when it is not a parts CSV."""
    text = data.decode("utf-8-sig", "replace")
    rd = csv.reader(io.StringIO(text))
    head = next(rd, None)
    if not head:
        raise ValueError("The file is empty.")
    idx = {}
    for i, h in enumerate(head):
        k = _COLS.get(h.strip().lower())
        if k and k not in idx:
            idx[k] = i
    if not {"part", "color", "qty"} <= set(idx):
        raise ValueError("Not a Rebrickable parts CSV: it needs Part, Color and Quantity columns.")
    rows = []
    for r in rd:
        if len(r) <= max(idx.values()) and len(r) <= idx["qty"]:
            continue
        part = r[idx["part"]].strip()
        try:
            color, qty = int(r[idx["color"]]), int(r[idx["qty"]])
        except ValueError:
            continue
        spare = "spare" in idx and len(r) > idx["spare"] and r[idx["spare"]].strip().lower() in ("true", "t", "1", "yes")
        if part and qty > 0:
            rows.append((part, color, qty, spare))
    if not rows:
        raise ValueError("No parts found in the file.")
    return rows


def fetch(slug, name, rows, on_progress=None):
    """Returns (list_info, parts, minifigs, categories, colors, problems) like partlists.fetch_partlist."""
    cats = rebrickable.part_categories()
    time.sleep(1.0)
    all_colors = {c["id"]: c for c in rebrickable._paged("/colors/")}
    time.sleep(1.0)
    nums = sorted({r[0] for r in rows})
    details = {}
    for i in range(0, len(nums), 50):
        for p in rebrickable._paged("/parts/", {"part_nums": ",".join(nums[i:i + 50]), "inc_part_details": 1}):
            details[p["part_num"]] = p
        time.sleep(1.0)

    raw, problems = [], []
    pairs = {}
    for n, (part_num, color_id, qty, spare) in enumerate(rows):
        part = details.get(part_num)
        color = all_colors.get(color_id) or {"id": color_id, "name": f"Colour {color_id}", "rgb": None, "is_trans": False, "external_ids": {}}
        if not part:
            problems.append(f"{part_num} (x{qty}) isn't a Rebrickable part number; added as a placeholder")
            part = {"part_num": part_num, "name": f"Part {part_num}", "part_cat_id": None, "part_img_url": None, "external_ids": {}}
            pc = {}
        else:
            if (part_num, color_id) not in pairs:
                try:
                    pairs[(part_num, color_id)] = rebrickable._get(f"/parts/{part_num}/colors/{color_id}/")
                except rebrickable.RebrickableError as e:
                    if "doesn't know" not in str(e):
                        raise
                    pairs[(part_num, color_id)] = {}  # the part is not known in this colour: keep the plain picture
                time.sleep(1.0)
            pc = pairs[(part_num, color_id)]
        elements = pc.get("elements") or []
        raw.append({"part": part, "color": color, "quantity": qty, "is_spare": spare,
                    "element_id": elements[-1] if elements else None, "part_img_url": pc.get("part_img_url")})
        if on_progress:
            on_progress(n + 1, len(rows))

    parts, colors = rebrickable._convert_parts(raw)
    info = {"set_num": PREFIX + slug, "name": name or slug, "year": None,
            "num_parts": sum(p["quantity"] for p in parts if not p["is_spare"]), "set_img_url": None,
            "kind": "partlist", "source_id": slug}
    return info, parts, [], cats, colors, problems

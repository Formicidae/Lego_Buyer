"""Rebrickable API client: set details, full part inventory with images and external IDs, minifigs."""
import re
import time

import requests

from .config import REBRICKABLE_API_KEY

BASE = "https://rebrickable.com/api/v3/lego"


class RebrickableError(Exception):
    pass


def normalize_set_num(raw: str) -> str:
    s = raw.strip().upper()
    if not s:
        raise RebrickableError("Enter a set number.")
    if re.fullmatch(r"[A-Z0-9]+", s):
        s = f"{s}-1"
    # Rebrickable's B-models and sub-sets end in a lower-case suffix: 4488-1-b1, 75192-1-s1.
    s = re.sub(r"-([BS]\d+)$", lambda m: "-" + m.group(1).lower(), s)
    return s


def _set_num_variants(set_num):
    """The number as normalised, then other spellings of its case, for ids Rebrickable writes unusually."""
    out = []
    for v in (set_num, set_num.lower(), set_num.upper()):
        if v not in out:
            out.append(v)
    return out


def _get(path, params=None):
    if not REBRICKABLE_API_KEY:
        raise RebrickableError("REBRICKABLE_API_KEY is not set on the server.")
    url = path if path.startswith("http") else f"{BASE}{path}"
    for attempt in range(6):
        r = requests.get(url, params=params, headers={"Authorization": f"key {REBRICKABLE_API_KEY}"}, timeout=30)
        if r.status_code == 429:
            # Rebrickable asks for ~1 req/s; back off on throttle.
            time.sleep(2 + attempt)
            continue
        if r.status_code == 404:
            raise RebrickableError("Rebrickable doesn't know that set number.")
        if r.status_code == 401:
            raise RebrickableError("Rebrickable rejected the API key.")
        if r.status_code >= 400:
            raise RebrickableError(f"Rebrickable error {r.status_code}: {r.text[:200]}")
        return r.json()
    raise RebrickableError("Rebrickable is throttling requests; try again in a minute.")


def _paged(path, params=None):
    params = dict(params or {})
    params.setdefault("page_size", 1000)
    url, out = path, []
    while url:
        data = _get(url, params if url == path else None)
        out.extend(data.get("results", []))
        url = data.get("next")
        if url:
            time.sleep(1.0)
    return out


_categories_cache = {"at": 0, "data": {}}


def part_categories():
    if time.time() - _categories_cache["at"] > 86400 or not _categories_cache["data"]:
        rows = _paged("/part_categories/")
        _categories_cache["data"] = {r["id"]: r["name"] for r in rows}
        _categories_cache["at"] = time.time()
    return _categories_cache["data"]


def _convert_parts(raw_parts):
    """Rebrickable inventory rows -> our part dicts (duplicates merged) and the colors they use."""
    parts, colors = [], {}
    for rp in raw_parts:
        part, color = rp.get("part") or {}, rp.get("color") or {}
        if not part.get("part_num"):
            continue
        colors[color.get("id", -1)] = {
            "id": color.get("id", -1),
            "name": color.get("name", "Unknown"),
            "rgb": color.get("rgb"),
            "is_trans": color.get("is_trans"),
            "external_ids": color.get("external_ids"),
        }
        parts.append(
            {
                "part_num": part["part_num"],
                "part_name": part.get("name", part["part_num"]),
                "part_cat_id": part.get("part_cat_id"),
                "color_id": color.get("id", -1),
                "color_name": color.get("name", "Unknown"),
                "color_rgb": color.get("rgb"),
                "element_id": rp.get("element_id"),
                "quantity": rp.get("quantity", 0),
                "is_spare": bool(rp.get("is_spare")),
                # Inventory lists carry the image in the correct color.
                "img_url": rp.get("part_img_url") or part.get("part_img_url"),
                "external_ids": {
                    "part": part.get("external_ids") or {},
                    "color": color.get("external_ids") or {},
                },
            }
        )

    # Merge duplicate (part, color, spare) rows — Rebrickable occasionally lists them separately.
    merged = {}
    for p in parts:
        k = (p["part_num"], p["color_id"], p["is_spare"])
        if k in merged:
            merged[k]["quantity"] += p["quantity"]
        else:
            merged[k] = p
    return list(merged.values()), colors



def minifig_search(q: str, limit=12):
    """Rebrickable minifigs matching a search term (name, or sometimes an external ID)."""
    data = _get("/minifigs/", {"search": q, "page_size": limit, "ordering": "-num_parts"})
    return [
        {"fig_num": m["set_num"], "name": m.get("name"), "num_parts": m.get("num_parts"), "img_url": m.get("set_img_url")}
        for m in data.get("results", [])
    ]


def fetch_minifig(fig_num: str):
    """A minifigure as a list: its parts, plus the figure itself as a whole unit."""
    info = _get(f"/minifigs/{fig_num}/")
    time.sleep(1.0)
    raw_parts = _paged(f"/minifigs/{fig_num}/parts/", {"inc_part_details": 1, "inc_color_details": 1})
    time.sleep(1.0)
    parts, colors = _convert_parts(raw_parts)
    figs = [{"fig_num": info["set_num"], "name": info.get("name", fig_num), "quantity": 1, "img_url": info.get("set_img_url")}]
    list_info = {
        "set_num": info["set_num"], "name": info.get("name", fig_num), "year": None, "num_parts": info.get("num_parts"),
        "set_img_url": info.get("set_img_url"), "kind": "minifig", "source_id": info["set_num"],
    }
    return list_info, parts, figs, part_categories(), colors


# Rebrickable files brick-built figures (destroyer droids, creatures, mechs) as "minifigs", which keeps
# their pieces out of the set's parts list: 7203 Jedi Defense I is 58 pieces but lists only two loose
# parts, the rest being two droidekas. A real minifigure is a handful of parts, so anything this big is
# treated as ordinary building: its pieces join the set's parts and it is not offered as a whole figure.
BRICK_BUILT_MIN_PARTS = 10


def _split_brick_built(raw_figs):
    """-> (figures to keep whole, raw part rows of the brick-built ones multiplied by how many the set has)."""
    whole, rows = [], []
    for f in raw_figs:
        fig, qty = f.get("set_num"), f.get("quantity") or 1
        if not fig:
            continue
        n = f.get("num_parts")
        try:
            if n is None:
                n = (_get(f"/minifigs/{fig}/") or {}).get("num_parts") or 0
                time.sleep(1.0)
            if n < BRICK_BUILT_MIN_PARTS:
                whole.append(f)
                continue
            fig_rows = _paged(f"/minifigs/{fig}/parts/", {"inc_part_details": 1, "inc_color_details": 1})
            time.sleep(1.0)
        except RebrickableError:
            whole.append(f)  # can't tell: leave it as a figure, as before
            continue
        rows += [{**r, "quantity": (r.get("quantity") or 0) * qty, "is_spare": False} for r in fig_rows]
    return whole, rows


def fetch_set(set_num: str):
    """Returns (set_info, parts, minifigs, categories, colors)."""
    info = None
    for i, cand in enumerate(_set_num_variants(set_num)):
        try:
            info = _get(f"/sets/{cand}/")
            set_num = cand
            break
        except RebrickableError as e:
            if "doesn't know" not in str(e) or i == len(_set_num_variants(set_num)) - 1:
                raise
            time.sleep(1.0)
    time.sleep(1.0)
    raw_parts = _paged(f"/sets/{set_num}/parts/", {"inc_part_details": 1, "inc_color_details": 1})
    time.sleep(1.0)
    raw_figs = _paged(f"/sets/{set_num}/minifigs/")
    time.sleep(1.0)
    cats = part_categories()
    raw_figs, built_rows = _split_brick_built(raw_figs)
    parts, colors = _convert_parts(raw_parts + built_rows)

    figs = [
        {
            "fig_num": f.get("set_num"),
            "name": f.get("set_name", f.get("set_num")),
            "quantity": f.get("quantity", 1),
            "img_url": f.get("set_img_url"),
        }
        for f in raw_figs
        if f.get("set_num")
    ]

    set_info = {
        "set_num": info["set_num"],
        "name": info.get("name", set_num),
        "year": info.get("year"),
        "num_parts": info.get("num_parts"),
        "set_img_url": info.get("set_img_url"),
    }
    return set_info, parts, figs, cats, colors


def element(element_id):
    """LEGO element id -> (part_num, color_id) via Rebrickable, or None if unknown."""
    try:
        data = _get(f"/elements/{element_id}/")
    except RebrickableError as e:
        if "know" in str(e) or "404" in str(e):
            return None
        raise
    part = (data.get("part") or {}).get("part_num")
    color = (data.get("color") or {}).get("id")
    if not part:
        return None
    time.sleep(0.6)  # stay under Rebrickable's rate limit when resolving many in a row
    return part, color


def part_color_elements(part_num, color_id):
    """All LEGO element ids Rebrickable knows for a part in a colour (instruction books may use any of them)."""
    try:
        data = _get(f"/parts/{part_num}/colors/{color_id}/")
    except RebrickableError as e:
        if "know" in str(e) or "404" in str(e):
            return []
        raise
    time.sleep(0.6)
    return [str(e) for e in (data.get("elements") or [])]

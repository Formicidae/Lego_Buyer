"""BrickOwl: read the account's wishlists and turn their lots into the same part rows a set uses.

Wishlist endpoints need only the API key (no catalog approval). Each lot is mapped to a Rebrickable
part + color through Rebrickable's own BrickOwl cross-references, so wishlist parts get the same
images, element IDs and LEGO prices as set parts.
"""
import re
import time

import requests

from . import rebrickable
from .config import BRICKOWL_API_KEY

BASE = "https://api.brickowl.com/v1"


class BrickOwlError(Exception):
    pass


def _get(path, **params):
    if not BRICKOWL_API_KEY:
        raise BrickOwlError("BRICKOWL_API_KEY is not set on the server.")
    r = requests.get(f"{BASE}/{path}", params={"key": BRICKOWL_API_KEY, **params}, timeout=30)
    if r.status_code == 401 or r.status_code == 403:
        raise BrickOwlError("BrickOwl rejected the API key.")
    if r.status_code >= 400:
        raise BrickOwlError(f"BrickOwl error {r.status_code}: {r.text[:200]}")
    data = r.json()
    if isinstance(data, dict) and data.get("error"):
        raise BrickOwlError(str(data["error"]))
    return data


def _as_list(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        # Some BrickOwl endpoints return {id: {...}} maps or {"lots": [...]} wrappers.
        for k in ("lists", "wishlists", "lots", "results"):
            if isinstance(data.get(k), list):
                return data[k]
        if all(isinstance(v, dict) for v in data.values()):
            return list(data.values())
    return []


def wishlists():
    out = []
    for w in _as_list(_get("wishlist/lists")):
        wid = w.get("wishlist_id") or w.get("id")
        if wid is None:
            continue
        out.append({"wishlist_id": str(wid), "name": w.get("name") or f"Wishlist {wid}", "description": w.get("description") or "", "count": w.get("lot_count") or w.get("count")})
    return out


def wishlist_lots(wishlist_id):
    return _as_list(_get("wishlist/lots", wishlist_id=wishlist_id))


_BOID_RE = re.compile(r"^(\d+)(?:-(\d+))?$")


def split_boid(boid):
    """'44980-38' -> ('44980', '38'); '44980' -> ('44980', None)."""
    m = _BOID_RE.match(str(boid or "").strip())
    return (m.group(1), m.group(2)) if m else (None, None)


# ---------- Rebrickable cross-reference ----------

_color_map = {"at": 0, "bo_to_rb": {}, "colors": {}}


def _colors():
    """BrickOwl color id -> Rebrickable color row."""
    if time.time() - _color_map["at"] > 86400 or not _color_map["bo_to_rb"]:
        rows = rebrickable._paged("/colors/")
        bo_to_rb, colors = {}, {}
        for c in rows:
            colors[c["id"]] = c
            ext = (c.get("external_ids") or {}).get("BrickOwl") or {}
            for bo_id in ext.get("ext_ids") or []:
                bo_to_rb.setdefault(str(bo_id), c)
        _color_map.update({"at": time.time(), "bo_to_rb": bo_to_rb, "colors": colors})
    return _color_map


_part_cache = {}


def part_by_boid(base_boid):
    if base_boid in _part_cache:
        return _part_cache[base_boid]
    data = rebrickable._get("/parts/", {"brickowl_id": base_boid, "inc_part_details": 1, "page_size": 5})
    results = data.get("results") or []
    part = results[0] if results else None
    _part_cache[base_boid] = part
    return part


def part_color_details(part_num, color_id):
    try:
        return rebrickable._get(f"/parts/{part_num}/colors/{color_id}/")
    except rebrickable.RebrickableError:
        return None


def lot_to_part(lot, cats):
    """Map one wishlist lot to a set_parts-shaped dict. Returns (part_or_None, reason)."""
    boid = lot.get("boid") or lot.get("item_boid") or ""
    base, boid_color = split_boid(boid)
    if not base:
        return None, f"no BOID on lot {lot.get('lot_id')}"
    qty = int(lot.get("minimum_quantity") or lot.get("qty") or lot.get("quantity") or 1)
    bo_color = str(lot.get("color_id") or lot.get("colour_id") or boid_color or "")

    part = part_by_boid(base)
    time.sleep(1.0)
    if not part:
        return None, f"BOID {base} ({lot.get('name') or '?'}) not in Rebrickable"
    cmap = _colors()
    color = cmap["bo_to_rb"].get(bo_color)
    if not color:
        # Non-part items (minifigs, sets) or unknown color: keep it, uncolored.
        color = {"id": -1, "name": lot.get("color_name") or "Unknown color", "rgb": None, "external_ids": {}}
    img, element = part.get("part_img_url"), None
    if color["id"] != -1:
        det = part_color_details(part["part_num"], color["id"])
        time.sleep(1.0)
        if det:
            img = det.get("part_img_url") or img
            els = det.get("elements") or []
            if els:
                element = str(max(els, key=lambda e: int(e) if str(e).isdigit() else 0))
    return {
        "part_num": part["part_num"],
        "part_name": part.get("name", part["part_num"]),
        "part_cat_id": part.get("part_cat_id"),
        "color_id": color["id"],
        "color_name": color.get("name", "Unknown"),
        "color_rgb": color.get("rgb"),
        "element_id": element,
        "quantity": qty,
        "is_spare": False,
        "img_url": img,
        "external_ids": {"part": part.get("external_ids") or {}, "color": color.get("external_ids") or {}, "boid": boid, "lot_id": lot.get("lot_id")},
    }, None


def fetch_wishlist(wishlist_id, on_progress=None):
    """Returns (list_info, parts, minifigs, categories, colors, problems)."""
    lists = {w["wishlist_id"]: w for w in wishlists()}
    info = lists.get(str(wishlist_id)) or {"wishlist_id": str(wishlist_id), "name": f"Wishlist {wishlist_id}"}
    lots = wishlist_lots(wishlist_id)
    cats = rebrickable.part_categories()
    parts, problems, colors = [], [], {}
    for i, lot in enumerate(lots):
        p, why = lot_to_part(lot, cats)
        if p:
            parts.append(p)
            colors[p["color_id"]] = {"id": p["color_id"], "name": p["color_name"], "rgb": p["color_rgb"], "is_trans": False, "external_ids": p["external_ids"].get("color")}
        else:
            problems.append(why)
        if on_progress:
            on_progress(i + 1, len(lots))
    merged = {}
    for p in parts:
        k = (p["part_num"], p["color_id"], False)
        if k in merged:
            merged[k]["quantity"] += p["quantity"]
        else:
            merged[k] = p
    list_info = {
        "set_num": f"bo-{info['wishlist_id']}",
        "name": info["name"],
        "year": None,
        "num_parts": sum(p["quantity"] for p in merged.values()),
        "set_img_url": None,
        "kind": "wishlist",
        "source_id": str(info["wishlist_id"]),
    }
    return list_info, list(merged.values()), [], cats, colors, problems

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
    return s


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


def fetch_set(set_num: str):
    """Returns (set_info, parts, minifigs, categories, colors)."""
    info = _get(f"/sets/{set_num}/")
    time.sleep(1.0)
    raw_parts = _paged(f"/sets/{set_num}/parts/", {"inc_part_details": 1, "inc_color_details": 1})
    time.sleep(1.0)
    raw_figs = _paged(f"/sets/{set_num}/minifigs/")
    time.sleep(1.0)
    cats = part_categories()

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
    parts = list(merged.values())

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

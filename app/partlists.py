"""Bundled parts lists: a hand-made list of LEGO element IDs (e.g. the contents of specific numbered
bags of a set) shipped with the app as app/partlists/<slug>.json.

Each element is looked up on Rebrickable (/lego/elements/{id}/), which gives the part, the colour and
the image, so a parts list gets the same pictures, LEGO prices and sourcing as a set. Elements that
Rebrickable doesn't know are kept as placeholder rows (using the list's own description) so they still
show up on the checklist, and are noted in the list's import notes.

JSON shape:
{
  "slug": "75382-bags-1-11", "name": "...", "set_num": "75382-1", "description": "...",
  "bags": [1, 2, ...], "autoimport": true,
  "minifigs": [{"match": "TIE Pilot", "quantity": 1, "elements": ["6487885", ...]}],
  "items": [{"element_id": "4211398", "quantity": 15, "bags": {"4": 2, "9": 7}, "category": "standard",
             "guess": "Light Bluish Gray plate 1x2", "note": "..."}]
}
Items whose element_id appears in a minifig's "elements" are folded into that whole figure (the app
sources minifigures whole), so they are not listed as loose parts.
"""
import json
import time
from pathlib import Path

from . import rebrickable

DIR = Path(__file__).with_name("partlists")
PREFIX = "pl-"


def available():
    out = []
    for f in sorted(DIR.glob("*.json")):
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        slug = d.get("slug") or f.stem
        out.append({
            "slug": slug,
            "set_num": PREFIX + slug,
            "name": d.get("name") or slug,
            "description": d.get("description") or "",
            "num_parts": sum(int(i.get("quantity") or 0) for i in d.get("items", [])),
            "lots": len(d.get("items", [])),
            "autoimport": bool(d.get("autoimport")),
        })
    return out


def load(slug):
    f = DIR / f"{slug}.json"
    if not f.is_file():
        raise rebrickable.RebrickableError(f"No bundled parts list called {slug!r}.")
    return json.loads(f.read_text())


def _element(element_id):
    """Rebrickable element -> (part, color, img) or None when unknown."""
    try:
        d = rebrickable._get(f"/elements/{element_id}/")
    except rebrickable.RebrickableError as e:
        if "doesn't know" in str(e) or "404" in str(e):
            return None
        raise
    return d.get("part") or {}, d.get("color") or {}, d.get("element_img_url") or d.get("part_img_url")


def fetch_partlist(slug, on_progress=None):
    """Returns (list_info, parts, minifigs, categories, colors, problems) like brickowl.fetch_wishlist."""
    d = load(slug)
    fig_defs = d.get("minifigs") or []
    fig_elements = {e for fd in fig_defs for e in fd.get("elements", [])}
    items = [i for i in d.get("items", []) if str(i.get("element_id")) not in fig_elements]

    set_img = None
    if d.get("set_num"):
        try:
            info = rebrickable._get(f"/sets/{d['set_num']}/")
            set_img = info.get("set_img_url")
            time.sleep(1.0)
        except rebrickable.RebrickableError:
            pass

    cats = rebrickable.part_categories()
    parts, colors, problems = [], {}, []
    total = len(items) + len(fig_defs)
    for n, it in enumerate(items):
        eid = str(it["element_id"])
        qty = int(it.get("quantity") or 0)
        bags = it.get("bags") or {}
        bag_note = ", ".join(f"bag {b}: {q}" for b, q in sorted(bags.items(), key=lambda kv: int(kv[0])))
        res = _element(eid)
        time.sleep(1.0)
        if res and res[0].get("part_num"):
            part, color, img = res
            colors[color.get("id", -1)] = {"id": color.get("id", -1), "name": color.get("name", "Unknown"), "rgb": color.get("rgb"),
                                           "is_trans": color.get("is_trans"), "external_ids": color.get("external_ids")}
            parts.append({
                "part_num": part["part_num"], "part_name": part.get("name", part["part_num"]), "part_cat_id": part.get("part_cat_id"),
                "color_id": color.get("id", -1), "color_name": color.get("name", "Unknown"), "color_rgb": color.get("rgb"),
                "element_id": eid, "quantity": qty, "is_spare": False, "img_url": img or part.get("part_img_url"),
                "external_ids": {"part": part.get("external_ids") or {}, "color": color.get("external_ids") or {},
                                 "bags": bags, "note": it.get("note") or "", "category": it.get("category") or "standard"},
            })
        else:
            # Keep it on the list so it isn't forgotten; the guess text is what the instructions show.
            guess = it.get("guess") or f"element {eid}"
            problems.append(f"{eid} ({guess}, x{qty}{', ' + bag_note if bag_note else ''}) isn't on Rebrickable; added as a placeholder")
            colors[-1] = {"id": -1, "name": "Unknown color", "rgb": None, "is_trans": False, "external_ids": {}}
            parts.append({
                "part_num": f"el{eid}", "part_name": f"{guess} (element {eid})", "part_cat_id": None,
                "color_id": -1, "color_name": "Unknown color", "color_rgb": None, "element_id": eid, "quantity": qty,
                "is_spare": False, "img_url": None,
                "external_ids": {"part": {}, "color": {}, "bags": bags, "note": it.get("note") or "", "category": it.get("category") or "standard", "placeholder": True},
            })
        if on_progress:
            on_progress(n + 1, total)

    figs = []
    if fig_defs and d.get("set_num"):
        try:
            raw = rebrickable._paged(f"/sets/{d['set_num']}/minifigs/")
            time.sleep(1.0)
        except rebrickable.RebrickableError as e:
            raw = []
            problems.append(f"couldn't read the set's minifigures from Rebrickable: {e}")
        for fd in fig_defs:
            want = (fd.get("match") or "").lower()
            hit = next((f for f in raw if want and want in (f.get("set_name") or "").lower()), None)
            if hit:
                figs.append({"fig_num": hit["set_num"], "name": hit.get("set_name", hit["set_num"]), "quantity": int(fd.get("quantity") or 1), "img_url": hit.get("set_img_url")})
            else:
                problems.append(f"minifigure {fd.get('match')!r} not found in Rebrickable's list for {d['set_num']}; its parts are {', '.join(fd.get('elements', []))}")
        if on_progress:
            on_progress(total, total)

    # Merge duplicate (part, colour) rows, keeping the per-bag breakdown.
    merged = {}
    for p in parts:
        k = (p["part_num"], p["color_id"], False)
        if k in merged:
            merged[k]["quantity"] += p["quantity"]
            mb = merged[k]["external_ids"].setdefault("bags", {})
            for b, q in (p["external_ids"].get("bags") or {}).items():
                mb[b] = mb.get(b, 0) + q
        else:
            merged[k] = p

    bags = d.get("bags") or []
    list_info = {
        "set_num": PREFIX + slug,
        "name": d.get("name") or slug,
        "year": None,
        "num_parts": sum(p["quantity"] for p in merged.values()),
        "set_img_url": set_img,
        "kind": "partlist",
        "source_id": slug,
    }
    return list_info, list(merged.values()), figs, cats, colors, problems

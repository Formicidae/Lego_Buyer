"""Colour swaps: for the parts of a list that cost the most, which other colours the same part comes in,
how common each is, and what it costs where a price is known. A part that only ever appeared in one or
two sets (a reddish brown 10 x 10 dish) can cost fifty times what the same mould costs in grey.

Rebrickable's /parts/<part>/colors/ gives every colour a part was made in with the number of sets it
appeared in, which stands in for price when there is none. Prices come from what the app already knows
(another list using that part and colour) and, when BrickLink keys are set, from its price guide.
The chosen swaps are written out as a new parts list so the original stays as it is.
"""
import json
import time

from . import bricklink, brickowl, db, rebrickable

MAX_LOTS = 40        # most expensive lots looked at in one scan
PRICED_PER_PART = 6  # alternative colours per part sent to BrickLink's price guide
_TTL = 7 * 86400


def part_colors(part_num):
    """[{color_id, color_name, num_sets, num_set_parts, part_img_url}] for a part, cached for a week."""
    key = f"partcolors:{part_num}"
    try:
        hit = json.loads(db.setting_get(key) or "null")
    except ValueError:
        hit = None
    if hit and time.time() - hit.get("at", 0) < _TTL:
        return hit["rows"]
    rows = [{"color_id": r["color_id"], "color_name": r.get("color_name"), "num_sets": r.get("num_sets") or 0,
             "num_set_parts": r.get("num_set_parts") or 0, "part_img_url": r.get("part_img_url")}
            for r in rebrickable._paged(f"/parts/{part_num}/colors/")]
    db.setting_set(key, json.dumps({"at": time.time(), "rows": rows}))
    time.sleep(1.0)
    return rows


def _rgb(hexstr):
    try:
        h = (hexstr or "").lstrip("#")
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except (ValueError, IndexError):
        return None


def color_distance(a, b):
    """How different two colours look, 0 = same (weighted RGB, good enough to rank 'closest first')."""
    a, b = _rgb(a), _rgb(b)
    if not a or not b:
        return 1e9
    rm = (a[0] + b[0]) / 2
    return (((2 + rm / 256) * (a[0] - b[0]) ** 2 + 4 * (a[1] - b[1]) ** 2 + (2 + (255 - rm) / 256) * (a[2] - b[2]) ** 2) ** 0.5)


def known_prices(part_num):
    """{color_id: lowest marketplace price the app has stored for this part in that colour}."""
    out = {}
    for r in db.q("SELECT color_id, bo_price, bl_price FROM set_parts WHERE part_num = ?", (part_num,)):
        for v in (r["bo_price"], r["bl_price"]):
            if v is not None and (r["color_id"] not in out or v < out[r["color_id"]]):
                out[r["color_id"]] = v
    return out


def pick(cur_price, cur_sets, alts):
    """The suggested swap: of the colours that are clearly cheaper (by price when known, else clearly more
    common), the one that looks closest to the original. None when nothing qualifies."""
    good = []
    for a in alts:
        if a.get("price") is not None and cur_price:
            ok = a["price"] <= cur_price * 0.6
        else:
            ok = a["num_sets"] >= max(8, 4 * (cur_sets or 1))
        if ok:
            good.append(a)
    return min(good, key=lambda a: a["distance"])["color_id"] if good else None


def scan(items, price_of, min_price, on_progress=None):
    """items: sourcing.needed_items rows. price_of(item) -> unit price or None.
    -> {"lots": [...], "skipped": n over the threshold but past MAX_LOTS, "bricklink": bool}"""
    over = [(price_of(i), i) for i in items]
    over = sorted(((p, i) for p, i in over if p is not None and p >= min_price), key=lambda x: -x[0] * x[1]["qty"])
    todo, skipped = over[:MAX_LOTS], max(0, len(over) - MAX_LOTS)
    cmap = brickowl._colors()["colors"]  # every Rebrickable colour, with its BrickLink id
    use_bl = bricklink.configured()
    lots = []
    for n, (price, i) in enumerate(todo):
        try:
            cols = part_colors(i["part_num"])
        except rebrickable.RebrickableError:
            cols = []
        known = known_prices(i["part_num"])
        cur_rgb = (cmap.get(i["color_id"]) or {}).get("rgb")
        cur_sets = next((c["num_sets"] for c in cols if c["color_id"] == i["color_id"]), 0)
        alts = []
        for c in cols:
            if c["color_id"] == i["color_id"]:
                continue
            col = cmap.get(c["color_id"]) or {}
            alts.append({"color_id": c["color_id"], "color_name": c["color_name"] or col.get("name"), "rgb": col.get("rgb"),
                         "num_sets": c["num_sets"], "img_url": c["part_img_url"], "price": known.get(c["color_id"]),
                         "price_source": "list" if c["color_id"] in known else None,
                         "distance": round(color_distance(cur_rgb, col.get("rgb")), 1)})
        if use_bl:
            bl_no = (((i.get("external_ids") or {}).get("part") or {}).get("BrickLink") or [i["part_num"]])[0]
            for a in sorted((a for a in alts if a["price"] is None), key=lambda a: -a["num_sets"])[:PRICED_PER_PART]:
                bl_col = ((((cmap.get(a["color_id"]) or {}).get("external_ids") or {}).get("BrickLink") or {}).get("ext_ids") or [None])[0]
                if bl_col is None:
                    continue
                try:
                    g = bricklink.price_guide("part", bl_no, bl_col)
                except Exception:
                    continue
                if g.get("qty_avg") or g.get("avg"):
                    a["price"], a["price_source"] = round(g.get("qty_avg") or g["avg"], 3), "bricklink"
        alts.sort(key=lambda a: a["distance"])
        lots.append({"key": i["key"], "part_num": i["part_num"], "name": i["name"], "color_id": i["color_id"], "color_name": i["color_name"],
                     "rgb": cur_rgb, "qty": i["qty"], "price": round(price, 3), "num_sets": cur_sets, "img_url": i.get("img_url"),
                     "alts": alts, "suggest": pick(price, cur_sets, alts)})
        if on_progress:
            on_progress(n + 1, len(todo))
    return {"lots": lots, "skipped": skipped, "bricklink": use_bl, "min": min_price}


def rows_with_swaps(items, swaps):
    """Rebrickable-CSV rows (part, colour, qty, spare) for everything still needed, with {key: color_id} applied."""
    merged = {}
    for i in items:
        color = int(swaps.get(i["key"], i["color_id"]))
        k = (i["part_num"], color)
        merged[k] = merged.get(k, 0) + i["qty"]
    return [(part, color, qty, False) for (part, color), qty in merged.items()]

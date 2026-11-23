"""Marketplace orders: what was bought, and which list each piece is for.

A saved BrickOwl order page is matched lot by lot against the parts a list (or several lists opened
together) still needs. Each lot's quantity is shared out to the lists in their order, up to what each
still needs; the result is stored with the order so the sorting sheet can be printed again after the
pieces have been marked as owned (when nothing is "still needed" any more).
"""
import re
import time

from . import db, sourcing


def _cap(m, count_alt):
    return max(0, m["quantity"] - m["owned"] - m["found_exact"] - (m["found_alt"] if count_alt else 0))


def _members(p, info):
    return p.get("members") or [{"set_num": info["set_num"], "list_name": info["name"], "key": p["key"], "quantity": p["quantity"],
                                 "owned": p["owned"], "found_exact": p["found_exact"], "found_alt": p["found_alt"]}]


_FIG_NOISE = {"minifigure", "minifig", "with", "and", "the", "a", "lego", "star", "wars"}


def _words(name):
    return {w for w in re.findall(r"[a-z0-9]+", (name or "").lower()) if w not in _FIG_NOISE}


def _fig_for(lot, figs, info, count_alt, used):
    """The minifigure a lot is for: BrickOwl and Rebrickable name figures differently, so every word of the
    shorter name has to be in the longer one, and a figure a list still needs wins over one it doesn't."""
    want = _words(lot["name"])
    if not want:
        return None
    best = None
    for f in figs:
        have = _words(f["name"])
        if not have or not (want <= have or have <= want):
            continue
        need = sum(max(0, _cap(m, count_alt) - used.get((m["set_num"], m["key"]), 0)) for m in _members(f, info))
        score = (1 if need > 0 else 0, -len(want ^ have))
        if best is None or score > best[0]:
            best = (score, f)
    return best[1] if best else None


def match(info, parts, figs, order):
    """-> {"lists": [{set_num, name}], "rows": [...]}: one row per order lot with "split" [{set_num, key, qty}],
    "extra" (pieces no list needs) and "note"."""
    count_alt = bool(info.get("count_alt", 1))
    lists = [{"set_num": s["set_num"], "name": s["name"]} for s in info.get("lists") or [info]]
    exact, loose = {}, {}
    usable = [p for p in parts if not p["is_spare"] or info.get("include_spares")]
    for p in usable:
        ex, lo = sourcing._item_ids(p)
        for pid in ex:
            exact.setdefault(pid, []).append(p)
        for pid in lo:
            loose.setdefault(pid, []).append(p)
    used, rows = {}, []
    for lot in order["lots"]:
        row = {**lot, "split": [], "extra": 0, "note": "", "our_name": None, "color_name": None, "color_rgb": None, "img_url": None}
        hit = []
        if lot.get("minifig"):
            f = _fig_for(lot, figs, info, count_alt, used)
            if f:
                hit = [f]
                row.update(our_name=f["name"], img_url=f.get("img_url"), note="matched by name")
        else:
            ids = [str(x) for x in lot.get("ids") or []]
            name = lot["name"].lower()
            uniq = lambda c: list({x["key"]: x for x in c}.values())
            right_colour = lambda c: [p for p in uniq(c) if any(name.startswith(cn + " ") for cn in sourcing._bo_color_names(p))]
            tight = [p for pid in ids for p in exact.get(pid, [])]
            wide = [p for pid in ids for p in exact.get(sourcing._base(pid), []) + loose.get(pid, []) + loose.get(sourcing._base(pid), [])]
            hit = right_colour(tight) or right_colour(wide)
            if not hit and len(uniq(tight + wide)) == 1:
                hit, row["note"] = uniq(tight + wide), "matched by part number only: check the colour"
            hit.sort(key=lambda p: p["is_spare"])
            if hit:
                p = hit[0]
                row.update(our_name=p["part_name"], color_name=p["color_name"], color_rgb=p.get("color_rgb"), img_url=p.get("img_url"))
        left = lot["qty"]
        for it in hit:
            for m in _members(it, info):
                k = (m["set_num"], m["key"])
                give = min(left, _cap(m, count_alt) - used.get(k, 0))
                if give > 0:
                    used[k] = used.get(k, 0) + give
                    left -= give
                    prev = next((s for s in row["split"] if s["set_num"] == m["set_num"] and s["key"] == m["key"]), None)
                    if prev:
                        prev["qty"] += give
                    else:
                        row["split"].append({"set_num": m["set_num"], "key": m["key"], "qty": give})
        row["extra"] = left
        row["matched"] = bool(hit)
        if not hit:
            row["note"] = "not on these lists"
        elif left and not row["split"]:
            row["note"] = "on the list, but none still needed"
        elif left:
            row["note"] = (row["note"] + "; " if row["note"] else "") + f"{left} more than still needed"
        rows.append(row)
    return {"lists": lists, "rows": rows}


def summary(o):
    """What the Buy tab shows for a stored order."""
    res = o["data"]["result"]
    per = {l["set_num"]: {"set_num": l["set_num"], "name": l["name"], "pieces": 0, "lots": 0} for l in res["lists"]}
    for r in res["rows"]:
        for s in r["split"]:
            if s["set_num"] in per:
                per[s["set_num"]]["pieces"] += s["qty"]
                per[s["set_num"]]["lots"] += 1
    rows = res["rows"]
    return {
        "id": o["id"], "order_no": o["order_no"], "store": o["store"], "date": o["data"]["order"].get("date"), "total": o["total"],
        "lots": len(rows), "pieces": sum(r["qty"] for r in rows), "placed": sum(s["qty"] for r in rows for s in r["split"]),
        "per_list": list(per.values()), "applied_at": o["applied_at"],
        "unmatched": [{"name": r["name"], "qty": r["qty"]} for r in rows if not r["matched"]],
        "extra": [{"name": r["name"], "qty": r["extra"]} for r in rows if r["matched"] and r["extra"]],
        "flagged": [{"name": r["name"], "note": r["note"]} for r in rows if r["matched"] and r["split"] and ("by name" in r["note"] or "number only" in r["note"])],
    }


def apply(o, who, undo=False):
    """Add (or take back) every placed piece on its list's owned count."""
    n = 0
    for r in o["data"]["result"]["rows"]:
        for s in r["split"]:
            db.adjust_progress(s["set_num"], s["key"], "owned", -s["qty"] if undo else s["qty"], who)
            n += s["qty"]
    db.set_order_applied(o["id"], None if undo else time.time())
    return n

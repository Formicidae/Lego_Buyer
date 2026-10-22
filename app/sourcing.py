"""Where to buy what's left: LEGO Pick a Brick vs the marketplaces (BrickLink/BrickOwl).

Marketplace cost per part uses the BrickLink price guide (quantity-weighted average of current
listings, new condition) as the estimate for both BrickLink and BrickOwl; the real cart will differ
by seller but this is good enough to decide which side of the line a part belongs on.
"""
from . import lego

DEFAULTS = {"market_shipping": 5.00, "market_min_order": 0.0, "max_lead_days": 60}


def _remaining(p, count_alt):
    return max(0, p["quantity"] - p["owned"] - p["found_exact"] - (p["found_alt"] if count_alt else 0))


def needed_items(set_info, parts):
    count_alt = bool(set_info.get("count_alt", 1))
    out = []
    for p in parts:
        if p["is_spare"] and not set_info.get("include_spares"):
            continue
        rem = _remaining(p, count_alt)
        if rem <= 0:
            continue
        sold = p.get("lego_price") is not None and p.get("lego_available")
        out.append({
            "key": p["key"], "part_num": p["part_num"], "color_id": p["color_id"], "color_name": p["color_name"],
            "name": p["part_name"], "element_id": p.get("element_id"), "qty": rem, "img_url": p.get("img_url"),
            "lego_price": p.get("lego_price") if sold else None, "tier": (p.get("tier") if sold else None),
            "lego_checked": p.get("price_checked_at") is not None,
            "bl_price": p.get("bl_price"), "bl_qty": p.get("bl_qty"), "bl_checked": p.get("bl_checked_at") is not None,
            "external_ids": p.get("external_ids") or {},
        })
    return out


def _lego_cost(items):
    plan = lego.plan_fees([{"price": i["lego_price"], "tier": i["tier"] or "Unknown", "qty": i["qty"]} for i in items])
    return plan


def _market_cost(items, opts):
    parts = sum((i["bl_price"] or 0) * i["qty"] for i in items)
    unpriced = [i for i in items if i["bl_price"] is None]
    ship = opts["market_shipping"] if items else 0.0
    return {"parts": round(parts, 2), "shipping": ship, "total": round(parts + ship, 2), "lots": len(items), "unpriced": len(unpriced)}


def scenarios(items, opts=None):
    """Three ways to split the order, each fully costed."""
    opts = {**DEFAULTS, **(opts or {})}
    lego_ok = [i for i in items if i["lego_price"] is not None]
    lego_no = [i for i in items if i["lego_price"] is None]

    def build(name, lego_items, market_items, note):
        L = _lego_cost(lego_items) if lego_items else None
        M = _market_cost(market_items, opts)
        total = (L["total"] if L else 0) + M["total"]
        return {"name": name, "note": note, "lego": L, "market": M, "lego_keys": [i["key"] for i in lego_items],
                "market_keys": [i["key"] for i in market_items], "total": round(total, 2)}

    out = [
        build("LEGO where possible", lego_ok, lego_no, "Everything LEGO sells from Pick a Brick; the rest from one marketplace order."),
        build("All marketplace", [], items, "One BrickLink/BrickOwl order for everything (LEGO-only prices are irrelevant)."),
    ]
    # Cheapest per part, then re-check LEGO's fee thresholds: if a LEGO category ends up tiny, push it to the market.
    cheap_lego, cheap_market = [], []
    for i in items:
        if i["lego_price"] is None:
            cheap_market.append(i)
        elif i["bl_price"] is None or i["lego_price"] <= i["bl_price"]:
            cheap_lego.append(i)
        else:
            cheap_market.append(i)
    plan = _lego_cost(cheap_lego) if cheap_lego else None
    if plan:
        for tier_name in ("Bestseller", "Standard"):
            t = plan["tiers"].get(tier_name)
            if t and t.get("fee") and t["subtotal"] < t["fee"]:
                # Paying a $7 fee on a few dollars of parts never wins; move that category to the marketplace.
                moved = [i for i in cheap_lego if (i["tier"] or "Unknown") == tier_name]
                cheap_lego = [i for i in cheap_lego if (i["tier"] or "Unknown") != tier_name]
                cheap_market += moved
    out.append(build("Cheapest per part", cheap_lego, cheap_market, "Each part from whichever is cheaper, with LEGO categories dropped when the fee would exceed the parts."))
    out.sort(key=lambda s: s["total"])
    return out


def cart_scenarios(items, carts, opts=None):
    """One scenario per imported cart: the cart as-is for the lots it covers, LEGO for whatever's left."""
    opts = {**DEFAULTS, **(opts or {})}
    out = []
    by_key = {i["key"]: i for i in items}
    for c in carts:
        covered = set(c["keys"]) or set(by_key)  # a cart with no key list is assumed to cover the whole marketplace set
        rest = [i for i in items if i["key"] not in covered]
        lego_items = [i for i in rest if i["lego_price"] is not None]
        leftover = [i for i in rest if i["lego_price"] is None]
        L = _lego_cost(lego_items) if lego_items else None
        total = c["total"] + (L["total"] if L else 0)
        out.append({
            "name": f"{c['source']} cart: {c['name']}", "cart_id": c["id"], "url": c.get("url"),
            "note": f"{len(c['stores'])} store{'s' if len(c['stores']) != 1 else ''} from {c['source']}'s cart builder"
                    + (f"; {len(leftover)} lot(s) not covered by anything" if leftover else ""),
            "lego": L, "market": {"parts": c["parts_total"], "shipping": c["shipping_total"], "total": c["total"], "lots": c.get("lots_covered") or len(covered), "unpriced": 0},
            "stores": c["stores"], "lego_keys": [i["key"] for i in lego_items], "market_keys": [k for k in covered if k in by_key],
            "total": round(total, 2), "imported": True,
        })
    return out


def brickowl_wishlist_items(items):
    """BrickOwl BOID + color id per part, for creating a wishlist. Returns (ok, skipped)."""
    ok, skipped = [], []
    for i in items:
        ext = i.get("external_ids") or {}
        boids = (ext.get("part") or {}).get("BrickOwl") or []
        cols = (((ext.get("color") or {}).get("BrickOwl") or {}).get("ext_ids") or [])
        if boids and cols:
            ok.append({"boid": str(boids[0]), "color_id": int(cols[0]), "qty": i["qty"], "key": i["key"], "name": i["name"]})
        else:
            skipped.append(i)
    return ok, skipped


def bricklink_xml(items):
    esc = lambda s: str(s).replace("&", "&amp;").replace("<", "&lt;")
    rows = []
    for i in items:
        ext = i.get("external_ids") or {}
        bl_no = ((ext.get("part") or {}).get("BrickLink") or [i["part_num"]])[0]
        bl_col = (((ext.get("color") or {}).get("BrickLink") or {}).get("ext_ids") or [None])[0]
        rows.append(f"<ITEM><ITEMTYPE>P</ITEMTYPE><ITEMID>{esc(bl_no)}</ITEMID>" + (f"<COLOR>{bl_col}</COLOR>" if bl_col is not None else "")
                    + f"<MINQTY>{i['qty']}</MINQTY><CONDITION>N</CONDITION></ITEM>")
    return "<INVENTORY>\n" + "\n".join(rows) + "\n</INVENTORY>"

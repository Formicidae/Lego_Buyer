"""Where to buy what's left: LEGO Pick a Brick vs the marketplaces (BrickLink/BrickOwl).

Marketplace cost per part uses the actual BrickOwl store price when a Buy Wishlist page has been
imported for that part, otherwise the BrickLink price guide (quantity-weighted average of current
listings, new condition) as the estimate for both marketplaces.
"""
import re

from . import lego

ALLOC_VERSION = 2  # bump when the way cart lots are turned into per-part prices changes
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
            "bo_price": p.get("bo_price"), "bo_store": p.get("bo_store"),
            "external_ids": p.get("external_ids") or {},
        })
    return out


def market_price(i, ctx=None):
    """What one of this part costs on the marketplace: what the imported BrickOwl cart charges for it
    (averaged over the stores it is split across), else BrickLink's price guide."""
    rows = (ctx or {}).get("alloc", {}).get(i["key"])
    if rows:
        q = sum(r["qty"] for r in rows)
        if q:
            return sum(r["qty"] * r["price"] for r in rows) / q
    return i["bo_price"] if i.get("bo_price") is not None else i["bl_price"]


def lego_price(i, opts=None):
    """LEGO's price for one piece after any cash back / discount the user entered."""
    return None if i["lego_price"] is None else i["lego_price"] * (1 - ((opts or {}).get("lego_off") or 0))


def _lego_cost(items, opts=None):
    opts = opts or {}
    plan = lego.plan_fees([{"price": i["lego_price"], "tier": i["tier"] or "Unknown", "qty": i["qty"]} for i in items])
    if opts.get("lego_free"):
        # "Assume the order is big enough": no service fees, no shipping.
        for t in plan["tiers"].values():
            if "fee" in t:
                t["fee"] = 0.0
        plan.update({"service_fees": 0.0, "shipping": 0.0, "waived": True})
    off = opts.get("lego_off") or 0
    plan["cashback"] = round(plan["subtotal"] * off, 2)
    plan["total"] = round(plan["subtotal"] + plan["service_fees"] + plan["shipping"] - plan["cashback"], 2)
    return plan


def _market_cost(items, opts):
    ctx = opts.get("cart")
    parts = sum((market_price(i, ctx) or 0) * i["qty"] for i in items)
    unpriced = [i for i in items if market_price(i, ctx) is None]
    # Shipping: what the user typed, else what the stores these parts come from charge in the imported
    # cart (dropping a store only when none of the parts still come from it), else the flat default.
    ship, how, n_stores = 0.0, None, None
    if items:
        if opts.get("market_shipping") is not None:
            ship, how = opts["market_shipping"], "user"
        else:
            stores = {r["store"] for i in items for r in (ctx or {}).get("alloc", {}).get(i["key"], [])}
            if stores:
                ship, how, n_stores = sum(ctx["shipping"].get(st, 0.0) for st in stores), "cart", len(stores)
            else:
                ship, how = DEFAULTS["market_shipping"], "default"
    return {"parts": round(parts, 2), "shipping": round(ship, 2), "shipping_from": how, "stores": n_stores,
            "total": round(parts + ship, 2), "lots": len(items), "unpriced": len(unpriced),
            "unpriced_names": [f"{i['color_name']} {i['name']}" for i in unpriced[:8]]}


def scenarios(items, opts=None):
    """Three ways to split the order, each fully costed."""
    opts = dict(opts or {})
    ctx = opts.get("cart")
    lego_ok = [i for i in items if i["lego_price"] is not None]
    lego_no = [i for i in items if i["lego_price"] is None]

    def build(name, lego_items, market_items, note):
        L = _lego_cost(lego_items, opts) if lego_items else None
        M = _market_cost(market_items, opts)
        total = (L["total"] if L else 0) + M["total"]
        return {"name": name, "note": note, "lego": L, "market": M, "lego_keys": [i["key"] for i in lego_items],
                "market_keys": [i["key"] for i in market_items], "total": round(total, 2),
                "incomplete": M["unpriced"] > 0}  # marketplace lots with no price make the total a floor, not an estimate

    out = [
        build("LEGO where possible", lego_ok, lego_no, "Everything LEGO sells from Pick a Brick; the rest from the marketplace."),
        build("All marketplace", [], items, "Everything from BrickLink/BrickOwl sellers (LEGO-only prices are irrelevant)."),
    ]
    # Cheapest per part, then re-check LEGO's fee thresholds: if a LEGO category ends up tiny, push it to the market.
    cheap_lego, cheap_market = [], []
    for i in items:
        if i["lego_price"] is None:
            cheap_market.append(i)
        elif market_price(i, ctx) is None or lego_price(i, opts) <= market_price(i, ctx):
            cheap_lego.append(i)
        else:
            cheap_market.append(i)
    plan = _lego_cost(cheap_lego, opts) if cheap_lego else None
    if plan and not opts.get("lego_free"):
        for tier_name in ("Bestseller", "Standard"):
            t = plan["tiers"].get(tier_name)
            if t and t.get("fee") and t["subtotal"] < t["fee"]:
                # Paying a $7 fee on a few dollars of parts never wins; move that category to the marketplace.
                moved = [i for i in cheap_lego if (i["tier"] or "Unknown") == tier_name]
                cheap_lego = [i for i in cheap_lego if (i["tier"] or "Unknown") != tier_name]
                cheap_market += moved
    # With an imported cart we know what each store charges to ship. If everything still coming from one
    # store is also sold by LEGO, and paying LEGO's (higher) prices for those parts costs less than that
    # store's shipping, drop the store.
    why = {}
    alloc = (ctx or {}).get("alloc", {})
    if alloc and opts.get("market_shipping") is None:
        cost = lambda lg, mk: (_lego_cost(lg, opts)["total"] if lg else 0) + _market_cost(mk, opts)["total"]
        while True:
            now, best = cost(cheap_lego, cheap_market), None
            for st in sorted({r["store"] for i in cheap_market for r in alloc.get(i["key"], [])}):
                mine = [i for i in cheap_market if any(r["store"] == st for r in alloc.get(i["key"], []))]
                if not mine or any(i["lego_price"] is None for i in mine):
                    continue
                keys = {i["key"] for i in mine}
                c = cost(cheap_lego + mine, [i for i in cheap_market if i["key"] not in keys])
                if c < now - 0.005 and (best is None or c < best[0]):
                    best = (c, st, mine, keys)
            if not best:
                break
            _, st, mine, keys = best
            cheap_lego += mine
            cheap_market = [i for i in cheap_market if i["key"] not in keys]
            for i in mine:
                why[i["key"]] = st
    sc = build("Cheapest per part", cheap_lego, cheap_market,
               "Each part from whichever is cheaper" + ("" if opts.get("lego_free") else ", with LEGO categories dropped when the fee would exceed the parts")
               + ("; a store is dropped when LEGO can supply everything it would send for less than its shipping" if alloc and opts.get("market_shipping") is None else "") + ".")
    # What to put in the LEGO basket, and what each line saves against the marketplace.
    detail = []
    for i in cheap_lego:
        lp, mp = lego_price(i, opts), market_price(i, ctx)
        detail.append({"key": i["key"], "name": f"{i['color_name']} {i['name']}", "qty": i["qty"], "lego": round(lp, 4),
                       "market": None if mp is None else round(mp, 4), "saving": None if mp is None else round((mp - lp) * i["qty"], 2),
                       "drops_store": why.get(i["key"])})
    detail.sort(key=lambda d: (d["drops_store"] is not None, -(d["saving"] if d["saving"] is not None else 1e9)))
    sc["lego_detail"] = detail
    if alloc:
        still = {r["store"] for i in cheap_market for r in alloc.get(i["key"], [])}
        sc["stores_dropped"] = [{"store": st, "shipping": sh} for st, sh in ctx["shipping"].items() if st not in still and any(
            r["store"] == st for rows in alloc.values() for r in rows)]
    out.append(sc)
    out.sort(key=lambda s: (s.get("incomplete", False), s["total"]))
    return out


def cart_scenarios(items, carts, opts=None):
    """One scenario per imported cart: the cart as-is for the lots it covers, LEGO for whatever's left."""
    out = []
    by_key = {i["key"]: i for i in items}
    for c in carts:
        covered = set(c["keys"]) or set(by_key)  # a cart with no key list is assumed to cover the whole marketplace set
        rest = [i for i in items if i["key"] not in covered]
        lego_items = [i for i in rest if i["lego_price"] is not None]
        leftover = [i for i in rest if i["lego_price"] is None]
        L = _lego_cost(lego_items, opts) if lego_items else None
        total = c["total"] + (L["total"] if L else 0)
        out.append({
            "name": f"{c['source']} cart: {c['name']}", "cart_id": c["id"], "url": c.get("url"),
            "note": f"{len(c['stores'])} store{'s' if len(c['stores']) != 1 else ''} from {c['source']}'s cart builder"
                    + (f"; {len(leftover)} lot(s) not covered by anything" if leftover else ""),
            "lego": L, "market": {"parts": c["parts_total"], "shipping": c["shipping_total"], "total": c["total"], "lots": len([k for k in covered if k in by_key]), "store_lots": c.get("lots_covered"), "unpriced": 0},
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


# ---------- matching an imported cart page's lots to our parts ----------

# Rebrickable colour name -> BrickOwl's, for the common ones, in case Rebrickable's cross-reference is missing.
_BO_COLOR_FALLBACK = {
    "light bluish gray": "medium stone gray", "dark bluish gray": "dark stone gray", "light gray": "light gray",
    "trans-clear": "transparent", "pearl gold": "pearl gold", "flat silver": "flat silver", "pearl dark gray": "pearl dark gray",
    "metallic silver": "metallic silver", "chrome silver": "chrome silver", "medium nougat": "medium nougat", "nougat": "nougat",
}


def _bo_color_names(i):
    ext = i.get("external_ids") or {}
    names = set()
    for group in (((ext.get("color") or {}).get("BrickOwl") or {}).get("ext_descrs") or []):
        for n in (group if isinstance(group, list) else [group]):
            names.add(str(n).lower())
    cn = (i.get("color_name") or "").lower()
    if cn:
        names.add(cn)
        names.add(_BO_COLOR_FALLBACK.get(cn, cn.replace("trans-", "transparent ")))
    return names


def _base(part_num):
    m = re.match(r"^\d+", part_num or "")
    return m.group(0) if m else (part_num or "")


def _item_ids(i):
    """(exact ids, looser ids) a store lot's design numbers may match: the part number and the ids
    Rebrickable cross-references for it (LEGO design ids, BrickLink's number), then their numeric stems."""
    ext = (i.get("external_ids") or {}).get("part") or {}
    exact = {str(i["part_num"])}
    for src in ("LEGO", "BrickLink"):
        for x in ext.get(src) or []:
            exact.add(str(x))
    return exact, {_base(x) for x in exact} - exact


def allocate(items, stores):
    """Attach each store lot of an imported cart to the part it is for (same design id, same colour name
    as BrickOwl calls it). A part bought from two stores gets two allocations.

    Returns (alloc, unmatched): alloc is {item key: [{"store", "qty", "price", "url"}]} with prices
    scaled so each store's lots add up to the subtotal BrickOwl charges (the page rounds unit prices to
    the cent); unmatched lists the store lots no part could be found for."""
    exact, loose = {}, {}
    for i in items:
        ex, lo = _item_ids(i)
        for pid in ex:
            exact.setdefault(pid, []).append(i)
        for pid in lo:
            loose.setdefault(pid, []).append(i)
    alloc, pending = {}, []

    def give(i, st, lot, factor):
        alloc.setdefault(i["key"], []).append({"store": st["name"], "qty": lot["qty"], "price": lot["price"] * factor, "url": lot.get("url")})

    for st in stores:
        lots = st.get("lot_list") or []
        shown = sum(l["qty"] * l["price"] for l in lots)
        factor = st["subtotal"] / shown if shown and 0.8 < (st.get("subtotal") or 0) / shown < 1.25 else 1.0
        for lot in lots:
            ids = [str(x) for x in lot.get("ids") or []]
            name = lot["name"].lower()
            right_colour = lambda c: [i for i in {x["key"]: x for x in c}.values() if any(name.startswith(cn + " ") for cn in _bo_color_names(i))]
            # Same design number first; a mould variant (3794a for a lot listed as 3794) only when that finds nothing in this colour.
            tight = [i for pid in ids for i in exact.get(pid, [])]
            wide = [i for pid in ids for i in exact.get(_base(pid), []) + loose.get(pid, []) + loose.get(_base(pid), [])]
            hit = right_colour(tight) or right_colour(wide)
            cands = list({i["key"]: i for i in tight + wide}.values())
            if hit:
                for i in hit:
                    give(i, st, lot, factor)
            else:
                pending.append((st, lot, factor, cands))
    unmatched = []
    for st, lot, factor, cands in pending:
        # The design number is on the list but under a colour name we don't recognise: if exactly one
        # such part has no lot yet, this lot must be it.
        free = [i for i in cands if i["key"] not in alloc]
        if len(free) == 1:
            give(free[0], st, lot, factor)
            alloc[free[0]["key"]][-1]["by_number_only"] = True
        else:
            unmatched.append({"store": st["name"], **{k: lot[k] for k in ("name", "qty", "price")}})
    return alloc, unmatched


def alloc_prices(items, alloc):
    """Per (part_num, color_id): what one piece costs across the stores it was allocated to
    (quantity-weighted), for the part cards. -> {(part_num, color_id): {price, qty, store, url}}"""
    out = {}
    by_key = {i["key"]: i for i in items}
    for key, rows in alloc.items():
        i = by_key.get(key)
        qty = sum(r["qty"] for r in rows)
        if not i or not qty:
            continue
        stores = list(dict.fromkeys(r["store"] for r in rows))
        out[(i["part_num"], i["color_id"])] = {
            "price": round(sum(r["qty"] * r["price"] for r in rows) / qty, 4), "qty": qty,
            "store": " + ".join(stores), "url": min(rows, key=lambda r: r["price"]).get("url"),
        }
    return out


def cart_context(items, cart):
    """What an imported cart tells the estimates: per-part cost and which stores each part comes from."""
    alloc, unmatched = allocate(items, cart["stores"])
    return {"cart_id": cart["id"], "name": cart["name"], "alloc": alloc, "unmatched": unmatched,
            "shipping": {st["name"]: st.get("shipping") or 0.0 for st in cart["stores"]}}

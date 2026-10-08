"""Combined views: several lists (sets, wishlists) merged into one store checklist.

Identical part+color rows across lists become one aggregated item. Taps on an aggregated item are
credited to one underlying list using a simple rule: lists are filled in the order they appear in
the view. Progress always lives on the individual lists, so a list opened on its own afterwards
shows exactly what it still needs.
"""
from . import db


def parse_keys(keys: str):
    out = []
    for k in keys.split(","):
        k = k.strip()
        if k and k not in out:
            out.append(k)
    return out


def view_info(keys):
    lists = [s for s in (db.get_set(k) for k in keys) if s]
    if not lists:
        return None
    # Settings for the view: spares/minifigs on if any list has them on; alt counts if all lists count it.
    return {
        "set_num": "v:" + ",".join(s["set_num"] for s in lists),
        "name": " + ".join(s["name"] for s in lists),
        "kind": "view",
        "lists": lists,
        "include_spares": 1 if any(s["include_spares"] for s in lists) else 0,
        "include_minifigs": 1 if any(s["include_minifigs"] for s in lists) else 0,
        "count_alt": 1 if all(s["count_alt"] for s in lists) else 0,
        "loaded_at": max(s["loaded_at"] for s in lists),
        "img_url": next((s["img_url"] for s in lists if s.get("img_url")), None),
        "year": None,
        "num_parts": sum(s.get("num_parts") or 0 for s in lists),
    }


def _agg_key(p):
    return f"p:{p['part_num']}:{p['color_id']}:{p['is_spare']}"


def _agg_fig_key(f):
    return f"f:{f['fig_num']}"


def rev(keys):
    return "-".join(str(db.get_rev(k)) for k in keys)


def items(keys):
    """Aggregated parts and minifigs across the lists, in view order."""
    parts, figs = {}, {}
    for k in keys:
        s = db.get_set(k)
        if not s:
            continue
        ps, fs = db.set_items(k)
        for p in ps:
            ak = _agg_key(p)
            member = {"set_num": k, "list_name": s["name"], "key": p["key"], "quantity": p["quantity"],
                      "owned": p["owned"], "found_exact": p["found_exact"], "found_alt": p["found_alt"]}
            if ak not in parts:
                parts[ak] = {**p, "key": ak, "members": [member]}
            else:
                a = parts[ak]
                a["members"].append(member)
                for f in ("quantity", "owned", "found_exact", "found_alt"):
                    a[f] += p[f]
                if (p.get("updated_at") or 0) > (a.get("updated_at") or 0):
                    a["updated_at"], a["updated_by"] = p["updated_at"], p["updated_by"]
                if a.get("lego_price") is None and p.get("lego_price") is not None:
                    for f in ("lego_price", "lego_tier", "lego_available", "lego_limit", "price_checked_at"):
                        a[f] = p.get(f)
        for f in fs:
            ak = _agg_fig_key(f)
            member = {"set_num": k, "list_name": s["name"], "key": f["key"], "quantity": f["quantity"],
                      "owned": f["owned"], "found_exact": f["found_exact"], "found_alt": f["found_alt"]}
            if ak not in figs:
                figs[ak] = {**f, "key": ak, "members": [member]}
            else:
                a = figs[ak]
                a["members"].append(member)
                for fld in ("quantity", "owned", "found_exact", "found_alt"):
                    a[fld] += f[fld]
    return list(parts.values()), list(figs.values())


def progress_snapshot(keys):
    parts, figs = items(keys)
    return {it["key"]: {f: it[f] for f in ("owned", "found_exact", "found_alt", "updated_at", "updated_by")} | {"members": it["members"]}
            for it in parts + figs}


def _members_for(keys, agg_key):
    parts, figs = items(keys)
    for it in parts + figs:
        if it["key"] == agg_key:
            return it
    return None


def adjust(keys, agg_key, field, delta, who, count_alt=True):
    """Apply delta to the aggregated item by crediting underlying lists in view order."""
    it = _members_for(keys, agg_key)
    if not it:
        return None
    members = it["members"]
    remaining = int(delta)

    def capacity(m):
        if field == "owned":
            return m["quantity"] - m["owned"]
        found = m["found_exact"] + (m["found_alt"] if count_alt else 0)
        return m["quantity"] - m["owned"] - found

    while remaining > 0:
        target = next((m for m in members if capacity(m) > 0), None) or members[0]
        db.adjust_progress(target["set_num"], target["key"], field, 1, who)
        target[field] += 1
        remaining -= 1
    while remaining < 0:
        target = next((m for m in reversed(members) if m[field] > 0), None)
        if not target:
            break
        db.adjust_progress(target["set_num"], target["key"], field, -1, who)
        target[field] -= 1
        remaining += 1
    it = _members_for(keys, agg_key)
    return {f: it[f] for f in ("owned", "found_exact", "found_alt", "updated_at", "updated_by")} | {"members": it["members"]}


def set_value(keys, agg_key, field, value, who, count_alt=True):
    it = _members_for(keys, agg_key)
    if not it:
        return None
    return adjust(keys, agg_key, field, int(value) - it[field], who, count_alt)

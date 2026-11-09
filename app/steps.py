"""Build order for a set: which parts each instruction step uses.

Sources:
  * LDraw models (.ldr / .mpd) with `0 STEP` markers — e.g. the LDraw Official Model Repository, or
    fan-made models from the LDraw forums. Submodels are expanded in place, the way instructions build a
    sub-assembly before it is attached.
  * JSON produced by hand or by an agent reading the instruction PDF:
      {"source": "pdf", "steps": [{"book": 1, "page": 7, "step": 3,
                                   "parts": [{"part_num": "3001", "color_id": 4, "qty": 2},
                                             {"element_id": "300126", "qty": 1}]}]}
Every part is resolved against the set's inventory so the UI can tap the right row; what doesn't resolve
is reported back rather than silently dropped.
"""
import json
import pathlib
import re
import time
from collections import Counter, OrderedDict

import logging

from . import db, rebrickable

log = logging.getLogger("lego_buyer.steps")

_PART_LINE = re.compile(r"^\s*1\s+(\S+)\s+(?:\S+\s+){12}(.+?)\s*$")


class StepsError(Exception):
    pass


# ---------- LDraw ----------

def _ldraw_models(text):
    """Split an MPD into {name: [lines]} (an .ldr is one unnamed model)."""
    models, cur, name = OrderedDict(), [], None
    for raw in text.splitlines():
        line = raw.strip()
        m = re.match(r"^0\s+FILE\s+(.+)$", line, re.I)
        if m:
            if cur or name is not None:
                models[name or "main"] = cur
            name, cur = m.group(1).strip(), []
            continue
        if re.match(r"^0\s+NOFILE", line, re.I):
            models[name or "main"] = cur
            name, cur = None, []
            continue
        cur.append(line)
    if cur or not models:
        models[name or "main"] = cur
    return models


def _pretty(name):
    """'75954 - Magic Window.ldr' -> 'Magic Window' (keeps the author's capitalisation)."""
    n = re.sub(r"\.(dat|ldr|mpd)$", "", name.strip(), flags=re.I)
    n = re.sub(r"^\d{3,7}(-\d+)?\s+-\s+", "", n)
    return n or name


def _is_step(line):
    return bool(re.match(r"^0\s+(STEP|ROTSTEP)\b", line, re.I))


def _part_ref(line):
    m = _PART_LINE.match(line)
    if not m:
        return None
    color, fname = m.group(1), m.group(2).strip().replace("\\", "/").split("/")[-1]
    try:
        color = int(color)
    except ValueError:
        color = -1
    base = re.sub(r"\.(dat|ldr|mpd)$", "", fname, flags=re.I)
    return color, base, fname


def parse_ldraw(text):
    """Returns (steps, submodel_names). Each step: {label, parts: [(ldraw_color, ldraw_part, qty)]}."""
    all_models = _ldraw_models(text)
    # Embedded part definitions (unofficial parts shipped inside the MPD) are parts, not sub-assemblies.
    def _is_part(name, lines):
        return name.lower().endswith(".dat") or any(re.match(r"^0\s+!LDRAW_ORG\s+Unofficial_(Part|Subpart|Primitive|Shortcut)", l, re.I) for l in lines[:12])
    models = OrderedDict((n, l) for n, l in all_models.items() if not _is_part(n, l))
    if not models:
        raise StepsError("No model found in the LDraw file (only part definitions)")
    main = next(iter(models))
    # Case-insensitive lookup by file name, with or without the extension.
    lookup = {}
    for n in models:
        lookup[n.lower()] = n
        lookup[re.sub(r"\.(dat|ldr|mpd)$", "", n, flags=re.I).lower()] = n

    def expand(model_name, prefix, depth, inherited_color):
        if depth > 12:
            raise StepsError("Submodels nest too deep (loop?)")
        lines = models[model_name]
        out = []
        step_no = 0
        bucket = Counter()
        subs = []  # (name, color, count) referenced in this step, expanded before the step's own parts

        def flush():
            nonlocal step_no, bucket, subs
            if not bucket and not subs:
                return
            step_no += 1
            for sub, color, count in subs:
                sub_name = _pretty(sub)
                sub_prefix = f"{prefix}{'' if not prefix else ' › '}{sub_name}"
                for s in expand(sub, sub_prefix, depth + 1, color):
                    s["parts"] = [(c, p, q * count) for c, p, q in s["parts"]]
                    if count > 1:
                        s["label"] += f" ×{count}"
                    out.append(s)
            label = f"{prefix + ' · ' if prefix else ''}Step {step_no}"
            out.append({"label": label, "parts": [(c, p, q) for (c, p), q in bucket.items()], "top": not prefix, "step": step_no})
            bucket, subs = Counter(), []

        for line in lines:
            if _is_step(line):
                flush()
                continue
            ref = _part_ref(line)
            if not ref:
                continue
            color, base, fname = ref
            if color == 16 and inherited_color is not None:
                color = inherited_color
            sub = lookup.get(fname.lower()) or lookup.get(base.lower())
            if sub:
                # Same submodel repeated in a step: build it once ×N.
                for i, (s, c, n) in enumerate(subs):
                    if s == sub and c == color:
                        subs[i] = (s, c, n + 1)
                        break
                else:
                    subs.append((sub, color, 1))
            else:
                bucket[(color, base)] += 1
        flush()
        return out

    return expand(main, "", 0, None), list(models)


# ---------- Resolution against the inventory ----------

def _ldraw_candidates(base):
    """Possible Rebrickable part numbers for an LDraw file name, most specific first."""
    base = re.sub(r"^\d{3,7}(-\d+)?\s+-\s+", "", base.strip())  # '75948 - 20310' (embedded part) -> '20310'
    c = [base]
    if "-" in base:
        c.append(base.split("-")[0])  # '4449-f1' -> '4449'
    b = base
    # Strip pattern/print suffixes progressively: 3626cpb0001 -> 3626c -> 3626
    while True:
        m = re.match(r"^(.*?)(p[a-z]*\d+[a-z]*|pb\d+|pr\d+|[a-z])$", b)
        if not m or not m.group(1):
            break
        b = m.group(1)
        c.append(b)
    return c


def _inventory(set_num):
    parts = db.q("SELECT id, part_num, color_id, color_name, is_spare, element_id, external_ids FROM set_parts WHERE set_num=? ORDER BY is_spare", (set_num,))
    colors = {r["id"]: r for r in db.q("SELECT id, name, external_ids FROM colors")}
    by_ld, by_num, by_elem = {}, {}, {}
    for p in parts:
        ext = json.loads(p["external_ids"] or "{}")
        for ld in (ext.get("part") or {}).get("LDraw") or []:
            by_ld.setdefault(str(ld).lower(), set()).add(p["part_num"])
        by_num.setdefault(p["part_num"], []).append(p)
        if p["element_id"]:
            by_elem.setdefault(str(p["element_id"]), p)
    ld_colors = {}
    for c in colors.values():
        ext = json.loads(c["external_ids"] or "{}")
        for cid in ((ext.get("LDraw") or {}).get("ext_ids") or []):
            if cid is not None:
                ld_colors.setdefault(int(cid), c["id"])
    return parts, by_ld, by_num, by_elem, ld_colors, colors


def resolve_ldraw(set_num, steps):
    """Map LDraw (color, part) pairs to inventory (part_num, color_id). Returns (steps, report)."""
    parts, by_ld, by_num, by_elem, ld_colors, colors = _inventory(set_num)
    unmatched, guessed = Counter(), Counter()
    resolved_steps = []
    for st in steps:
        rp = []
        for ldc, ldp, qty in st["parts"]:
            part_num = None
            for cand in _ldraw_candidates(ldp.lower()):
                if cand in by_ld:
                    part_num = sorted(by_ld[cand], key=len)[0]
                    break
                if cand in by_num:
                    part_num = cand
                    break
            if not part_num:
                unmatched[f"{ldp} (LDraw color {ldc})"] += qty
                continue
            color_id = ld_colors.get(ldc)
            rows = by_num[part_num]
            if color_id is None or not any(r["color_id"] == color_id for r in rows):
                cols = {r["color_id"] for r in rows}
                if len(cols) == 1:
                    color_id = cols.pop()  # only one color of this part in the set: take it
                    guessed[f"{ldp} LDraw color {ldc} → {rows[0]['color_name']}"] += qty
                else:
                    unmatched[f"{ldp} in LDraw color {ldc} (set has {part_num} in {', '.join(sorted({r['color_name'] for r in rows}))})"] += qty
                    continue
            rp.append({"part_num": part_num, "color_id": color_id, "qty": qty})
        resolved_steps.append({"label": st["label"], "step": st.get("step"), "parts": _merge(rp)})
    report = _report(set_num, resolved_steps, unmatched, parts)
    report["color_guessed"] = dict(guessed.most_common())
    return resolved_steps, report


def resolve_json(set_num, data):
    parts, by_ld, by_num, by_elem, ld_colors, colors = _inventory(set_num)
    steps_in = data.get("steps") if isinstance(data, dict) else data
    if not isinstance(steps_in, list) or not steps_in:
        raise StepsError("JSON needs a non-empty \"steps\" list")
    unmatched, remapped = Counter(), {}
    out = []

    def via_rebrickable(element_id):
        """Instruction books and Rebrickable often use different element ids for the same part+colour:
        ask Rebrickable what the book's id is, then find that part+colour in the inventory."""
        cached = db.element_map_get(element_id)
        stale = cached is not None and cached["part_num"] is None and (time.time() - (cached["checked_at"] or 0)) > 86400
        if cached is None or stale:
            try:
                res = rebrickable.element(element_id)
            except Exception as e:  # network/API trouble: remember the attempt so we don't loop, retry tomorrow
                log.warning("element lookup %s failed: %s", element_id, e)
                res = None
            db.element_map_put(element_id, res[0] if res else None, res[1] if res else None)
            cached = {"part_num": res[0] if res else None, "color_id": res[1] if res else None}
        return cached["part_num"], cached["color_id"]

    for i, st in enumerate(steps_in, start=1):
        rp = []
        for p in st.get("parts") or []:
            qty = int(p.get("qty") or 1)
            part_num, color_id = p.get("part_num"), p.get("color_id")
            if p.get("element_id") and not part_num:
                row = by_elem.get(str(p["element_id"]))
                if row:
                    part_num, color_id = row["part_num"], row["color_id"]
                else:
                    part_num, color_id = via_rebrickable(str(p["element_id"]))
                    if part_num and part_num not in by_num:
                        # Rebrickable may name a mould variant (3068b vs 3068a): fall back to the inventory
                        # part sharing the same base number, if there is exactly one.
                        base = re.match(r"^\d+", part_num)
                        alts = [n for n in by_num if base and re.match(r"^\d+", n) and re.match(r"^\d+", n).group(0) == base.group(0)]
                        if len(alts) == 1:
                            part_num = alts[0]
                    if part_num in by_num:
                        remapped[str(p["element_id"])] = part_num
            if part_num not in by_num:
                unmatched[f"{part_num or p.get('element_id') or '?'}"] += qty
                continue
            rows = by_num[part_num]
            if color_id is None or not any(r["color_id"] == int(color_id) for r in rows):
                cols = {r["color_id"] for r in rows}
                if len(cols) == 1:
                    color_id = cols.pop()
                else:
                    unmatched[f"{part_num} color {color_id}"] += qty
                    continue
            rp.append({"part_num": part_num, "color_id": int(color_id), "qty": qty, "note": p.get("note")})
        label = st.get("label") or " · ".join(x for x in [f"Book {st['book']}" if st.get("book") else "", f"p.{st['page']}" if st.get("page") else "", f"Step {st.get('step', i)}"] if x)
        out.append({"book": st.get("book"), "page": st.get("page"), "step": st.get("step", i), "label": label, "note": st.get("note"), "parts": _merge(rp)})
    report = _report(set_num, out, unmatched, parts)
    report["remapped_elements"] = len(remapped)
    return out, report


def _merge(rp):
    c = OrderedDict()
    for p in rp:
        k = (p["part_num"], p["color_id"])
        if k in c:
            c[k]["qty"] += p["qty"]
        else:
            c[k] = dict(p)
    return list(c.values())


def _report(set_num, steps, unmatched, parts):
    """Reconcile step totals against the inventory so a bad import is visible."""
    used = Counter()
    for st in steps:
        for p in st["parts"]:
            used[(p["part_num"], p["color_id"])] += p["qty"]
    inv, names = Counter(), {}
    for r in db.q("SELECT part_num, color_id, color_name, quantity FROM set_parts WHERE set_num=? AND is_spare=0", (set_num,)):
        inv[(r["part_num"], r["color_id"])] += r["quantity"]
        names[(r["part_num"], r["color_id"])] = f"{r['part_num']} {r['color_name']}"
    over = {names.get(k, str(k)): used[k] - inv.get(k, 0) for k in used if used[k] > inv.get(k, 0)}
    missing = {names[k]: inv[k] - used.get(k, 0) for k in inv if inv[k] > used.get(k, 0)}
    return {
        "steps": len(steps),
        "pieces_in_steps": sum(used.values()),
        "pieces_in_set": sum(inv.values()),
        "lots_covered": sum(1 for k in inv if used.get(k)),
        "lots_in_set": len(inv),
        "unmatched": dict(unmatched.most_common()),
        "over": over,          # more used in steps than the set has (wrong part/color, or duplicated submodel)
        "not_in_steps": missing,  # inventory parts the steps never use
    }


def import_text(set_num, text, filename=""):
    """Detect the format and import. Returns the report."""
    text = text.lstrip("﻿")
    stripped = text.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            data = json.loads(stripped)
        except ValueError as e:
            raise StepsError(f"Not valid JSON: {e}")
        steps, report = resolve_json(set_num, data)
        source = (data.get("source") if isinstance(data, dict) else None) or "json"
        if filename:
            source = f"{filename}: {source}"
    elif re.search(r"^\s*(0|1)\s", text, re.M):
        raw, models = parse_ldraw(text)
        if not raw:
            raise StepsError("No parts found in the LDraw file")
        steps, report = resolve_ldraw(set_num, raw)
        report["submodels"] = len(models)
        report["has_step_markers"] = bool(re.search(r"^\s*0\s+(STEP|ROTSTEP)\b", text, re.M | re.I))
        source = f"ldraw:{filename}" if filename else "ldraw"
    else:
        raise StepsError("Couldn't tell what this file is (expected LDraw .ldr/.mpd or JSON)")
    if not any(st["parts"] for st in steps):
        raise StepsError("Nothing matched the set's inventory. " + ("Unmatched: " + ", ".join(list(report["unmatched"])[:8]) if report["unmatched"] else ""))
    notes = json.dumps({k: report[k] for k in ("unmatched", "over", "not_in_steps") if report.get(k)})
    db.replace_steps(set_num, steps, source, notes)
    return report


# ---------- Bundled build orders ----------

BUNDLED_DIR = pathlib.Path(__file__).parent / "buildorders"


def bundled_path(set_num):
    for ext in ("mpd", "ldr", "json"):
        p = BUNDLED_DIR / f"{set_num}.{ext}"
        if p.exists():
            return p
    return None


def needs_reimport(meta):
    """A bundled build order whose unmatched element ids have never been looked up on Rebrickable."""
    try:
        notes = json.loads(meta.get("notes") or "{}")
    except ValueError:
        return False
    ids = [k for k in (notes.get("unmatched") or {}) if re.fullmatch(r"\d{6,7}", str(k))]
    return any(db.element_map_get(k) is None for k in ids)


def import_bundled(set_num):
    """Import the shipped build order for a set, if there is one. Returns the report or None."""
    p = bundled_path(set_num)
    if not p:
        return None
    return import_text(set_num, p.read_text(encoding="utf-8", errors="replace"), f"bundled {p.name}")

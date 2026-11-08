"""Pull build-step structure out of a LEGO instruction PDF.

For every page: words (with font size) and embedded images (with bbox + XObject id). A callout is an
"Nx" label with an image directly above it; a step is a big numeral. Each callout is assigned to the
step whose numeral sits just below/left of it. The parts-inventory pages at the back pair a small
image with "Nx" + element id. Output: JSON with steps, callouts (keyed by image object id), inventory.
"""
import json, re, sys, collections
import pdfplumber

QTY = re.compile(r"^(\d{1,3})x$", re.I)
ELEM = re.compile(r"^\d{6,7}$")


def boxes_of(p):
    """Filled callout boxes (rounded rects) big enough to hold part pictures."""
    out = []
    ox, oy = p.bbox[0], -p.bbox[1]
    for sh in list(p.rects) + list(p.curves):
        w, h = sh["x1"] - sh["x0"], sh["bottom"] - sh["top"]
        if sh.get("fill") and 40 <= w < p.width * 0.95 and 22 <= h < p.height * 0.9:
            out.append({"x0": sh["x0"] - ox, "x1": sh["x1"] - ox, "top": sh["top"] + oy, "bottom": sh["bottom"] + oy, "area": w * h})
    return out


def box_for(lab, boxes):
    cx, cy = (lab["x0"] + lab["x1"]) / 2, (lab["top"] + lab["bottom"]) / 2
    inside = [b for b in boxes if b["x0"] <= cx <= b["x1"] and b["top"] <= cy <= b["bottom"]]
    return min(inside, key=lambda b: b["area"]) if inside else None


def images_inside(region, images, min_inside=0.5, must=None):
    x0, t, x1, b = region
    if must is not None and must["w"] >= 4 and must["h"] >= 4:
        x0, x1 = min(x0, must["x0"]), max(x1, must["x1"])
    out = []
    for im in images:
        area = max(0.01, im["w"] * im["h"])
        ix = max(0, min(x1, im["x1"]) - max(x0, im["x0"])); iy = max(0, min(b, im["bottom"]) - max(t, im["top"]))
        if (ix * iy >= min_inside * area and area >= 2) or (must is not None and im is must and must["w"] >= 4 and must["h"] >= 4):
            out.append(im)
    return out


def shrink_to_images(region, images, min_inside=0.5, must=None):
    """Tighten a region to the union of the image objects lying mostly inside it (works for whole
    pictures, tiled strips and composites alike). Leaves the region alone if no image is inside."""
    ins = images_inside(region, images, min_inside, must)
    if not ins:
        return region
    x0, t, x1, b = region
    if must is not None and must in ins:
        x0, x1 = min(x0, must["x0"]), max(x1, must["x1"])
    return [max(x0, min(i["x0"] for i in ins)) - 1, max(t, min(i["top"] for i in ins)) - 1,
            min(x1, max(i["x1"] for i in ins)) + 1, min(b, max(i["bottom"] for i in ins)) + 1]


def crop_regions(labels, images, boxes):
    """For each label: the page region holding its part picture, plus an identity key when that picture
    is a single clean image object. Within a callout box every image object is handed to exactly one
    label (the one at its bottom-left), except an image spanning several labels of one row, which is
    sliced between them. A label that ends up with no image gets the geometric span instead."""
    info = {}
    by_box = collections.defaultdict(list)
    for l in labels:
        b = box_for(l, boxes)
        by_box[id(b) if b else None].append(l)
    boxes_by_id = {id(b): b for b in boxes}
    for bid, labs in by_box.items():
        b = boxes_by_id.get(bid)
        if b is None:
            for l in labs:
                im = image_above(l, images)
                good = im is not None and im["h"] >= 4 and im["w"] >= 4
                info[id(l)] = {"crop": [im["x0"], im["top"], im["x1"], im["bottom"]] if good else None,
                               "objid": im["objid"] if im else None, "key": str(im["objid"]) if good else None}
            continue
        ins = [im for im in images if im["w"] * im["h"] >= 2 and im["x0"] >= b["x0"] - 2 and im["x1"] <= b["x1"] + 2
               and im["top"] >= b["top"] - 2 and im["bottom"] <= b["bottom"] + 2]
        pieces = collections.defaultdict(list)   # label id -> [(bbox, objid, whole)]
        for im in ins:
            below = [l for l in labs if l["top"] >= im["bottom"] - 3 and im["x0"] - 8 <= l["x0"] <= im["x1"] - 6]
            if below:
                rows = collections.defaultdict(list)
                for l in below:
                    rows[round(l["top"] / 8)].append(l)
                row = sorted(min(rows.values(), key=lambda r: min(l["top"] for l in r) - im["bottom"]), key=lambda l: l["x0"])
            else:
                row = []
            if len(row) >= 2:
                for i, l in enumerate(row):
                    x0 = im["x0"] if i == 0 else max(im["x0"], l["x0"] - 8)
                    x1 = im["x1"] if i == len(row) - 1 else row[i + 1]["x0"] - 4
                    pieces[id(l)].append(([x0, im["top"], x1, im["bottom"]], im["objid"], False))
            else:
                if row:
                    l = row[0]
                else:
                    above_of = [l for l in labs if l["top"] >= im["bottom"] - 3 and l["x0"] >= im["x0"] - 40 and l["x0"] <= im["x1"] + 10]
                    if not above_of:
                        continue  # not a part picture (box decoration, arrows, icons)
                    l = min(above_of, key=lambda l: abs(l["x0"] - im["x0"]) + 0.3 * abs(l["top"] - im["bottom"]))
                pieces[id(l)].append(([im["x0"], im["top"], im["x1"], im["bottom"]], im["objid"], True))
        for l in labs:
            ps = pieces.get(id(l), [])
            im = image_above(l, images)
            if ps:
                crop = [min(p[0][0] for p in ps) - 1, min(p[0][1] for p in ps) - 1, max(p[0][2] for p in ps) + 1, max(p[0][3] for p in ps) + 1]
                clean = len(ps) == 1 and ps[0][2] and (ps[0][0][3] - ps[0][0][1]) >= 4 and (ps[0][0][2] - ps[0][0][0]) >= 4
                info[id(l)] = {"crop": crop, "objid": ps[0][1], "key": str(ps[0][1]) if clean else None}
            else:
                row = sorted([o for o in labs if abs(o["top"] - l["top"]) < 8], key=lambda o: o["x0"])
                i = row.index(l)
                x0 = max(b["x0"] + 2, l["x0"] - 8)
                x1 = b["x1"] - 2 if i == len(row) - 1 else row[i + 1]["x0"] - 4
                above = [o for o in labs if o["bottom"] < l["top"] - 4 and o["x0"] < x1 and o["x1"] > x0]
                top = max([b["top"] + 2] + [o["bottom"] + 2 for o in above])
                info[id(l)] = {"crop": [x0, top, x1, l["top"] - 1], "objid": im["objid"] if im else None, "key": None}
    return info


def inventory_regions(entries, page_w, page_h):
    """entries: [{x0, top, bottom (of qty+id text)}] -> crop boxes in a column grid above each label."""
    xs = sorted({round(e["x0"]) for e in entries})
    cols = []
    for x in xs:
        if not cols or x - cols[-1][-1] > 25:
            cols.append([x])
        else:
            cols[-1].append(x)
    col_x0 = [min(c) for c in cols]
    out = []
    for e in entries:
        ci = min(range(len(col_x0)), key=lambda i: abs(col_x0[i] - e["x0"]))
        left = col_x0[ci] - 6
        right = (col_x0[ci + 1] - 8) if ci + 1 < len(col_x0) else min(page_w - 4, col_x0[ci] + 140)
        same_col = [o for o in entries if abs(o["x0"] - col_x0[ci]) < 25 and o["bottom"] < e["top"] - 2]
        top = max([8] + [o["bottom"] + 2 for o in same_col])
        out.append([left, top, right, e["top"] - 1])
    return out


def page_data(p):
    try:
        p = p.dedupe_chars(tolerance=1)  # some books draw every glyph twice (outline + fill)
    except Exception:
        pass
    words = p.extract_words(extra_attrs=["size", "fontname"], keep_blank_chars=False)
    # MediaBox origin: pdfplumber coordinates are absolute user space; the rendered page starts at bbox[0]
    # horizontally and its "top" is off by bbox[1] when the MediaBox doesn't start at y=0.
    ox, oy = p.bbox[0], -p.bbox[1]
    for w in words:
        w["x0"] -= ox; w["x1"] -= ox; w["top"] += oy; w["bottom"] += oy
    images = []
    for im in p.images:
        objid = getattr(im.get("stream"), "objid", None)
        images.append({"x0": im["x0"] - ox, "x1": im["x1"] - ox, "top": im["top"] + oy, "bottom": im["bottom"] + oy, "objid": objid,
                       "w": im["x1"] - im["x0"], "h": im["bottom"] - im["top"], "src": im.get("srcsize")})
    return words, images


def image_above(label, images, max_gap=14):
    """The image whose bottom edge sits just above the label and spans the label's x."""
    best = None
    lx = (label["x0"] + label["x1"]) / 2
    for im in images:
        gap = label["top"] - im["bottom"]
        if -3 <= gap <= max_gap and im["x0"] - 6 <= label["x0"] and lx <= im["x1"] + 6:
            if best is None or gap < best[0]:
                best = (gap, im)
    return best[1] if best else None


def extract(path, main_sizes=(26,), sub_sizes=(16, 22), bag_sizes=(30, 36), label_size=(7, 10)):
    """Font sizes (pt, rounded) identify the numerals: main step numbers, sub-assembly step numbers and
    bag markers. The defaults fit LEGO books from ~2017 on; 2014 books use main 38 / sub 30,18 / bag 36."""
    pdf = pdfplumber.open(path)
    out = {"file": path, "pages": len(pdf.pages), "steps": [], "inventory": [], "odd_labels": [], "bags": [],
           "params": {"main_sizes": list(main_sizes), "sub_sizes": list(sub_sizes), "bag_sizes": list(bag_sizes)}}
    for pno, p in enumerate(pdf.pages, start=1):
        words, images = page_data(p)
        nums = [w for w in words if re.fullmatch(r"\d{1,3}", w["text"])]
        step_words = [w for w in nums if round(w["size"]) in main_sizes or round(w["size"]) in sub_sizes]
        for w in nums:
            if round(w["size"]) in bag_sizes:
                out["bags"].append({"page": pno, "num": int(w["text"]), "size": round(w["size"]), "x": w["x0"], "top": w["top"]})
        labels = [w for w in words if QTY.match(w["text"])]
        elems = [w for w in words if ELEM.match(w["text"])]
        # Inventory page: many element ids
        if len(elems) >= 8:
            entries = []
            for e in elems:
                lab = min((l for l in labels if abs(l["x0"] - e["x0"]) < 8 and 0 < e["top"] - l["top"] < 16), key=lambda l: e["top"] - l["top"], default=None)
                if not lab:
                    continue
                entries.append({"x0": min(lab["x0"], e["x0"]), "x1": max(lab["x1"], e["x1"]), "top": lab["top"], "bottom": e["bottom"],
                                "element": e["text"], "qty": int(QTY.match(lab["text"]).group(1))})
            # LEGO inventory pages put the picture above its "Nx" + element id.
            pics = [im for im in images if 2 <= im["w"] * im["h"] and im["w"] <= 130 and im["h"] <= 130]
            above = True
            # Hand every picture (or picture strip) to the entry it belongs to.
            pieces = collections.defaultdict(list)
            for im in pics:
                if above:
                    cands = [e for e in entries if -3 <= e["top"] - im["bottom"] <= 70 and -12 <= im["x0"] - e["x0"] <= 45]
                    key = lambda e: abs(e["x0"] - im["x0"]) * 1.5 + (e["top"] - im["bottom"])
                else:
                    cands = [e for e in entries if -3 <= im["top"] - e["bottom"] <= 70 and -12 <= im["x0"] - e["x0"] <= 45]
                    key = lambda e: abs(e["x0"] - im["x0"]) * 1.5 + (im["top"] - e["bottom"])
                if not cands:
                    continue
                pieces[id(min(cands, key=key))].append(im)
            for ent in entries:
                ps = pieces.get(id(ent), [])
                box = [min(i["x0"] for i in ps) - 1, min(i["top"] for i in ps) - 1, max(i["x1"] for i in ps) + 1, max(i["bottom"] for i in ps) + 1] if ps else None
                out["inventory"].append({"page": pno, "element": ent["element"], "qty": ent["qty"], "bbox": box})
            continue
        # Step page
        steps = [{"page": pno, "num": int(w["text"]), "x": w["x0"], "top": w["top"], "size": w["size"], "sub": round(w["size"]) in sub_sizes, "callouts": []} for w in step_words]
        boxes = boxes_of(p)
        for lab in labels:
            lab["page"] = pno
        regions = crop_regions([l for l in labels if label_size[0] <= l["size"] <= label_size[1]], images, boxes)
        for lab in labels:
            q = int(QTY.match(lab["text"]).group(1))
            r = regions.get(id(lab), {"crop": None, "objid": None, "key": None})
            rec = {"qty": q, "x": lab["x0"], "top": lab["top"], "size": round(lab["size"], 1),
                   "objid": r["objid"], "key": r["key"], "bbox": r["crop"]}
            if not (label_size[0] <= lab["size"] <= label_size[1]):
                rec["page"] = pno
                out["odd_labels"].append(rec)
                continue
            # step numeral just below (top greater) and not to the right of the label
            # The owning numeral sits just below its callout box, in the same column. Numerals far to
            # the left (the other column) are penalised so a vertically-closer one there doesn't win.
            cands = [s for s in steps if s["top"] > lab["top"] - 2 and s["x"] <= lab["x0"] + 12]
            if cands:
                s = min(cands, key=lambda s: (s["top"] - lab["top"]) + 2 * max(0, (lab["x0"] - s["x"]) - 150))
                s["callouts"].append(rec)
            else:
                rec["page"] = pno
                out["odd_labels"].append(rec)
        for s in steps:
            out["steps"].append(s)
    return out


if __name__ == "__main__":
    path = sys.argv[1]
    d = extract(path)
    json.dump(d, open(sys.argv[2], "w"), indent=0)
    steps = d["steps"]
    co = [c for s in steps for c in s["callouts"]]
    objs = collections.Counter(c["objid"] for c in co)
    print(f"{path}: pages={d['pages']} steps={len(steps)} callouts={len(co)} pieces={sum(c['qty'] for c in co)} distinct images={len(objs)} "
          f"no-crop={sum(1 for c in co if c['bbox'] is None)} odd labels={len(d['odd_labels'])} inventory entries={len(d['inventory'])} "
          f"inv pieces={sum(e['qty'] or 0 for e in d['inventory'])} inv no-img={sum(1 for e in d['inventory'] if e['objid'] is None)}")
    sizes = collections.Counter(round(s["size"]) for s in steps)
    print("  step numeral sizes:", dict(sizes), " steps without callouts:", sum(1 for s in steps if not s["callouts"]))
    print("  odd labels:", [(o.get("page"), o["qty"], o["size"]) for o in d["odd_labels"]][:20])

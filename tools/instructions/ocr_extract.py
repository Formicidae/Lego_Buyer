"""Step structure for instruction books with no text layer (flattened scans, pre-2010).

Renders each page, finds the callout boxes by their fill colour, OCRs the "Nx" labels inside them and
the big step numeral below each box, and OCRs the element ids on the inventory pages (snapped to a known
inventory list when given). Writes the same extract.json shape as extract.py so match/assign/build_steps
work unchanged. Coordinates are PDF points.

Usage: ocr_extract.py book.pdf out.json [--inventory elements.txt] [--box-color R,G,B] [--dpi 200]
"""
import argparse, csv, io, json, os, re, subprocess, sys, collections
import numpy as np
from PIL import Image
from scipy import ndimage

QTY = re.compile(r"^(\d{1,2})x$", re.I)


def render(pdf, pno, dpi, outdir):
    os.makedirs(outdir, exist_ok=True)
    fn = os.path.join(outdir, f"ocr_p{pno:03d}_{dpi}.png")
    if not os.path.exists(fn):
        subprocess.run(["pdftoppm", "-f", str(pno), "-l", str(pno), "-r", str(dpi), "-png", "-singlefile", pdf, fn[:-4]], check=True)
    return fn


def ocr(img, psm=11, whitelist="0123456789x", scale=1):
    """Tesseract TSV -> [{text, left, top, width, height, conf}] in the coordinates of `img`."""
    if scale != 1:
        img = img.resize((img.width * scale, img.height * scale), Image.LANCZOS)
    buf = io.BytesIO(); img.save(buf, format="PNG")
    r = subprocess.run(["tesseract", "stdin", "-", "--psm", str(psm), "-c", f"tessedit_char_whitelist={whitelist}", "tsv"],
                       input=buf.getvalue(), capture_output=True)
    out = []
    for row in csv.DictReader(io.StringIO(r.stdout.decode("utf-8", "replace")), delimiter="\t"):
        t = (row.get("text") or "").strip()
        if not t:
            continue
        try:
            conf = float(row["conf"])
        except ValueError:
            conf = -1
        out.append({"text": t, "left": int(row["left"]) / scale, "top": int(row["top"]) / scale,
                    "width": int(row["width"]) / scale, "height": int(row["height"]) / scale, "conf": conf})
    return out


def color_boxes(arr, rgb, tol=28, min_w=120, min_h=60):
    """Connected regions of (roughly) one colour: the callout boxes."""
    d = np.abs(arr.astype(int) - np.array(rgb)).sum(axis=2)
    mask = d < tol
    mask = ndimage.binary_opening(mask, iterations=2)
    lab, n = ndimage.label(mask)
    boxes = []
    for i, sl in enumerate(ndimage.find_objects(lab), start=1):
        h, w = sl[0].stop - sl[0].start, sl[1].stop - sl[1].start
        if w >= min_w and h >= min_h:
            fill = (lab[sl] == i).mean()
            if fill > 0.5:
                boxes.append({"x0": sl[1].start, "top": sl[0].start, "x1": sl[1].stop, "bottom": sl[0].stop})
    return boxes


def dark_blobs(arr, region, min_h, max_h):
    """Bounding boxes of dark connected components (text) inside region, merged when adjacent (digits of a number)."""
    x0, t, x1, b = region
    sub = arr[t:b, x0:x1]
    gray = sub.mean(axis=2)
    mask = gray < 90
    lab, n = ndimage.label(mask)
    comps = []
    for i, sl in enumerate(ndimage.find_objects(lab), start=1):
        h, w = sl[0].stop - sl[0].start, sl[1].stop - sl[1].start
        if min_h <= h <= max_h and w <= max_h * 1.2:
            comps.append([sl[1].start + x0, sl[0].start + t, sl[1].stop + x0, sl[0].stop + t])
    # merge horizontally adjacent components of similar height into one numeral
    comps.sort()
    merged = []
    for c in comps:
        if merged and c[0] - merged[-1][2] < max_h * 0.6 and abs(c[1] - merged[-1][1]) < max_h * 0.4:
            m = merged[-1]; m[2] = max(m[2], c[2]); m[1] = min(m[1], c[1]); m[3] = max(m[3], c[3])
        else:
            merged.append(c)
    return merged


def find_labels(crop, dpi, lo_frac=0.07, digit_only=False):
    """'Nx' labels inside a callout box: small dark text blobs, each OCR'd on its own, upscaled.
    digit_only: also accept a bare number (inventory pages, where the small 'x' often fragments)."""
    arr = np.asarray(crop); gray = arr.mean(axis=2)
    mask = gray < 110
    lab, n = ndimage.label(mask)
    lo, hi = int(lo_frac * dpi), int(0.19 * dpi)  # label text ~0.1in tall
    comps = []
    for i, sl in enumerate(ndimage.find_objects(lab), start=1):
        h, w = sl[0].stop - sl[0].start, sl[1].stop - sl[1].start
        if lo <= h <= hi and w <= hi * 1.5:
            comps.append([sl[1].start, sl[0].start, sl[1].stop, sl[0].stop])
    comps.sort()
    merged = []
    for c in comps:
        # glue a glyph onto a word to its left on the same line (digits + 'x')
        m = next((m for m in merged if -2 <= c[0] - m[2] < hi * 0.6 and abs(c[1] - m[1]) < hi * 0.5), None)
        if m:
            m[2] = max(m[2], c[2]); m[1] = min(m[1], c[1]); m[3] = max(m[3], c[3])
        else:
            merged.append(c)
    out = []
    for x0, t, x1, b in merged:
        if (x1 - x0) > 4 * (b - t) or ((x1 - x0) < (b - t) * 0.8 and not digit_only):
            continue  # not a short word
        pad = 6
        sub = crop.crop((max(0, x0 - pad), max(0, t - pad), min(crop.width, x1 + pad), min(crop.height, b + pad)))
        best = None
        for psm in (7, 8, 13):
            words = ocr(sub, psm=psm, whitelist="0123456789x", scale=4)
            if not words:
                continue
            text = "".join(w["text"] for w in words).lower()
            conf = min(w["conf"] for w in words)
            if text == "x" and (x1 - x0) > 1.0 * (b - t):
                text, conf = "1x", min(conf, 45)  # thin '1' dropped by the OCR
            if digit_only and re.fullmatch(r"\d{1,2}", text):
                text = text + "x"
            if QTY.match(text) and (best is None or conf > best[1]):
                best = (text, conf)
        if best and best[1] > 25:
            out.append({"text": best[0], "left": x0, "top": t, "width": x1 - x0, "height": b - t, "conf": best[1]})
    return sorted(out, key=lambda w: (round(w["top"] / 40), w["left"]))


def find_ids(img, dpi):
    """Element-id words (6-7 digits) found as dark text blobs, each OCR'd on its own. Complements the
    whole-page OCR pass, which misses some on low-resolution scans."""
    arr = np.asarray(img); gray = arr.mean(axis=2)
    mask = gray < 120
    lab, n = ndimage.label(mask)
    lo, hi = int(0.045 * dpi), int(0.12 * dpi)
    comps = []
    for i, sl in enumerate(ndimage.find_objects(lab), start=1):
        h, w = sl[0].stop - sl[0].start, sl[1].stop - sl[1].start
        if lo <= h <= hi and w <= hi * 1.2:
            comps.append([sl[1].start, sl[0].start, sl[1].stop, sl[0].stop])
    comps.sort()
    words = []
    for c in comps:
        m = next((m for m in words if -2 <= c[0] - m[2] < hi * 0.5 and abs(c[1] - m[1]) < hi * 0.4), None)
        if m:
            m[2] = max(m[2], c[2]); m[1] = min(m[1], c[1]); m[3] = max(m[3], c[3]); m[4] += 1
        else:
            words.append(c + [1])
    out = []
    for x0, t, x1, b, nglyph in words:
        w, h = x1 - x0, b - t
        if nglyph < 5 or not (3.0 * h <= w <= 8.0 * h):
            continue
        pad = 6
        sub = img.crop((max(0, x0 - pad), max(0, t - pad), min(img.width, x1 + pad), min(img.height, b + pad)))
        best = None
        for psm in (7, 8, 13):
            ws = ocr(sub, psm=psm, whitelist="0123456789", scale=3)
            text = "".join(w["text"] for w in ws)
            if re.fullmatch(r"\d{5,8}", text):
                conf = min(w["conf"] for w in ws)
                if best is None or conf > best[1]:
                    best = (text, conf)
        if best:
            out.append({"text": best[0], "left": x0, "top": t, "width": w, "height": h, "conf": max(31, best[1])})
    return out


def read_numeral(img, box, pad=8):
    x0, t, x1, b = box
    crop = img.crop((max(0, x0 - pad), max(0, t - pad), x1 + pad, b + pad))
    # Tesseract likes ~30px high text: scale accordingly
    sc = max(1, int(round(40 / max(1, (b - t)) * 3)))
    sc = min(sc, 4)
    best = None
    for psm in (7, 8, 13):
        for w in ocr(crop, psm=psm, whitelist="0123456789", scale=sc):
            if re.fullmatch(r"\d{1,3}", w["text"]) and (best is None or w["conf"] > best[1]):
                best = (int(w["text"]), w["conf"])
    return best


def snap_element(text, known):
    """Fix an OCR'd element id against a known list: exact, else one substitution/deletion/insertion away."""
    if text in known:
        return text, "exact"
    cands = []
    for k in known:
        if abs(len(k) - len(text)) > 1:
            continue
        if len(k) == len(text):
            if sum(a != b for a, b in zip(k, text)) == 1:
                cands.append(k)
        else:
            s, l = (text, k) if len(text) < len(k) else (k, text)
            if any(l[:i] + l[i + 1:] == s for i in range(len(l))):
                cands.append(k)
    if len(cands) == 1:
        return cands[0], "snapped"
    if not cands and len(text) in (6, 7):
        far = [k for k in known if len(k) == len(text) and sum(a != b for a, b in zip(k, text)) == 2]
        if len(far) == 1:
            return far[0], "snapped2"
    return None, "unknown"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf"); ap.add_argument("out")
    ap.add_argument("--dpi", type=int, default=200)
    ap.add_argument("--box-color", default="175,236,255")
    ap.add_argument("--inventory", default=None, help="text file with one known element id per line (e.g. from Rebrickable)")
    ap.add_argument("--pages", default=None, help="page range to treat as steps, e.g. 5-57 (default: all)")
    ap.add_argument("--inv-pages", default=None, help="inventory pages, e.g. 58-59 (default: pages where many 6-7 digit numbers are read)")
    a = ap.parse_args()
    rgb = tuple(int(v) for v in a.box_color.split(","))
    known, known_qty = set(), {}
    if a.inventory:
        for l in open(a.inventory):
            l = l.strip()
            if not l:
                continue
            if "|" in l:  # qty|part|element|colour (brickmissing / Rebrickable)
                q, part, el, *_ = l.split("|")
                if re.fullmatch(r"\d{5,8}", el):
                    known.add(el); known_qty[el] = known_qty.get(el, 0) + int(q)
            else:
                known.add(l)
    npages = int(subprocess.run(["pdfinfo", a.pdf], capture_output=True, text=True).stdout.split("Pages:")[1].split()[0])
    rng = lambda s: set(range(int(s.split("-")[0]), int(s.split("-")[-1]) + 1)) if s else None
    step_pages, inv_pages = rng(a.pages), rng(a.inv_pages)
    S = 72.0 / a.dpi  # px -> pt
    out = {"file": a.pdf, "pages": npages, "steps": [], "inventory": [], "odd_labels": [], "bags": [], "ocr": True}
    outdir = os.path.join(os.path.dirname(os.path.abspath(a.out)), "pages")
    for pno in range(1, npages + 1):
        fn = render(a.pdf, pno, a.dpi, outdir)
        img = Image.open(fn).convert("RGB"); arr = np.asarray(img)
        H, W = arr.shape[:2]
        is_inv = inv_pages is not None and pno in inv_pages
        if inv_pages is None and (step_pages is None or pno not in step_pages):
            words = ocr(img, psm=11, whitelist="0123456789x", scale=1)
            is_inv = sum(1 for w in words if re.fullmatch(r"\d{6,7}", w["text"])) >= 8
        if is_inv:
            # Inventory pages: re-render at 300 dpi for the small element ids.
            fn3 = render(a.pdf, pno, 300, outdir)
            img3 = Image.open(fn3).convert("RGB"); S3 = 72.0 / 300
            words = ocr(img3, psm=11, whitelist="0123456789x", scale=1)
            words += ocr(img3, psm=12, whitelist="0123456789x", scale=1)
            words += ocr(img3, psm=11, whitelist="0123456789x", scale=2)  # upscaled copy catches the rest
            # the 200 dpi render upscaled 1.5x reads differently again (smoother glyph edges); union it
            k = 300 / a.dpi
            words += [{**w, "left": w["left"] * k, "top": w["top"] * k, "width": w["width"] * k, "height": w["height"] * k}
                      for w in ocr(img, psm=11, whitelist="0123456789x", scale=2)]
            ids = []
            for w in sorted(words, key=lambda w: -w["conf"]):
                if re.fullmatch(r"\d{5,8}", w["text"]) and w["conf"] > 30:
                    if not any(abs(w["left"] - o["left"]) < 40 and abs(w["top"] - o["top"]) < 25 for o in ids):
                        ids.append(w)
            # second pass: blob-based, catches ids the page OCR skipped
            for w in find_ids(img3, 300):
                if not any(abs(w["left"] - o["left"]) < 40 and abs(w["top"] - o["top"]) < 25 for o in ids):
                    ids.append(w)
            labs, seen_l = [], set()
            for w in sorted(words, key=lambda w: -w["conf"]):
                if QTY.match(w["text"]) and w["conf"] > 30:
                    k = (round(w["left"] / 30), round(w["top"] / 30))
                    if k not in seen_l:
                        seen_l.add(k); labs.append(w)
            # partial reads ('10633' for '4210633') duplicate a full id elsewhere on the page: drop them
            full = [w["text"] for w in ids if len(w["text"]) >= 6]
            ids = [w for w in ids if len(w["text"]) >= 6 or not any(f.endswith(w["text"]) for f in full)]
            # columns from the id positions
            xs = sorted(w["left"] for w in ids); cols = []
            for x in xs:
                if not cols or x - cols[-1][-1] > 60:
                    cols.append([x])
                else:
                    cols[-1].append(x)
            col_x = [min(c) for c in cols]
            for e in ids:
                text = e["text"]
                if known:
                    fixed, how = snap_element(text, known)
                    text = fixed or text
                else:
                    how = "ocr"
                if how == "unknown" and known:
                    # Not in the published list: the book may use an older element id for the same part
                    # (the app resolves those on Rebrickable), so keep it, flagged.
                    print(f"  inventory: id {text!r} not in the published list (kept)")
                lab = min((l for l in labs if abs(l["left"] - e["left"]) < 90 and 0 < e["top"] - l["top"] < 90), key=lambda l: e["top"] - l["top"], default=None)
                qty = int(QTY.match(lab["text"]).group(1)) if lab else None
                if qty is None:
                    # the "Nx" sits just above the id: read that strip on its own
                    strip = img3.crop((max(0, int(e["left"]) - 40), max(0, int(e["top"]) - 62), int(e["left"]) + 160, int(e["top"]) - 2))
                    found = find_labels(strip, 300, lo_frac=0.045, digit_only=True)
                    if found:
                        f = found[0]
                        qty = int(QTY.match(f["text"]).group(1))
                        lab = {"left": e["left"] - 40 + f["left"], "top": e["top"] - 62 + f["top"], "height": f["height"]}
                ref = lab or e
                ci = min(range(len(col_x)), key=lambda i: abs(col_x[i] - e["left"]))
                right = (col_x[ci + 1] - 12) if ci + 1 < len(col_x) else min(img3.width - 4, col_x[ci] + 420)
                above = [w for w in ids if abs(w["left"] - col_x[ci]) < 90 and w["top"] < ref["top"] - 10]
                top_px = max([ref["top"] - 260] + [w["top"] + w["height"] + 8 for w in above])
                box_px = [col_x[ci] - 14, top_px, right, ref["top"] - 4]
                if text in known_qty:
                    qty = known_qty[text]  # the published inventory beats a 6-pixel OCR'd digit
                if qty is None:
                    qty, how = 1, how + "+noqty"
                out["inventory"].append({"page": pno, "element": text, "qty": qty, "how": how, "ocr": e["text"],
                                         "bbox": [v * S3 for v in box_px]})
            print(f"page {pno}: inventory {len(ids)} ids, {len(labs)} labels, {len(col_x)} columns", flush=True)
            continue
        if step_pages is not None and pno not in step_pages:
            continue
        boxes = color_boxes(arr, rgb)
        if not boxes:
            continue
        steps_here = []
        for b in boxes:
            crop = img.crop((b["x0"], b["top"], b["x1"], b["bottom"]))
            labs = find_labels(crop, a.dpi)
            # the step numeral sits below the box's left edge
            region = [max(0, b["x0"] - 20), b["bottom"] + 5, min(W, b["x0"] + 600), min(H, b["bottom"] + 420)]
            cands = dark_blobs(arr, region, min_h=int(0.25 * a.dpi), max_h=int(0.75 * a.dpi))
            num = None
            for c in sorted(cands, key=lambda c: (-(c[3] - c[1]), c[1], c[0])):  # tallest first: sub-step numerals are smaller
                r = read_numeral(img, c)
                if r and r[1] > 30:
                    num = (r[0], c); break
            step = {"page": pno, "num": num[0] if num else None, "x": (num[1][0] if num else b["x0"]) * S, "top": (num[1][1] if num else b["bottom"]) * S,
                    "size": 26, "sub": False, "callouts": [], "box": [b["x0"] * S, b["top"] * S, b["x1"] * S, b["bottom"] * S]}
            for i, l in enumerate(labs):
                row = [o for o in labs if abs(o["top"] - l["top"]) < 40]
                row.sort(key=lambda o: o["left"]); j = row.index(l)
                x0 = max(0, l["left"] - 16) if j == 0 else max(0, l["left"] - 16)
                x1 = (row[j + 1]["left"] - 8) if j + 1 < len(row) else crop.width - 4
                above = [o for o in labs if o["top"] + o["height"] < l["top"] - 10 and o["left"] < x1 and o["left"] + o["width"] > x0]
                top = max([4] + [o["top"] + o["height"] + 4 for o in above])
                bb = [b["x0"] + x0, b["top"] + top, b["x0"] + x1, b["top"] + l["top"] - 2]
                step["callouts"].append({"qty": int(QTY.match(l["text"]).group(1)), "x": (b["x0"] + l["left"]) * S, "top": (b["top"] + l["top"]) * S,
                                         "size": 8, "objid": None, "key": None, "bbox": [v * S for v in bb], "conf": l["conf"]})
            steps_here.append(step)
        out["steps"].extend(steps_here)
        print(f"page {pno}: {len(boxes)} box(es), steps {[s['num'] for s in steps_here]}, labels {[len(s['callouts']) for s in steps_here]}", flush=True)
    json.dump(out, open(a.out, "w"), indent=0)
    co = [c for s in out["steps"] for c in s["callouts"]]
    print(f"steps={len(out['steps'])} (unnumbered {sum(1 for s in out['steps'] if s['num'] is None)}) callouts={len(co)} pieces={sum(c['qty'] for c in co)} "
          f"inventory={len(out['inventory'])} ({collections.Counter(e['how'] for e in out['inventory'])}) inv pieces={sum(e['qty'] or 0 for e in out['inventory'])}")


if __name__ == "__main__":
    main()

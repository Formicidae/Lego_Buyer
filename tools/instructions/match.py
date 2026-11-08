"""Match distinct callout images to inventory-page images (same renderer, same angle, different scale).

Usage: match.py <book.json> <book.pdf> <outdir>
Writes <outdir>/review.png (contact sheet) and <outdir>/match.json ({objid: [[element, score], ...]}).
"""
import json, sys, os, subprocess, collections
import numpy as np
from PIL import Image, ImageDraw, ImageFont

DPI = 288  # render scale: 4x PDF points
S = DPI / 72.0
CANVAS = 56


def render(pdf, pages, outdir):
    """pages: page numbers (for `pdf`) or (pdf_path, page) tuples. Returns {page or tuple: png}."""
    os.makedirs(outdir, exist_ok=True)
    have = {}
    for key in set(pages):
        src, pno = key if isinstance(key, tuple) else (pdf, key)
        tag = os.path.basename(src).split(".")[0]
        fn = os.path.join(outdir, f"{tag}_p{pno:03d}.png")
        if not os.path.exists(fn):
            subprocess.run(["pdftoppm", "-f", str(pno), "-l", str(pno), "-r", str(DPI), "-png", "-singlefile", src, fn[:-4]], check=True)
        have[key] = fn
        if not isinstance(key, tuple):
            have[(src, pno)] = fn
    return have


def crop(pagepng, bbox, pad=1):
    im = Image.open(pagepng).convert("RGB")
    x0, t, x1, b = [v * S for v in bbox]
    X0 = min(max(0, int(min(x0, x1)) - pad), im.width - 2); Y0 = min(max(0, int(min(t, b)) - pad), im.height - 2)
    X1 = max(X0 + 1, min(im.width, int(max(x0, x1)) + pad + 1)); Y1 = max(Y0 + 1, min(im.height, int(max(t, b)) + pad + 1))
    return im.crop((X0, Y0, X1, Y1))


def key_bg(im, tol=0.10):
    """Replace the background (median colour of the border ring) with white; return (image, fg mask)."""
    a = np.asarray(im.convert("RGB")).astype(float) / 255.0
    h, w, _ = a.shape
    ring = np.concatenate([a[0], a[-1], a[:, 0], a[:, -1]])
    bg = np.median(ring, axis=0)
    dist = np.linalg.norm(a - bg, axis=2)
    mask = dist > tol
    # drop specks: keep the mask's bounding content only
    out = a.copy(); out[~mask] = 1.0
    return Image.fromarray((out * 255).astype(np.uint8)), mask


def tight(im, mask=None, thr=245, min_frac=0.04):
    """Bounding box of the significant foreground: connected components smaller than min_frac of the
    largest one (specks, box edges, bits of neighbouring glyphs) are ignored."""
    from scipy import ndimage
    if mask is None:
        a = np.asarray(im.convert("RGB")).astype(int)
        mask = (a.min(axis=2) < thr)
    lab, n = ndimage.label(mask)
    if n == 0:
        return im
    sizes = ndimage.sum(mask, lab, range(1, n + 1))
    keep = np.isin(lab, [i + 1 for i, sz in enumerate(sizes) if sz >= max(sizes) * min_frac and sz >= 6])
    ys, xs = np.where(keep)
    if len(xs) == 0:
        return im
    return im.crop((xs.min(), ys.min(), xs.max() + 1, ys.max() + 1))


def norm(im):
    """Key out the background, tight-crop, fit into CANVAS x CANVAS on white, keep aspect."""
    im, mask = key_bg(im)
    im = tight(im, mask)
    w, h = im.size
    sc = (CANVAS - 2) / max(w, h)
    im2 = im.resize((max(1, int(w * sc)), max(1, int(h * sc))), Image.LANCZOS)
    out = Image.new("RGB", (CANVAS, CANVAS), "white")
    out.paste(im2, ((CANVAS - im2.width) // 2, (CANVAS - im2.height) // 2))
    return out, (w / h if h else 1)


def feats(im):
    n, aspect = norm(im)
    a = np.asarray(n).astype(float) / 255.0
    mask = np.linalg.norm(a - 1.0, axis=2) > 0.08
    fg = a[mask] if mask.any() else a.reshape(-1, 3)
    mean_col = fg.mean(axis=0)
    # colour as a small histogram too (helps tan vs grey vs dark grey)
    gray = a.mean(axis=2)
    gray = (gray - gray.mean()) / (gray.std() + 1e-6)
    return {"aspect": aspect, "col": mean_col, "gray": gray, "mask": mask.astype(float), "fill": mask.mean()}


def score(f1, f2):
    ar = abs(np.log((f1["aspect"] + 1e-3) / (f2["aspect"] + 1e-3)))
    col = np.linalg.norm(f1["col"] - f2["col"])
    corr = float((f1["gray"] * f2["gray"]).mean())
    iou = float((np.minimum(f1["mask"], f2["mask"]).sum()) / (np.maximum(f1["mask"], f2["mask"]).sum() + 1e-6))
    return 1.5 * corr + 2.0 * iou - 5.0 * col - 1.5 * ar


def dhash(im, size=16):
    n, _ = norm(im)
    g = np.asarray(n.convert("L").resize((size + 1, size), Image.LANCZOS)).astype(int)
    return (g[:, 1:] > g[:, :-1]).flatten()


def assign_keys(d, pdf, outdir, ham=6, col_tol=0.04):
    """Cluster callout crops that show the same picture (perceptual hash + colour) and set c['key']."""
    pages = sorted({s["page"] for s in d["steps"]})
    pngs = render(pdf, pages, os.path.join(outdir, "pages"))
    clusters = []  # (hash, colour, key)
    for s in d["steps"]:
        for c in s["callouts"]:
            if c["bbox"] is None:
                c["key"] = None
                continue
            if c.get("key"):
                continue  # identity known from the PDF image object
            im = crop(pngs[s["page"]], c["bbox"])
            h = dhash(im); f = feats(im)
            best = None
            for ch, ccol, key in clusters:
                dist = int((ch != h).sum())
                if dist <= ham and np.linalg.norm(ccol - f["col"]) <= col_tol and (best is None or dist < best[0]):
                    best = (dist, key)
            if best:
                c["key"] = best[1]
            else:
                c["key"] = f"k{len(clusters)}"
                clusters.append((h, f["col"], c["key"]))
    return len(clusters)


def main(jpath, pdf, outdir):
    d = json.load(open(jpath))
    callouts = collections.OrderedDict()
    for s in d["steps"]:
        for c in s["callouts"]:
            if c["key"] is None or c["bbox"] is None:
                continue
            e = callouts.setdefault(c["key"], {"qty": 0, "n": 0, "first": (s["page"], c["bbox"]), "steps": []})
            e["qty"] += c["qty"]; e["n"] += 1; e["steps"].append((s["page"], s["num"]))
    inv = [e for e in d["inventory"] if e["bbox"]]
    pages = [v["first"][0] for v in callouts.values()] + [(e.get("pdf", pdf), e["page"]) for e in inv]
    pngs = render(pdf, pages, os.path.join(outdir, "pages"))
    inv_imgs = []
    for e in inv:
        im = crop(pngs[(e.get("pdf", pdf), e["page"])], e["bbox"], pad=2)
        inv_imgs.append((e, im, feats(im)))
    results = {}
    rows = []
    for objid, v in callouts.items():
        im = crop(pngs[v["first"][0]], v["first"][1])
        f = feats(im)
        scored = sorted(((score(f, fi), e) for e, _, fi in inv_imgs), key=lambda t: -t[0])[:10]
        results[str(objid)] = [[e["element"], round(sc, 3), e["qty"]] for sc, e in scored]
        rows.append((objid, v, im, scored))
    json.dump({"callouts": {str(k): {"qty": v["qty"], "n": v["n"], "steps": v["steps"]} for k, v in callouts.items()}, "matches": results,
               "inventory": [{"element": e["element"], "qty": e["qty"]} for e in inv]}, open(os.path.join(outdir, "match.json"), "w"), indent=0)
    # contact sheet: one row per callout objid
    cell = 96; cols = 1 + 4
    font = ImageFont.load_default()
    sheet = Image.new("RGB", (cols * cell + 260, len(rows) * cell), "white")
    dr = ImageDraw.Draw(sheet)
    inv_by = {e["element"]: im for e, im, _ in inv_imgs}
    for r, (objid, v, im, scored) in enumerate(rows):
        y = r * cell
        n, _ = norm(im); sheet.paste(n.resize((cell - 8, cell - 8)), (4, y + 4))
        dr.text((cell + 4, y + 4), f"#{r} obj {objid}", fill="black", font=font)
        dr.text((cell + 4, y + 18), f"{v['qty']}x in {v['n']} callouts", fill="black", font=font)
        dr.text((cell + 4, y + 32), f"{v['steps'][0]}", fill="gray", font=font)
        for k, (sc, e) in enumerate(scored):
            x = cell + 120 + k * (cell + 20)
            n2, _ = norm(inv_by[e["element"]]); sheet.paste(n2.resize((cell - 8, cell - 8)), (x + 4, y + 4))
            dr.text((x + 4, y + cell - 22), f"{e['element']} {e['qty']}x", fill="black", font=font)
            dr.text((x + 4, y + cell - 11), f"{sc:.2f}", fill="gray", font=font)
        dr.line((0, y + cell - 1, sheet.width, y + cell - 1), fill="#ddd")
    sheet.save(os.path.join(outdir, "review.png"))
    print(f"{len(rows)} callout images, {len(inv)} inventory entries; sheet {sheet.size}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3])

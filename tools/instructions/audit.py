"""Audit sheets: each step's parts box from the book next to the inventory pictures of the parts the list gives it.

Usage: audit.py <book.pdf> <outdir> [rows_per_sheet]   (reads <outdir>/extract.json, steps.json; writes <outdir>/audit/NN.png)
Also prints pages whose "Nx" labels do not all have a callout, and the pieces of the parts list not placed in any step.
"""
import json, subprocess, os, sys, re, collections
from PIL import Image, ImageDraw
pdf, out = sys.argv[1], sys.argv[2]
per = int(sys.argv[3]) if len(sys.argv) > 3 else 9
S = 150 / 72
b = json.load(open(f'{out}/steps.json')); d = json.load(open(f'{out}/extract.json'))
inv = {e['element']: e for e in d['inventory']}
os.makedirs(f'{out}/audit', exist_ok=True)
cache = {}
def page(p):
    if p not in cache:
        fn = f'{out}/audit/p{p}.png'
        if not os.path.exists(fn):
            subprocess.run(['pdftoppm', '-f', str(p), '-l', str(p), '-r', '150', '-png', '-singlefile', pdf, fn[:-4]], check=True, stderr=subprocess.DEVNULL)
        cache[p] = Image.open(fn).convert('RGB')
    return cache[p]
def crop(p, bb, pad=3):
    x0, y0, x1, y1 = bb; im = page(p)
    return im.crop((max(0, int(min(x0, x1) * S) - pad), max(0, int(min(y0, y1) * S) - pad), int(max(x0, x1) * S) + pad, int(max(y0, y1) * S) + pad))
es = [e for e in d['steps'] if not (e['sub'] and not e['callouts'])]
bs = [s for s in b['steps'] if s.get('page') and s.get('step')]
rows = []; usedi = set()
for s in bs:
    sub = 'Sub-assembly' in s['label']
    cand = [i for i, e in enumerate(es) if i not in usedi and e['page'] == s['page'] and e['num'] == s['step'] and bool(e['sub']) == sub]
    if not cand: print('no extract step for', s['label']); continue
    q = sum(p['qty'] for p in s['parts'])
    cand.sort(key=lambda i: abs(sum(c['qty'] for c in es[i]['callouts']) - q))
    i = cand[0]; usedi.add(i); e = es[i]
    if not e['callouts'] and not s['parts']: continue
    cb = [c['bbox'] for c in e['callouts'] if c['bbox']]
    box = crop(s['page'], [min(x[0] for x in cb) - 6, min(x[1] for x in cb) - 6, max(x[2] for x in cb) + 6, max(x[3] for x in cb) + 14]) if cb else Image.new('RGB', (40, 40), 'white')
    rows.append((s, e, box))
RH = 112; W = 1500
for k in range(0, len(rows), per):
    chunk = rows[k:k + per]
    sh = Image.new('RGB', (W, RH * len(chunk)), 'white'); dr = ImageDraw.Draw(sh)
    for r, (s, e, box) in enumerate(chunk):
        y = r * RH
        sc = min(560 / box.width, (RH - 18) / box.height, 1.6); bx = box.resize((max(1, int(box.width * sc)), max(1, int(box.height * sc))))
        sh.paste(bx, (2, y + 14))
        qb = sum(c['qty'] for c in e['callouts']); qj = sum(p['qty'] for p in s['parts'])
        dr.text((2, y + 1), f"{s['label']}   book {qb} pcs / list {qj}" + ("   !!" if qb != qj else "") + (f"   NOTE {s.get('note')}" if s.get('note') else ''), fill='red' if qb != qj else 'black')
        x = 580
        for p in s['parts']:
            ie = inv.get(p['element_id'])
            if ie and ie['bbox']:
                im = crop(ie['page'], ie['bbox'], 1); sc = min(78 / im.width, (RH - 34) / im.height, 2.2); im = im.resize((max(1, int(im.width * sc)), max(1, int(im.height * sc)))); sh.paste(im, (x, y + 14))
            dr.text((x, y + RH - 18), f"{p['qty']}x {p['element_id']}", fill='black'); x += 92
        dr.line((0, y + RH - 1, W, y + RH - 1), fill='#bbb')
    sh.save(f'{out}/audit/{k // per:02d}.png')
print(len(rows), 'steps with parts ->', (len(rows) + per - 1) // per, 'sheets')
# label cross-check
import pdfplumber
perp = collections.Counter(); perq = collections.Counter()
for s in d['steps']:
    m = s.get('mult', 1) or 1
    for c in s['callouts']: perp[s['page']] += 1; perq[s['page']] += c['qty'] // m
inv_pages = {e['page'] for e in d['inventory']}
small = None
with pdfplumber.open(pdf) as pp:
    for i, pg in enumerate(pp.pages, start=1):
        if i in inv_pages: continue
        labs = [w for w in pg.extract_words(extra_attrs=['size']) if re.fullmatch(r'\d+x', w['text'])]
        sizes = collections.Counter(round(w['size']) for w in labs)
        if not labs: continue
        smallest = min(sizes); n = sizes[smallest]
        if n != perp[i]: print(f'  page {i}: {n} small Nx labels vs {perp[i]} callouts  (sizes {dict(sizes)})')
used = collections.Counter()
for s in b['steps']:
    for p in s['parts']: used[p['element_id']] += p['qty']
tot = collections.Counter()
for e in d['inventory']: tot[e['element']] += e['qty']
print('over:', {e: (used[e], tot[e]) for e in used if used[e] > tot[e]})
print('unplaced:', sum(q - used[e] for e, q in tot.items() if q > used[e]), {e: q - used[e] for e, q in tot.items() if q > used[e]})
print('steps with unidentified pieces:', [(s['label'], s['note']) for s in b['steps'] if s.get('note') and 'identified' in s['note']])

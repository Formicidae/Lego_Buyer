"""extract -> match -> assign -> steps JSON for the app.

Usage: build_steps.py <book.pdf> <outdir> [--book N] [--overrides ov.json] [--bags 1,2] [--source label]
"""
import argparse, json, os, sys, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import extract as ex
import match as mt
from assign import assign

ap = argparse.ArgumentParser()
ap.add_argument("pdf"); ap.add_argument("outdir")
ap.add_argument("--book", type=int, default=1)
ap.add_argument("--overrides", default=None, help="json {objid: element}; null leaves that callout unidentified")
ap.add_argument("--bags", default=None, help="only keep steps from these bag numbers, e.g. 1 or 1,2")
ap.add_argument("--source", default=None)
ap.add_argument("--no-match", action="store_true")
ap.add_argument("--inventory-from", default=None, help="extract.json of another book of the same set that has the parts inventory")
ap.add_argument("--min-score", type=float, default=-9, help="reject matches below this score (use ~1.6 for OCR'd scans)")
ap.add_argument("--extract-json", default=None, help="use this prebuilt extract.json (e.g. from ocr_extract.py) instead of reading the PDF text layer")
ap.add_argument("--main", default="26", help="main step numeral sizes, e.g. 26 or 38")
ap.add_argument("--sub", default="16,22", help="sub-assembly step numeral sizes")
ap.add_argument("--bag", default="30,36", help="bag marker numeral sizes")
ap.add_argument("--pins", default=None, help='json {element_id: {"part_num": .., "color_id": ..}} for ids Rebrickable cannot resolve')
ap.add_argument("--extra-steps", default=None, help='json list of hand-written steps for builds the book shows without a parts box (minifigures, droids): '
                '[{"page": 5, "bag": 1, "label": "...", "parts": [{"element_id": "...", "qty": 1}], "drop_empty": true}]; each goes before the first step '
                'on or after its page, and drop_empty removes that page\'s numbered steps that have no parts')
ap.add_argument("--leftovers-step", default=None, help="append a final step with this label holding every inventory piece not placed in a numbered step (minifigures, spares)")
a = ap.parse_args()
os.makedirs(a.outdir, exist_ok=True)

sizes = lambda v: tuple(int(x) for x in v.split(",") if x)
if a.extract_json:
    d = json.load(open(a.extract_json))
    for s in d["steps"]:
        s.setdefault("sub", False); s.setdefault("size", 26)
        if s.get("num") is None:
            s["num"] = 0
else:
    d = ex.extract(a.pdf, main_sizes=sizes(a.main), sub_sizes=sizes(a.sub), bag_sizes=sizes(a.bag))
# Repeated sub-assembly sections: an "Nx" at the section start (18pt in 2014 books, 24pt from ~2017)
# and a 40pt "Nx" at its end. Two layouts exist:
#  A) a main step inside the span carries the full callout (quantities already multiplied) and the
#     sub-steps repeat the per-copy callouts -> drop the sub-step callouts (they would double count);
#  B) only sub-steps inside the span, each showing one copy -> multiply them by N.
# Start markers are 16, 18 or 24pt depending on the book's vintage (16pt is also used for the small
# "built twice" boxes whose callouts already carry totals), so pair each 40pt end marker with the
# nearest preceding same-quantity marker after the previous end.
starts = sorted([o for o in d["odd_labels"] if round(o["size"]) in (16, 18, 24)], key=lambda o: (o["page"], o["top"]))
ends = sorted([o for o in d["odd_labels"] if round(o["size"]) == 40], key=lambda o: (o["page"], o["top"]))
spans = []
prev_end = (0, -1e9)
for en in ends:
    cands = [s for s in starts if prev_end < (s["page"], s["top"]) < (en["page"], en["top"]) and s["qty"] == en["qty"]]
    if cands:
        spans.append((cands[-1], en))
    prev_end = (en["page"], en["top"])
def in_span(s, st, en):
    after_start = s["page"] > st["page"] or (s["page"] == st["page"] and s["top"] >= st["top"] - 40)
    before_end = s["page"] < en["page"] or (s["page"] == en["page"] and s["top"] <= en["top"] + 20)
    return after_start and before_end
mult_pieces = dropped = 0
for s in d["steps"]:
    s["mult"] = 1
for st, en in spans:
    members = [s for s in d["steps"] if in_span(s, st, en)]
    mains = [s for s in members if not s["sub"] and s["callouts"]]
    subs = [s for s in members if s["sub"]]
    if len(mains) == 1 and subs:  # layout A: one main step with the full callout + per-copy sub-steps
        for s in members:
            if s["sub"] and s["callouts"]:
                dropped += sum(c["qty"] for c in s["callouts"]); s["callouts"] = []
    else:
        for s in members:
            if s["callouts"]:
                s["mult"] = st["qty"]
                for c in s["callouts"]:
                    mult_pieces += c["qty"] * (st["qty"] - 1); c["qty"] *= st["qty"]
print("repeat spans:", [(st["page"], en["page"], st["qty"], "A" if (sum(1 for s in d["steps"] if in_span(s, st, en) and not s["sub"] and s["callouts"]) == 1 and any(s["sub"] for s in d["steps"] if in_span(s, st, en))) else "B") for st, en in spans])
print("pieces added by repeats:", mult_pieces, " per-copy duplicates dropped:", dropped)
if a.inventory_from:
    other = json.load(open(a.inventory_from))
    d["inventory"] = [{**e, "pdf": other["file"]} for e in other["inventory"]]
if not a.no_match:
    print("callout picture clusters:", mt.assign_keys(d, a.pdf, a.outdir))
json.dump(d, open(os.path.join(a.outdir, "extract.json"), "w"), indent=0)
co = [c for s in d["steps"] for c in s["callouts"]]
print(f"steps={len(d['steps'])} callouts={len(co)} pieces={sum(c['qty'] for c in co)} inventory={len(d['inventory'])} inv pieces={sum(e['qty'] or 0 for e in d['inventory'])} odd={len(d['odd_labels'])}")
print("bags:", [(b['page'], b['num']) for b in d['bags']])
if a.no_match:
    sys.exit()
mt.main(os.path.join(a.outdir, "extract.json"), a.pdf, a.outdir)  # NOTE: match totals use un-multiplied qty
m = json.load(open(os.path.join(a.outdir, "match.json")))
got, cap = assign(m, min_score=a.min_score)
if a.overrides:
    for k, v in json.load(open(a.overrides)).items():
        if v is None:
            got.pop(str(k), None)  # "I can't tell either": leave it unidentified rather than wrong
        else:
            got[str(k)] = (v, 9.0)
un = [o for o in m["callouts"] if o not in got]
print("unassigned:", [(o, m["callouts"][o]["qty"]) for o in un])
left = {e: q for e, q in cap.items() if q > 0}
print(f"inventory left: {len(left)} elements / {sum(left.values())} pieces:", left)

# bag per page: a lone big numeral on a page marks the start of that bag
marks = collections.defaultdict(list)
for b in d["bags"]:
    marks[b["page"]].append(b["num"])
bag_start = {}
for pg in sorted(marks):
    nums = marks[pg]
    if len(nums) != 1:
        continue
    if not bag_start or nums[0] == max(bag_start.values()) + 1:
        bag_start[pg] = nums[0]  # bag markers run 1, 2, 3, ...; other lone big numerals are section headers
def bag_for(page):
    cur = None
    for pg in sorted(bag_start):
        if pg <= page:
            cur = bag_start[pg]
    return cur

keep_bags = {int(x) for x in a.bags.split(",")} if a.bags else None
steps_out = []
for s in sorted(d["steps"], key=lambda s: (s["page"], s["sub"], s["num"])):
    bag = bag_for(s["page"])
    if keep_bags and bag not in keep_bags:
        continue
    if not s["callouts"] and s["page"] < min(bag_start, default=0):
        continue  # bag overview numerals before the first bag starts
    if s["sub"] and not s["callouts"]:
        continue  # sub-assembly illustration steps whose parts are in the parent step's callout
    parts = collections.Counter()
    unknown = 0
    for c in s["callouts"]:
        el = got.get(str(c["key"]), (None,))[0] if c["key"] is not None else None
        if el:
            parts[el] += c["qty"]
        else:
            unknown += c["qty"]
    sub = s["sub"]
    label = " · ".join(x for x in [f"Bag {bag}" if bag else "", f"p.{s['page']}", (f"Sub-assembly step {s['num']}" if sub else f"Step {s['num']}"), (f"×{s['mult']}" if s.get("mult", 1) > 1 else "")] if x)
    steps_out.append({"book": a.book, "page": s["page"], "step": s["num"], "bag": bag, "label": label,
                      "parts": [{"element_id": e, "qty": q} for e, q in parts.items()],
                      **({"note": f"{unknown} piece(s) in this step could not be identified"} if unknown else {})})
if a.extra_steps and not keep_bags:
    for ex in sorted(json.load(open(a.extra_steps)), key=lambda e: -e["page"]):
        if ex.get("drop_empty"):
            steps_out = [s for s in steps_out if not (s["page"] == ex["page"] and not s["parts"] and not s.get("note"))]
        at = next((i for i, s in enumerate(steps_out) if s["page"] >= ex["page"]), len(steps_out))
        bag = ex.get("bag")
        steps_out.insert(at, {"book": a.book, "page": ex["page"], "step": 0, "bag": bag,
                              "label": " · ".join(x for x in [f"Bag {bag}" if bag else "", f"p.{ex['page']}", ex["label"]] if x),
                              "parts": ex["parts"], "note": ex.get("note", "Built without a parts box in the book")})
    print("extra steps added:", len(json.load(open(a.extra_steps))))
if a.leftovers_step and not keep_bags:
    used = collections.Counter()
    for s in steps_out:
        for p in s["parts"]:
            used[p["element_id"]] += p["qty"]
    inv = collections.Counter()
    for e in d["inventory"]:
        inv[e["element"]] += e["qty"]
    rest = {e: q - used[e] for e, q in inv.items() if q - used[e] > 0}
    if rest:
        steps_out.append({"book": a.book, "step": len(steps_out) + 1, "label": a.leftovers_step,
                          "parts": [{"element_id": e, "qty": q} for e, q in rest.items()],
                          "note": "Pieces from the parts list that do not appear in any numbered step (minifigure parts and spares)"})
        print(f"leftovers step: {sum(rest.values())} pieces")
if a.pins:
    pins = json.load(open(a.pins))
    for s in steps_out:
        s["parts"] = [{**pins[p["element_id"]], "element_id": p["element_id"], "qty": p["qty"]} if p["element_id"] in pins else p for p in s["parts"]]
out = {"source": a.source or f"pdf {os.path.basename(a.pdf)}", "steps": steps_out}
json.dump(out, open(os.path.join(a.outdir, "steps.json"), "w"), indent=1)
print(f"wrote {len(steps_out)} steps, {sum(q['qty'] for s in steps_out for q in s['parts'])} pieces -> {a.outdir}/steps.json")

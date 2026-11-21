"""Storage bins: Rebrickable's part categories grouped into about a dozen physical bins, in an order that
follows the kind of part (bricks, slopes, plates, tiles, Technic, ...) instead of the alphabet.

The "Piece type" sort walks the bins in order, so a list can be worked through one bin at a time. The
grouping below is only a suggestion: /bins lets it be renamed, reordered and regrouped, and the result is
stored in the settings table as [{"name": ..., "cats": [category names in order]}].
"""
import json
import re

from . import db

SETTING = "bins"
MINIFIG = "Minifigure"  # whole minifigures (sourced as a unit) are filed like a category

# (bin name, patterns tried in order against the category name). First bin with a match wins, so
# "Technic Bricks" lands with the bricks before the Technic catch-all sees it.
DEFAULT = [
    ("Bricks", [r"^bricks$"]),
    ("Bricks: special, round & Technic", [r"^bricks special", r"^bricks round", r"^technic bricks"]),
    ("Slopes, curves & wedges", [r"^bricks sloped", r"^bricks curved", r"^bricks wedged"]),
    ("Plates", [r"^plates$"]),
    ("Plates: special", [r"^plates special"]),
    ("Plates: wedge, round & dishes", [r"^plates angled", r"^plates round"]),
    ("Tiles", [r"^tiles$", r"^tiles special", r"^tiles round", r"^tiles"]),
    ("Technic pins, axles & bushes", [r"^technic pins", r"^technic axles", r"^technic bushes"]),
    ("Technic beams, connectors & gears", [r"^technic beams$", r"^technic beams", r"^technic connectors", r"^technic gears", r"^technic", r"^pneumatics", r"^mechanical"]),
    ("Hinges, bars & clips", [r"^hinges", r"^bars", r"^supports", r"^tubes", r"^string"]),
    ("Panels, windows & windscreens", [r"^panels", r"^windows", r"^windscreens", r"^containers"]),
    ("Wheels & vehicle parts", [r"^wheels", r"^transportation"]),
    ("Minifigures & accessories", [r"^minifig accessories", r"^tools", r"^minifig", r"^minidoll", r"^flags", r"^energy"]),
    ("Everything else", [r""]),
]


def _default_place(cat):
    """-> (default bin name, rank inside it) for a category name."""
    low = (cat or "").lower()
    for name, pats in DEFAULT:
        for i, pat in enumerate(pats):
            if re.search(pat, low):
                return name, i
    return DEFAULT[-1][0], 0


def saved():
    try:
        cfg = json.loads(db.setting_get(SETTING) or "null")
    except ValueError:
        cfg = None
    return cfg if isinstance(cfg, list) and cfg else None


def _usage():
    rows = db.q("SELECT c.name AS name, SUM(p.quantity) AS pieces, COUNT(*) AS lots FROM set_parts p "
                "JOIN part_categories c ON c.id = p.part_cat_id WHERE p.is_spare = 0 GROUP BY c.name")
    return {r["name"]: (r["pieces"] or 0, r["lots"]) for r in rows}


def layout():
    """The bins in order, each with every known category filed in it:
    {"custom": bool, "bins": [{"name", "cats": [{"name", "pieces", "lots"}]}]}"""
    cfg = saved()
    names = [b["name"] for b in cfg] if cfg else [n for n, _ in DEFAULT]
    placed = {}  # category -> (bin index, rank)
    if cfg:
        for bi, b in enumerate(cfg):
            for ci, c in enumerate(b.get("cats") or []):
                placed.setdefault(c, (bi, ci))
    cats = {r["name"] for r in db.q("SELECT name FROM part_categories")} | {MINIFIG}
    for c in cats:
        if c not in placed:
            dname, rank = _default_place(c)
            # a category this layout has never seen: its suggested bin if that still exists, else the last one
            placed[c] = (names.index(dname) if dname in names else len(names) - 1, 1000 + rank)
    use = _usage()
    bins = [{"name": n, "cats": []} for n in names]
    for c in sorted(cats, key=lambda c: (placed[c][0], placed[c][1], c)):
        if placed[c][0] < len(bins):
            pieces, lots = use.get(c, (0, 0))
            bins[placed[c][0]]["cats"].append({"name": c, "pieces": pieces, "lots": lots})
    return {"custom": bool(cfg), "bins": bins}


def save(bins):
    """bins: [{"name", "cats": [names]}] in the wanted order. Raises ValueError on a bad layout."""
    if not isinstance(bins, list) or not 1 <= len(bins) <= 40:
        raise ValueError("Between 1 and 40 bins, please.")
    out, seen_names, seen_cats = [], set(), set()
    for b in bins:
        name = str((b or {}).get("name") or "").strip()[:60]
        if not name:
            raise ValueError("Every bin needs a name.")
        if name.lower() in seen_names:
            raise ValueError(f"Two bins are called “{name}”.")
        seen_names.add(name.lower())
        cats = []
        for c in (b.get("cats") or []):
            c = str(c)[:80]
            if c and c not in seen_cats:
                seen_cats.add(c)
                cats.append(c)
        out.append({"name": name, "cats": cats})
    db.setting_set(SETTING, json.dumps(out))
    return layout()


def reset():
    db.setting_set(SETTING, None)
    return layout()


# What a Rebrickable category looks like in the hand, for the printed bin guide (matched on the lower-case name).
HINTS = {
    "bricks": "plain rectangular bricks: 1x1 up to 2x10, tall 1x2x5 columns",
    "bricks special": "bricks with studs on the side (SNOT), headlight bricks, grooved and masonry bricks, bricks with clips or handles",
    "bricks round and cones": "round 1x1 and 2x2 bricks, cones, cylinders, 4x4 round bricks, domes",
    "technic bricks": "bricks with pin or axle holes through the side",
    "bricks sloped": "roof slopes of every angle, inverted slopes, cheese wedges, corner and peak slopes",
    "bricks curved": "curved slopes, arches, bows, mudguard-shaped curves",
    "bricks wedged": "tapered wedge bricks, nose and wing-shaped wedges with studs",
    "plates": "plain rectangular plates: 1x1 up to 6x14 and larger",
    "plates special": "jumper plates, plates with clips, bars, handles, pin holes or side studs, brackets, tooth plates",
    "plates angled": "wing and wedge plates, cut-corner plates, left and right pairs",
    "plates round curved and dishes": "round plates 1x1 to 8x8, radar dishes, rounded-end and quarter-circle plates",
    "tiles": "smooth flat tiles: 1x1 up to 2x4 and larger",
    "tiles special": "grille tiles, tiles with a clip, handle or pin, inverted tiles, modified tiles",
    "tiles round and curved": "round tiles, quarter-circle and macaroni tiles, tiles with a hole",
    "tiles printed": "tiles with a printed pattern: gauges, screens, signs",
    "technic pins": "friction and smooth pins, long pins, axle-pins, pins with a stop",
    "technic axles": "cross axles of every length, axles with a stop or a stud end",
    "technic bushes": "bushes, half bushes, axle and pin spacers",
    "technic beams": "studless liftarms, straight and bent, thick and thin",
    "technic beams special": "liftarms with extra shapes: L and T beams, frames, beams with ball or pin ends",
    "technic connectors": "axle and pin connectors, angle connectors, cross blocks, axle joiners",
    "technic gears": "gears, worm gears, racks, clutch gears, differentials, pulleys",
    "technic panels": "Technic fairing and body panels",
    "technic special": "ball joints, shock absorbers, changeover catches, odd Technic parts",
    "technic steering, suspension and engine": "steering arms, hubs, wishbones, pistons, cylinders, engine blocks",
    "technic widgets": "small Technic odds and ends",
    "pneumatics": "pumps, cylinders, switches, air tanks",
    "mechanical": "winches, turntable gears, hooks, cranks, chain links",
    "hinges, arms and turntables": "hinge bricks and plates, click hinges, ball and socket joints, mechanical arms, turntables",
    "bars, ladders and fences": "bars and rods, 1x1 clips on a bar, antennas, ladders, fences, lattices",
    "supports, girders and cranes": "pillars, girders, crane arms, support stands",
    "tubes and hoses": "rigid and flexible hoses, tubing, pneumatic hose",
    "string, bands and reels": "string, rubber bands, winch reels, chains",
    "panels": "thin wall panels, corner panels, panels with side supports",
    "windows and doors": "window and door frames, glass, shutters, doors, gates",
    "windscreens and fuselage": "cockpit canopies, windscreens, aircraft hull and nose sections",
    "containers": "crates, boxes, barrels, chests, cupboards and their lids",
    "wheels and tyres": "wheels, rims, tyres, hubcaps, tank treads",
    "transportation - land": "mudguards, car chassis and bases, steering wheels, train parts, bumpers",
    "transportation - sea and air": "boat hulls, masts, propellers, rotors, wings, tail fins, engines",
    "minifigs": "whole minifigures, kept assembled",
    "minifig heads": "minifigure heads",
    "minifig upper body": "torsos, arms, hands",
    "minifig lower body": "legs, hips, skirts",
    "minifig headwear": "hair, helmets, hats, hoods",
    "minifig accessories": "weapons, lightsabers, blasters, capes, backpacks, cups, tools held by figures",
    "minidoll heads": "mini-doll heads (Friends style)",
    "tools": "wrenches, hammers, shovels, binoculars, radios",
    "flags, signs, plastics and cloth": "flags, sign posts, sails, capes and other cloth or plastic sheet",
    "energy effects": "flames, lightning, blast and power-burst pieces",
    "plants and animals": "leaves, flowers, trees, animals, bones",
    "rock": "rock panels, boulders, crystals, jewels",
    "baseplates": "thin baseplates, road plates, raised baseplates",
    "magnets and holders": "magnets, magnet holders, train couplings",
    "electronics": "motors, battery boxes, lights, sensors, wires",
    "stickers": "sticker sheets",
    "large buildable figures": "big buildable-figure limbs, armour and shells",
    "bionicle, hero factory and constraction": "Bionicle and Hero Factory limbs, masks, claws, ball-joint parts",
    "belville, scala and fabuland": "large doll-scale figures and their parts",
    "duplo, quatro and primo": "Duplo and other big-stud bricks",
    "clikits": "Clikits jewellery parts",
    "ho scale": "tiny HO-scale vehicles and parts from the 1950s-60s",
    "modulex": "Modulex architectural bricks (smaller than LEGO studs)",
    "znap": "Znap snap-together girders",
    "non-lego": "storage, books and other non-brick items",
    "other": "anything Rebrickable has not sorted elsewhere",
    "minifigure": "whole minifigures bought as a unit",
}


def hint(category):
    return HINTS.get((category or "").strip().lower(), "")

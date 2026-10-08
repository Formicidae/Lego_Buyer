"""SQLite storage. Small enough that plain sqlite3 is the simplest, most reliable option."""
import json
import sqlite3
import threading
import time
from contextlib import contextmanager

from .config import DB_PATH

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS sets (
    set_num TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    year INTEGER,
    num_parts INTEGER,
    img_url TEXT,
    loaded_at REAL NOT NULL,
    include_spares INTEGER NOT NULL DEFAULT 0,
    include_minifigs INTEGER NOT NULL DEFAULT 0,
    count_alt INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS part_categories (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS colors (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    rgb TEXT,
    is_trans INTEGER NOT NULL DEFAULT 0,
    external_ids TEXT
);

-- One row per (set, part, color). This is the unit everything else keys on.
CREATE TABLE IF NOT EXISTS set_parts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    set_num TEXT NOT NULL REFERENCES sets(set_num) ON DELETE CASCADE,
    part_num TEXT NOT NULL,
    part_name TEXT NOT NULL,
    part_cat_id INTEGER,
    color_id INTEGER NOT NULL,
    color_name TEXT NOT NULL,
    color_rgb TEXT,
    element_id TEXT,
    quantity INTEGER NOT NULL,
    is_spare INTEGER NOT NULL DEFAULT 0,
    img_url TEXT,
    external_ids TEXT,
    lego_price REAL,
    lego_tier TEXT,
    price_checked_at REAL,
    UNIQUE(set_num, part_num, color_id, is_spare)
);

CREATE TABLE IF NOT EXISTS set_minifigs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    set_num TEXT NOT NULL REFERENCES sets(set_num) ON DELETE CASCADE,
    fig_num TEXT NOT NULL,
    name TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    img_url TEXT,
    UNIQUE(set_num, fig_num)
);

-- Progress is keyed by a string so parts ("p:<set_parts.id>") and minifigs ("f:<set_minifigs.id>") share it.
CREATE TABLE IF NOT EXISTS progress (
    set_num TEXT NOT NULL,
    key TEXT NOT NULL,
    owned INTEGER NOT NULL DEFAULT 0,
    found_exact INTEGER NOT NULL DEFAULT 0,
    found_alt INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL,
    updated_by TEXT,
    PRIMARY KEY (set_num, key)
);

CREATE TABLE IF NOT EXISTS trips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    set_num TEXT NOT NULL,
    finished_at REAL NOT NULL,
    pieces INTEGER NOT NULL,
    lots INTEGER NOT NULL,
    lego_value REAL,
    finished_by TEXT
);

CREATE TABLE IF NOT EXISTS revisions (
    set_num TEXT PRIMARY KEY,
    rev INTEGER NOT NULL DEFAULT 0
);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


MIGRATIONS = [
    "ALTER TABLE sets ADD COLUMN kind TEXT NOT NULL DEFAULT 'set'",
    "ALTER TABLE sets ADD COLUMN source_id TEXT",
    "ALTER TABLE sets ADD COLUMN import_notes TEXT",
    "ALTER TABLE set_parts ADD COLUMN lego_available INTEGER",
    "ALTER TABLE set_parts ADD COLUMN lego_limit INTEGER",
    "ALTER TABLE set_parts ADD COLUMN lego_error TEXT",
]


def init():
    with connect() as conn:
        conn.executescript(SCHEMA)
        for m in MIGRATIONS:
            try:
                conn.execute(m)
            except sqlite3.OperationalError:
                pass  # column already exists


@contextmanager
def tx():
    """Serialized write transaction."""
    with _lock:
        conn = connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def q(sql, params=()):
    conn = connect()
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def q1(sql, params=()):
    rows = q(sql, params)
    return rows[0] if rows else None


def bump_rev(conn, set_num) -> int:
    conn.execute(
        "INSERT INTO revisions(set_num, rev) VALUES(?, 1) "
        "ON CONFLICT(set_num) DO UPDATE SET rev = rev + 1",
        (set_num,),
    )
    return conn.execute("SELECT rev FROM revisions WHERE set_num=?", (set_num,)).fetchone()[0]


def get_rev(set_num) -> int:
    row = q1("SELECT rev FROM revisions WHERE set_num=?", (set_num,))
    return row["rev"] if row else 0


# ---------- Sets ----------

def list_sets():
    return q(
        "SELECT s.*, "
        "(SELECT COUNT(*) FROM set_parts p WHERE p.set_num=s.set_num AND p.is_spare=0) AS lots "
        "FROM sets s ORDER BY loaded_at DESC"
    )


def get_set(set_num):
    return q1("SELECT * FROM sets WHERE set_num=?", (set_num,))


def replace_set(set_info, parts, minifigs, categories, colors):
    """Store a freshly fetched inventory. Progress rows are preserved by re-keying on
    (part_num, color_id, is_spare) / fig_num so a reload never loses counts."""
    set_num = set_info["set_num"]
    with tx() as conn:
        # Remember old progress keyed by natural identity.
        old_parts = {
            (r["part_num"], r["color_id"], r["is_spare"]): r["id"]
            for r in conn.execute("SELECT id, part_num, color_id, is_spare FROM set_parts WHERE set_num=?", (set_num,))
        }
        old_figs = {r["fig_num"]: r["id"] for r in conn.execute("SELECT id, fig_num FROM set_minifigs WHERE set_num=?", (set_num,))}
        old_progress = {r["key"]: dict(r) for r in conn.execute("SELECT * FROM progress WHERE set_num=?", (set_num,))}
        old_prices = {
            r["element_id"]: dict(r)
            for r in conn.execute("SELECT element_id, lego_price, lego_tier, lego_available, lego_limit, price_checked_at FROM set_parts WHERE set_num=? AND price_checked_at IS NOT NULL", (set_num,))
        }

        existing = conn.execute("SELECT include_spares, include_minifigs, count_alt FROM sets WHERE set_num=?", (set_num,)).fetchone()
        conn.execute("DELETE FROM set_parts WHERE set_num=?", (set_num,))
        conn.execute("DELETE FROM set_minifigs WHERE set_num=?", (set_num,))
        conn.execute("DELETE FROM progress WHERE set_num=?", (set_num,))
        conn.execute(
            "INSERT INTO sets(set_num, name, year, num_parts, img_url, loaded_at, include_spares, include_minifigs, count_alt, kind, source_id, import_notes) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(set_num) DO UPDATE SET "
            "name=excluded.name, year=excluded.year, num_parts=excluded.num_parts, img_url=excluded.img_url, loaded_at=excluded.loaded_at, "
            "kind=excluded.kind, source_id=excluded.source_id, import_notes=excluded.import_notes",
            (
                set_num, set_info["name"], set_info.get("year"), set_info.get("num_parts"), set_info.get("set_img_url"),
                time.time(),
                existing["include_spares"] if existing else 0,
                existing["include_minifigs"] if existing else 0,
                existing["count_alt"] if existing else 1,
                set_info.get("kind", "set"), set_info.get("source_id"), set_info.get("import_notes"),
            ),
        )
        for cid, cname in categories.items():
            conn.execute("INSERT OR REPLACE INTO part_categories(id, name) VALUES(?,?)", (cid, cname))
        for c in colors.values():
            conn.execute(
                "INSERT OR REPLACE INTO colors(id, name, rgb, is_trans, external_ids) VALUES(?,?,?,?,?)",
                (c["id"], c["name"], c.get("rgb"), 1 if c.get("is_trans") else 0, json.dumps(c.get("external_ids") or {})),
            )
        for p in parts:
            cur = conn.execute(
                "INSERT INTO set_parts(set_num, part_num, part_name, part_cat_id, color_id, color_name, color_rgb, element_id, "
                "quantity, is_spare, img_url, external_ids) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    set_num, p["part_num"], p["part_name"], p.get("part_cat_id"), p["color_id"], p["color_name"], p.get("color_rgb"),
                    p.get("element_id"), p["quantity"], 1 if p.get("is_spare") else 0, p.get("img_url"), json.dumps(p.get("external_ids") or {}),
                ),
            )
            op_ = old_prices.get(p.get("element_id"))
            if op_:
                conn.execute(
                    "UPDATE set_parts SET lego_price=?, lego_tier=?, lego_available=?, lego_limit=?, price_checked_at=? WHERE id=?",
                    (op_["lego_price"], op_["lego_tier"], op_["lego_available"], op_["lego_limit"], op_["price_checked_at"], cur.lastrowid),
                )
            old_id = old_parts.get((p["part_num"], p["color_id"], 1 if p.get("is_spare") else 0))
            if old_id is not None and f"p:{old_id}" in old_progress:
                op = old_progress[f"p:{old_id}"]
                conn.execute(
                    "INSERT INTO progress(set_num, key, owned, found_exact, found_alt, updated_at, updated_by) VALUES(?,?,?,?,?,?,?)",
                    (set_num, f"p:{cur.lastrowid}", op["owned"], op["found_exact"], op["found_alt"], op["updated_at"], op["updated_by"]),
                )
        for f in minifigs:
            cur = conn.execute(
                "INSERT INTO set_minifigs(set_num, fig_num, name, quantity, img_url) VALUES(?,?,?,?,?)",
                (set_num, f["fig_num"], f["name"], f["quantity"], f.get("img_url")),
            )
            old_id = old_figs.get(f["fig_num"])
            if old_id is not None and f"f:{old_id}" in old_progress:
                op = old_progress[f"f:{old_id}"]
                conn.execute(
                    "INSERT INTO progress(set_num, key, owned, found_exact, found_alt, updated_at, updated_by) VALUES(?,?,?,?,?,?,?)",
                    (set_num, f"f:{cur.lastrowid}", op["owned"], op["found_exact"], op["found_alt"], op["updated_at"], op["updated_by"]),
                )
        bump_rev(conn, set_num)


def delete_set(set_num):
    with tx() as conn:
        conn.execute("DELETE FROM progress WHERE set_num=?", (set_num,))
        conn.execute("DELETE FROM sets WHERE set_num=?", (set_num,))
        conn.execute("DELETE FROM revisions WHERE set_num=?", (set_num,))


def update_set_settings(set_num, **fields):
    allowed = {"include_spares", "include_minifigs", "count_alt"}
    sets = {k: 1 if v else 0 for k, v in fields.items() if k in allowed}
    if not sets:
        return
    with tx() as conn:
        conn.execute(
            f"UPDATE sets SET {', '.join(f'{k}=?' for k in sets)} WHERE set_num=?",
            (*sets.values(), set_num),
        )
        bump_rev(conn, set_num)


# ---------- Inventory + progress ----------

def set_items(set_num):
    """Everything the UI needs for one set: parts, minifigs, progress, categories."""
    parts = q(
        "SELECT p.*, c.name AS cat_name FROM set_parts p LEFT JOIN part_categories c ON c.id=p.part_cat_id "
        "WHERE p.set_num=? ORDER BY p.part_num, p.color_name",
        (set_num,),
    )
    figs = q("SELECT * FROM set_minifigs WHERE set_num=? ORDER BY name", (set_num,))
    prog = {r["key"]: r for r in q("SELECT * FROM progress WHERE set_num=?", (set_num,))}
    for p in parts:
        p["key"] = f"p:{p['id']}"
        p["external_ids"] = json.loads(p["external_ids"] or "{}")
        p.update(_prog_fields(prog.get(p["key"])))
    for f in figs:
        f["key"] = f"f:{f['id']}"
        f.update(_prog_fields(prog.get(f["key"])))
    return parts, figs


def _prog_fields(row):
    if not row:
        return {"owned": 0, "found_exact": 0, "found_alt": 0, "updated_at": None, "updated_by": None}
    return {k: row[k] for k in ("owned", "found_exact", "found_alt", "updated_at", "updated_by")}


def progress_snapshot(set_num):
    return {r["key"]: _prog_fields(r) for r in q("SELECT * FROM progress WHERE set_num=?", (set_num,))}


def adjust_progress(set_num, key, field, delta, who, maximum=None):
    """Atomically add delta to one counter, clamped to [0, maximum]. Returns the new row."""
    assert field in ("owned", "found_exact", "found_alt")
    with tx() as conn:
        row = conn.execute("SELECT * FROM progress WHERE set_num=? AND key=?", (set_num, key)).fetchone()
        cur = dict(row) if row else {"owned": 0, "found_exact": 0, "found_alt": 0}
        new = max(0, cur[field] + int(delta))
        if maximum is not None:
            new = min(new, maximum)
        cur[field] = new
        conn.execute(
            "INSERT INTO progress(set_num, key, owned, found_exact, found_alt, updated_at, updated_by) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(set_num, key) DO UPDATE SET owned=excluded.owned, found_exact=excluded.found_exact, "
            "found_alt=excluded.found_alt, updated_at=excluded.updated_at, updated_by=excluded.updated_by",
            (set_num, key, cur["owned"], cur["found_exact"], cur["found_alt"], time.time(), who),
        )
        rev = bump_rev(conn, set_num)
        return {**_prog_fields(conn.execute("SELECT * FROM progress WHERE set_num=? AND key=?", (set_num, key)).fetchone()), "rev": rev}


def set_progress(set_num, key, who, **values):
    with tx() as conn:
        row = conn.execute("SELECT * FROM progress WHERE set_num=? AND key=?", (set_num, key)).fetchone()
        cur = dict(row) if row else {"owned": 0, "found_exact": 0, "found_alt": 0}
        for k, v in values.items():
            if k in ("owned", "found_exact", "found_alt") and v is not None:
                cur[k] = max(0, int(v))
        conn.execute(
            "INSERT INTO progress(set_num, key, owned, found_exact, found_alt, updated_at, updated_by) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(set_num, key) DO UPDATE SET owned=excluded.owned, found_exact=excluded.found_exact, "
            "found_alt=excluded.found_alt, updated_at=excluded.updated_at, updated_by=excluded.updated_by",
            (set_num, key, cur["owned"], cur["found_exact"], cur["found_alt"], time.time(), who),
        )
        rev = bump_rev(conn, set_num)
        return {**_prog_fields(conn.execute("SELECT * FROM progress WHERE set_num=? AND key=?", (set_num, key)).fetchone()), "rev": rev}


def reset_progress(set_num, fields):
    with tx() as conn:
        sets = ", ".join(f"{f}=0" for f in fields if f in ("owned", "found_exact", "found_alt"))
        if sets:
            conn.execute(f"UPDATE progress SET {sets}, updated_at=? WHERE set_num=?", (time.time(), set_num))
        bump_rev(conn, set_num)


# ---------- LEGO prices ----------

def elements_to_price(set_num, max_age_hours=24, include_spares=False):
    """Distinct element IDs in the set whose price is missing or stale."""
    cutoff = time.time() - max_age_hours * 3600
    rows = q(
        "SELECT DISTINCT element_id FROM set_parts WHERE set_num=? AND element_id IS NOT NULL AND element_id != '' "
        "AND (price_checked_at IS NULL OR price_checked_at < ?)" + ("" if include_spares else " AND is_spare=0"),
        (set_num, cutoff),
    )
    return [r["element_id"] for r in rows]


def save_price(element_id, result, error=None):
    """Apply a lookup result to every set that contains this element."""
    with tx() as conn:
        if result is None and error:
            # Lookup failed (network, blocked, parser): keep any old price, record the error, retry next run.
            conn.execute("UPDATE set_parts SET lego_error=? WHERE element_id=?", (error[:300], element_id))
        elif result is None:
            # LEGO doesn't sell this element.
            conn.execute(
                "UPDATE set_parts SET lego_price=NULL, lego_tier=NULL, lego_available=0, lego_limit=NULL, lego_error=NULL, price_checked_at=? WHERE element_id=?",
                (time.time(), element_id),
            )
        else:
            conn.execute(
                "UPDATE set_parts SET lego_price=?, lego_tier=?, lego_available=?, lego_limit=?, lego_error=NULL, price_checked_at=? WHERE element_id=?",
                (result.get("price"), result.get("tier"), 1 if result.get("available") else 0, result.get("limit"), time.time(), element_id),
            )
        for r in conn.execute("SELECT DISTINCT set_num FROM set_parts WHERE element_id=?", (element_id,)):
            bump_rev(conn, r["set_num"])


def finish_trip(set_num, who=None):
    """Everything found at the store becomes owned; store counters reset. Returns a summary."""
    s = get_set(set_num)
    count_alt = bool(s and s.get("count_alt", 1))
    parts, figs = set_items(set_num)
    by_key = {p["key"]: p for p in parts + figs}
    pieces = lots = 0
    value = 0.0
    with tx() as conn:
        for r in conn.execute("SELECT * FROM progress WHERE set_num=? AND (found_exact > 0 OR found_alt > 0)", (set_num,)).fetchall():
            found = r["found_exact"] + (r["found_alt"] if count_alt else 0)
            if found <= 0:
                continue
            item = by_key.get(r["key"]) or {}
            pieces += found
            lots += 1
            if item.get("lego_price") is not None:
                value += item["lego_price"] * found
            conn.execute(
                "UPDATE progress SET owned = owned + ?, found_exact = 0, found_alt = 0, updated_at=?, updated_by=? WHERE set_num=? AND key=?",
                (found, time.time(), who, set_num, r["key"]),
            )
        conn.execute(
            "INSERT INTO trips(set_num, finished_at, pieces, lots, lego_value, finished_by) VALUES(?,?,?,?,?,?)",
            (set_num, time.time(), pieces, lots, round(value, 2), who),
        )
        bump_rev(conn, set_num)
    return {"pieces": pieces, "lots": lots, "lego_value": round(value, 2)}


def trips(set_num):
    return q("SELECT * FROM trips WHERE set_num=? ORDER BY finished_at DESC", (set_num,))

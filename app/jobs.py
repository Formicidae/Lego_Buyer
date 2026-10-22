"""Background jobs (one thread each, status readable by the UI). Small enough not to need a queue."""
import threading
import time

from . import bricklink, brickowl, db, lego

_jobs = {}
_lock = threading.Lock()


def status(name):
    with _lock:
        return dict(_jobs.get(name) or {"state": "idle"})


def start_price_job(set_num, force=False):
    name = f"prices:{set_num}"
    with _lock:
        cur = _jobs.get(name)
        if cur and cur.get("state") == "running":
            return dict(cur)
        _jobs[name] = {"state": "running", "done": 0, "total": 0, "errors": 0, "not_sold": 0, "started": time.time(), "last_error": None}

    def run():
        try:
            ids = db.elements_to_price(set_num, max_age_hours=0 if force else 24)
            with _lock:
                _jobs[name]["total"] = len(ids)
            for eid, res, err in lego.price_many(ids):
                db.save_price(eid, res, err)
                with _lock:
                    j = _jobs[name]
                    j["done"] += 1
                    if err:
                        j["errors"] += 1
                        j["last_error"] = err
                    elif res is None:
                        j["not_sold"] += 1
                # Stop hammering LEGO if everything is failing (blocked, outage).
                with _lock:
                    j = _jobs[name]
                    if j["done"] >= 5 and j["errors"] == j["done"]:
                        j["state"] = "failed"
                        j["message"] = "LEGO.com isn't answering price lookups right now. " + (j["last_error"] or "")
                        return
            with _lock:
                _jobs[name]["state"] = "done"
                _jobs[name]["finished"] = time.time()
        except Exception as e:  # pragma: no cover
            with _lock:
                _jobs[name].update({"state": "failed", "message": str(e)})

    threading.Thread(target=run, name=name, daemon=True).start()
    return status(name)


def start_wishlist_import(wishlist_id):
    name = f"import:bo-{wishlist_id}"
    with _lock:
        cur = _jobs.get(name)
        if cur and cur.get("state") == "running":
            return dict(cur)
        _jobs[name] = {"state": "running", "done": 0, "total": 0, "started": time.time(), "set_num": f"bo-{wishlist_id}"}

    def progress(done, total):
        with _lock:
            _jobs[name].update({"done": done, "total": total})

    def run():
        try:
            info, parts, figs, cats, colors, problems = brickowl.fetch_wishlist(wishlist_id, on_progress=progress)
            info["import_notes"] = "\n".join(problems) if problems else None
            db.replace_set(info, parts, figs, cats, colors)
            with _lock:
                _jobs[name].update({"state": "done", "finished": time.time(), "lots": len(parts), "problems": problems})
        except Exception as e:
            with _lock:
                _jobs[name].update({"state": "failed", "message": str(e)})

    threading.Thread(target=run, name=name, daemon=True).start()
    return status(name)


def start_bricklink_job(set_num, parts, force=False):
    """Price-guide lookups for the parts still needed (one call per part+color, cached a day)."""
    name = f"blprices:{set_num}"
    with _lock:
        cur = _jobs.get(name)
        if cur and cur.get("state") == "running":
            return dict(cur)
        _jobs[name] = {"state": "running", "done": 0, "total": 0, "errors": 0, "started": time.time(), "last_error": None}

    def run():
        try:
            todo = db.parts_to_price_bricklink(set_num, max_age_hours=0 if force else 24)
            by_key = {(p["part_num"], p["color_id"]): p for p in parts}
            todo = [t for t in todo if (t["part_num"], t["color_id"]) in by_key]
            with _lock:
                _jobs[name]["total"] = len(todo)
            for t in todo:
                p = by_key[(t["part_num"], t["color_id"])]
                bl_no, bl_col = bricklink.ids_for(p)
                try:
                    g = bricklink.price_guide("PART", bl_no, color_id=bl_col)
                    db.save_bl_price(t["part_num"], t["color_id"], g["qty_avg"] or g["avg"], g["qty"], g["lots"])
                except bricklink.BrickLinkError as e:
                    db.save_bl_price(t["part_num"], t["color_id"], None, 0, 0)
                    with _lock:
                        _jobs[name]["errors"] += 1
                        _jobs[name]["last_error"] = str(e)
                with _lock:
                    _jobs[name]["done"] += 1
                    j = _jobs[name]
                    if j["done"] >= 5 and j["errors"] == j["done"]:
                        j.update({"state": "failed", "message": "BrickLink isn't answering: " + (j["last_error"] or "")})
                        return
                time.sleep(0.4)
            with _lock:
                _jobs[name].update({"state": "done", "finished": time.time()})
        except Exception as e:
            with _lock:
                _jobs[name].update({"state": "failed", "message": str(e)})

    threading.Thread(target=run, name=name, daemon=True).start()
    return status(name)


def start_brickowl_push(set_num, list_name, items):
    name = f"bopush:{set_num}"
    with _lock:
        cur = _jobs.get(name)
        if cur and cur.get("state") == "running":
            return dict(cur)
        _jobs[name] = {"state": "running", "done": 0, "total": len(items), "started": time.time()}

    def progress(done, total):
        with _lock:
            _jobs[name].update({"done": done, "total": total})

    def run():
        try:
            wid, problems = brickowl.push_wishlist(list_name, items, on_progress=progress)
            with _lock:
                _jobs[name].update({"state": "done", "wishlist_id": wid, "problems": problems,
                                    "url": f"https://www.brickowl.com/mywishlist/{wid}", "finished": time.time()})
        except Exception as e:
            with _lock:
                _jobs[name].update({"state": "failed", "message": str(e)})

    threading.Thread(target=run, name=name, daemon=True).start()
    return status(name)

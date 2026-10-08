"""Background jobs (one thread each, status readable by the UI). Small enough not to need a queue."""
import threading
import time

from . import db, lego

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

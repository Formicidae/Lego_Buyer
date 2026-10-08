import contextlib
import hashlib
import hmac
import json
import logging
import logging.handlers
import os
import shutil
import subprocess
import sys
import threading
import time

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from . import brickowl, db, jobs, lego, rebrickable, views
from .checklist import build_checklist, render_checklist_html, render_checklist_pdf
from .config import APP_PASSCODE, SECRET, BRICKOWL_API_KEY, REBRICKABLE_API_KEY, DATA_DIR, CHROMIUM_PATH

LOG_PATH = DATA_DIR / "app.log"
_handler = logging.handlers.RotatingFileHandler(LOG_PATH, maxBytes=2_000_000, backupCount=3)
_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
for _name in ("", "uvicorn", "uvicorn.error", "uvicorn.access"):
    logging.getLogger(_name).addHandler(_handler)
logging.getLogger().setLevel(logging.INFO)
log = logging.getLogger("lego_buyer")

templates = Jinja2Templates(directory="app/templates")

COOKIE = "lb_auth"


def _token() -> str:
    return hmac.new(SECRET.encode(), b"lego-buyer-session", hashlib.sha256).hexdigest()


def _authed(request: Request) -> bool:
    if not APP_PASSCODE:
        return True  # No passcode configured: open (dev only).
    return hmac.compare_digest(request.cookies.get(COOKIE, ""), _token())


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        path = request.url.path
        if path.startswith("/static/") or path in ("/login", "/health", "/sw.js", "/manifest.webmanifest") or _authed(request):
            return await call_next(request)
        if path.startswith("/api/"):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return RedirectResponse(f"/login?next={path}", status_code=303)


# ---------- Pages ----------

async def login(request: Request):
    error = None
    if request.method == "POST":
        form = await request.form()
        if APP_PASSCODE and hmac.compare_digest(form.get("passcode", "").strip(), APP_PASSCODE):
            resp = RedirectResponse(form.get("next") or "/", status_code=303)
            resp.set_cookie(COOKIE, _token(), max_age=60 * 60 * 24 * 365, httponly=True, samesite="lax")
            return resp
        error = "That passcode isn't right."
    return templates.TemplateResponse(request, "login.html", {"error": error, "next": request.query_params.get("next", "/")})


async def home(request: Request):
    return templates.TemplateResponse(request, "index.html", {"set_num": None, "view_keys": "", "sets": db.list_sets()})


async def set_page(request: Request):
    set_num = request.path_params["set_num"]
    if not db.get_set(set_num):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "index.html", {"set_num": set_num, "view_keys": "", "sets": db.list_sets()})


async def view_page(request: Request):
    keys = views.parse_keys(request.path_params["keys"])
    if not views.view_info(keys):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "index.html", {"set_num": "", "view_keys": ",".join(keys), "sets": db.list_sets()})


async def admin_page(request: Request):
    return templates.TemplateResponse(request, "admin.html", {"sets": db.list_sets()})


async def sw(request: Request):
    # Served from the root so the worker's scope covers the whole app.
    return FileResponse("app/static/sw.js", media_type="application/javascript", headers={"Cache-Control": "no-cache"})


async def manifest(request: Request):
    return JSONResponse(
        {
            "name": "Lego Buyer", "short_name": "Lego Buyer", "start_url": "/", "display": "standalone",
            "background_color": "#f4f1e8", "theme_color": "#0c2117",
            "icons": [{"src": "/static/icon.svg", "sizes": "any", "type": "image/svg+xml"}],
        },
        media_type="application/manifest+json",
    )


async def health(request: Request):
    return JSONResponse(
        {
            "ok": True,
            "rebrickable_key": bool(REBRICKABLE_API_KEY),
            "brickowl_key": bool(BRICKOWL_API_KEY),
            "passcode": bool(APP_PASSCODE),
        }
    )


# ---------- API ----------

async def api_sets(request: Request):
    if request.method == "POST":
        body = await request.json()
        raw = (body.get("set_num") or "").strip()
        if raw.lower().startswith("bo-"):
            return JSONResponse({"job": jobs.start_wishlist_import(raw[3:]), "set": db.get_set(raw)})
        try:
            set_num = rebrickable.normalize_set_num(raw)
            set_info, parts, figs, cats, colors = await run_in_threadpool(rebrickable.fetch_set, set_num)
        except rebrickable.RebrickableError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        except Exception as e:  # network etc.
            return JSONResponse({"error": f"Couldn't load the set: {e}"}, status_code=502)
        db.replace_set(set_info, parts, figs, cats, colors)
        return JSONResponse({"set": db.get_set(set_info["set_num"])})
    return JSONResponse({"sets": db.list_sets()})


async def api_set(request: Request):
    set_num = request.path_params["set_num"]
    s = db.get_set(set_num)
    if not s:
        return JSONResponse({"error": "not found"}, status_code=404)
    if request.method == "DELETE":
        db.delete_set(set_num)
        return JSONResponse({"ok": True})
    parts, figs = db.set_items(set_num)
    return JSONResponse({"set": s, "parts": parts, "minifigs": figs, "rev": db.get_rev(set_num)})


async def api_progress(request: Request):
    set_num = request.path_params["set_num"]
    if request.method == "GET":
        client_rev = int(request.query_params.get("rev", "-1"))
        rev = db.get_rev(set_num)
        if rev == client_rev:
            return JSONResponse({"rev": rev, "changed": False})
        return JSONResponse({"rev": rev, "changed": True, "progress": db.progress_snapshot(set_num), "set": db.get_set(set_num)})
    body = await request.json()
    key, field, who = body.get("key"), body.get("field"), (body.get("who") or "")[:40]
    if field not in ("owned", "found_exact", "found_alt") or not key:
        return JSONResponse({"error": "bad request"}, status_code=400)
    if "value" in body:
        row = db.set_progress(set_num, key, who, **{field: body["value"]})
    else:
        row = db.adjust_progress(set_num, key, field, body.get("delta", 1), who)
    return JSONResponse(row)


async def api_settings(request: Request):
    set_num = request.path_params["set_num"]
    body = await request.json()
    db.update_set_settings(set_num, **body)
    return JSONResponse({"set": db.get_set(set_num), "rev": db.get_rev(set_num)})


async def api_finish_trip(request: Request):
    set_num = request.path_params["set_num"]
    body = await request.json()
    summary = db.finish_trip(set_num, (body.get("who") or "")[:40])
    return JSONResponse({**summary, "rev": db.get_rev(set_num)})


async def api_reset(request: Request):
    set_num = request.path_params["set_num"]
    body = await request.json()
    db.reset_progress(set_num, body.get("fields", []))
    return JSONResponse({"rev": db.get_rev(set_num)})


async def api_prices(request: Request):
    set_num = request.path_params["set_num"]
    if not db.get_set(set_num):
        return JSONResponse({"error": "not found"}, status_code=404)
    if request.method == "POST":
        body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        return JSONResponse(jobs.start_price_job(set_num, force=bool(body.get("force"))))
    return JSONResponse(jobs.status(f"prices:{set_num}"))


def _buyplan(s, parts, job):
    """What's still needed after owned + found, priced against LEGO with the US fee rules applied."""
    count_alt = bool(s.get("count_alt", 1))
    items = []
    for p in parts:
        if p["is_spare"] and not s.get("include_spares"):
            continue
        rem = max(0, p["quantity"] - p["owned"] - p["found_exact"] - (p["found_alt"] if count_alt else 0))
        if rem <= 0:
            continue
        sold = p.get("lego_price") is not None and p.get("lego_available")
        items.append({"key": p["key"], "element_id": p["element_id"], "qty": rem, "price": p.get("lego_price"),
                      "tier": p.get("lego_tier") if sold else None, "sold_by_lego": bool(sold), "checked": p.get("price_checked_at") is not None})
    lego_items = [i for i in items if i["sold_by_lego"]]
    plan = lego.plan_fees(lego_items)
    return {
        "lego": plan,
        "lego_lots": len(lego_items),
        "not_sold_by_lego": [i for i in items if i["checked"] and not i["sold_by_lego"]],
        "unpriced": [i for i in items if not i["checked"]],
        "job": job,
    }


async def api_buyplan(request: Request):
    set_num = request.path_params["set_num"]
    s = db.get_set(set_num)
    if not s:
        return JSONResponse({"error": "not found"}, status_code=404)
    parts, figs = db.set_items(set_num)
    return JSONResponse(_buyplan(s, parts, jobs.status(f"prices:{set_num}")))


# ---------- Combined views (mirror the set endpoints) ----------

def _view(request):
    keys = views.parse_keys(request.path_params["keys"])
    info = views.view_info(keys)
    return keys, info


async def api_view(request: Request):
    keys, info = _view(request)
    if not info:
        return JSONResponse({"error": "not found"}, status_code=404)
    parts, figs = views.items(keys)
    return JSONResponse({"set": info, "parts": parts, "minifigs": figs, "rev": views.rev(keys)})


async def api_view_progress(request: Request):
    keys, info = _view(request)
    if not info:
        return JSONResponse({"error": "not found"}, status_code=404)
    if request.method == "GET":
        r = views.rev(keys)
        if r == request.query_params.get("rev"):
            return JSONResponse({"rev": r, "changed": False})
        return JSONResponse({"rev": r, "changed": True, "progress": views.progress_snapshot(keys), "set": info})
    body = await request.json()
    key, field, who = body.get("key"), body.get("field"), (body.get("who") or "")[:40]
    if field not in ("owned", "found_exact", "found_alt") or not key:
        return JSONResponse({"error": "bad request"}, status_code=400)
    if "value" in body:
        row = views.set_value(keys, key, field, body["value"], who, bool(info["count_alt"]))
    else:
        row = views.adjust(keys, key, field, body.get("delta", 1), who, bool(info["count_alt"]))
    if row is None:
        return JSONResponse({"error": "unknown item"}, status_code=404)
    return JSONResponse({**row, "rev": views.rev(keys)})


async def api_view_settings(request: Request):
    keys, info = _view(request)
    body = await request.json()
    for k in keys:
        db.update_set_settings(k, **body)
    return JSONResponse({"set": views.view_info(keys), "rev": views.rev(keys)})


async def api_view_reset(request: Request):
    keys, info = _view(request)
    body = await request.json()
    for k in keys:
        db.reset_progress(k, body.get("fields", []))
    return JSONResponse({"rev": views.rev(keys)})


async def api_view_finish_trip(request: Request):
    keys, info = _view(request)
    body = await request.json()
    total = {"pieces": 0, "lots": 0, "lego_value": 0.0}
    for k in keys:
        r = db.finish_trip(k, (body.get("who") or "")[:40])
        for f in total:
            total[f] += r[f]
    total["lego_value"] = round(total["lego_value"], 2)
    return JSONResponse({**total, "rev": views.rev(keys)})


async def api_view_prices(request: Request):
    keys, info = _view(request)
    if request.method == "POST":
        body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        for k in keys:
            jobs.start_price_job(k, force=bool(body.get("force")))
    sts = [jobs.status(f"prices:{k}") for k in keys]
    agg = {"state": "running" if any(x["state"] == "running" for x in sts) else ("failed" if any(x["state"] == "failed" for x in sts) else ("done" if any(x["state"] == "done" for x in sts) else "idle")),
           "done": sum(x.get("done", 0) for x in sts), "total": sum(x.get("total", 0) for x in sts),
           "errors": sum(x.get("errors", 0) for x in sts), "not_sold": sum(x.get("not_sold", 0) for x in sts),
           "last_error": next((x.get("last_error") for x in sts if x.get("last_error")), None),
           "message": next((x.get("message") for x in sts if x.get("message")), None)}
    return JSONResponse(agg)


async def api_view_buyplan(request: Request):
    keys, info = _view(request)
    if not info:
        return JSONResponse({"error": "not found"}, status_code=404)
    parts, figs = views.items(keys)
    return JSONResponse(_buyplan(info, parts, jobs.status(f"prices:{keys[0]}")))


async def view_checklist_html(request: Request):
    keys, info = _view(request)
    if not info:
        return RedirectResponse("/", status_code=303)
    ctx = build_checklist(info, *views.items(keys), mode=request.query_params.get("mode", "trip"), order=request.query_params.get("order", "color"))
    return HTMLResponse(render_checklist_html(ctx))


async def view_checklist_pdf(request: Request):
    keys, info = _view(request)
    if not info:
        return RedirectResponse("/", status_code=303)
    mode = request.query_params.get("mode", "trip")
    ctx = build_checklist(info, *views.items(keys), mode=mode, order=request.query_params.get("order", "color"))
    try:
        pdf = await run_in_threadpool(render_checklist_pdf, ctx)
    except Exception as e:
        return JSONResponse({"error": f"PDF rendering failed: {e}"}, status_code=500)
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="combined-{mode}-checklist-{time.strftime("%Y%m%d")}.pdf"'})


# ---------- BrickOwl ----------

async def api_bo_wishlists(request: Request):
    try:
        return JSONResponse({"wishlists": await run_in_threadpool(brickowl.wishlists)})
    except brickowl.BrickOwlError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": f"Couldn't reach BrickOwl: {e}"}, status_code=502)


async def api_bo_import(request: Request):
    body = await request.json()
    wid = str(body.get("wishlist_id") or "").strip()
    if not wid:
        return JSONResponse({"error": "wishlist_id required"}, status_code=400)
    return JSONResponse(jobs.start_wishlist_import(wid))


async def api_job(request: Request):
    return JSONResponse(jobs.status(request.path_params["name"]))


# ---------- Admin ----------

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _git(*args):
    try:
        return subprocess.run(["git", *args], cwd=APP_ROOT, capture_output=True, text=True, timeout=120).stdout.strip()
    except Exception as e:
        return f"(git failed: {e})"


def QUERY_CACHE_LEN():
    try:
        return len(lego.QUERY_CACHE.read_text())
    except OSError:
        return 0


def _diagnostics():
    out = {"version": _git("log", "-1", "--format=%h %s (%cd)", "--date=short"), "branch": _git("rev-parse", "--abbrev-ref", "HEAD")}
    out["keys"] = {"rebrickable": bool(REBRICKABLE_API_KEY), "brickowl": bool(BRICKOWL_API_KEY), "passcode": bool(APP_PASSCODE)}
    out["pdf_engine"] = f"system chromium ({CHROMIUM_PATH})" if CHROMIUM_PATH else "playwright"
    try:
        du = shutil.disk_usage(DATA_DIR)
        out["disk_free_mb"] = du.free // 1_000_000
    except Exception:
        pass
    try:
        with open("/proc/meminfo") as f:
            mem = {l.split(":")[0]: int(l.split()[1]) // 1024 for l in f if l.startswith(("MemTotal", "MemAvailable"))}
        out["memory_mb"] = mem
    except Exception:
        pass
    try:
        out["rebrickable"] = "ok" if rebrickable._get("/colors/", {"page_size": 1}).get("count") else "unexpected response"
    except Exception as e:
        out["rebrickable"] = f"FAIL: {e}"
    try:
        out["brickowl"] = f"ok, {len(brickowl.wishlists())} wishlists"
    except Exception as e:
        out["brickowl"] = f"FAIL: {e}"
    out["lego_query"] = f"cached, {QUERY_CACHE_LEN()} chars" if lego.QUERY_CACHE.exists() else "not discovered yet"
    try:
        r = lego.lookup("6204668")
        out["lego"] = f"ok via {r['source']}: ${r['price']:.2f}" + (f" ({r['tier']})" if r and r.get("tier") else " (tier unknown)") if r else "element 6204668 not found?"
    except Exception as e:
        out["lego"] = f"FAIL: {e}"
    try:
        out["tailscale"] = subprocess.run(["tailscale", "funnel", "status"], capture_output=True, text=True, timeout=10).stdout.strip() or "(no funnel configured)"
    except Exception:
        out["tailscale"] = "(tailscale not installed)"
    return out


async def api_admin_discover(request: Request):
    def run():
        q = lego.discover_query(force=True)
        try:
            probe = lego.graphql_lookup("6204668")
        except Exception as e:
            probe = f"FAIL: {e}"
        return {"chars": len(q), "head": q[:600], "variables": lego.query_variables(q, "6204668"), "probe_6204668": probe}
    try:
        return JSONResponse(await run_in_threadpool(run))
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


async def api_admin_diagnostics(request: Request):
    return JSONResponse(await run_in_threadpool(_diagnostics))


async def api_admin_logs(request: Request):
    n = int(request.query_params.get("lines", "200"))
    try:
        with open(LOG_PATH, "r", errors="replace") as f:
            lines = f.readlines()[-n:]
    except FileNotFoundError:
        lines = []
    return Response("".join(lines) or "(no log yet)", media_type="text/plain")


async def api_admin_update(request: Request):
    """git pull + pip install, then exit so systemd (Restart=always) brings up the new code."""
    def run():
        log.info("admin update: pulling")
        before = _git("rev-parse", "HEAD")
        pull = _git("pull", "--ff-only")
        after = _git("rev-parse", "HEAD")
        log.info("admin update: %s", pull)
        pip = os.path.join(APP_ROOT, ".venv", "bin", "pip")
        if os.path.exists(pip):
            subprocess.run([pip, "install", "-q", "-r", os.path.join(APP_ROOT, "requirements.txt")], cwd=APP_ROOT, timeout=600)
        log.info("admin update: restarting (%s -> %s)", before[:7], after[:7])
        time.sleep(1.5)
        os._exit(0)

    if not os.path.isdir(os.path.join(APP_ROOT, ".git")):
        return JSONResponse({"error": "not a git checkout"}, status_code=400)
    threading.Thread(target=run, daemon=True).start()
    return JSONResponse({"ok": True, "message": "Updating and restarting; give it ~20 seconds."})


# ---------- Checklist ----------

async def checklist_html(request: Request):
    set_num = request.path_params["set_num"]
    s = db.get_set(set_num)
    if not s:
        return RedirectResponse("/", status_code=303)
    mode = request.query_params.get("mode", "trip")
    ctx = build_checklist(s, *db.set_items(set_num), mode=mode, order=request.query_params.get("order", "color"))
    return HTMLResponse(render_checklist_html(ctx))


async def checklist_pdf(request: Request):
    set_num = request.path_params["set_num"]
    s = db.get_set(set_num)
    if not s:
        return RedirectResponse("/", status_code=303)
    mode = request.query_params.get("mode", "trip")
    ctx = build_checklist(s, *db.set_items(set_num), mode=mode, order=request.query_params.get("order", "color"))
    try:
        pdf = await run_in_threadpool(render_checklist_pdf, ctx)
    except Exception as e:
        return JSONResponse({"error": f"PDF rendering failed: {e}"}, status_code=500)
    fname = f"{set_num}-{mode}-checklist-{time.strftime('%Y%m%d')}.pdf"
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{fname}"'})


routes = [
    Route("/", home),
    Route("/login", login, methods=["GET", "POST"]),
    Route("/health", health),
    Route("/sw.js", sw),
    Route("/manifest.webmanifest", manifest),
    Route("/s/{set_num}", set_page),
    Route("/v/{keys}", view_page),
    Route("/v/{keys}/checklist", view_checklist_html),
    Route("/v/{keys}/checklist.pdf", view_checklist_pdf),
    Route("/admin", admin_page),
    Route("/api/views/{keys}", api_view),
    Route("/api/views/{keys}/progress", api_view_progress, methods=["GET", "POST"]),
    Route("/api/views/{keys}/settings", api_view_settings, methods=["POST"]),
    Route("/api/views/{keys}/reset", api_view_reset, methods=["POST"]),
    Route("/api/views/{keys}/finish_trip", api_view_finish_trip, methods=["POST"]),
    Route("/api/views/{keys}/prices", api_view_prices, methods=["GET", "POST"]),
    Route("/api/views/{keys}/buyplan", api_view_buyplan),
    Route("/api/brickowl/wishlists", api_bo_wishlists),
    Route("/api/brickowl/import", api_bo_import, methods=["POST"]),
    Route("/api/jobs/{name}", api_job),
    Route("/api/admin/diagnostics", api_admin_diagnostics),
    Route("/api/admin/logs", api_admin_logs),
    Route("/api/admin/update", api_admin_update, methods=["POST"]),
    Route("/api/admin/discover_lego", api_admin_discover, methods=["POST"]),
    Route("/s/{set_num}/checklist", checklist_html),
    Route("/s/{set_num}/checklist.pdf", checklist_pdf),
    Route("/api/sets", api_sets, methods=["GET", "POST"]),
    Route("/api/sets/{set_num}", api_set, methods=["GET", "DELETE"]),
    Route("/api/sets/{set_num}/progress", api_progress, methods=["GET", "POST"]),
    Route("/api/sets/{set_num}/settings", api_settings, methods=["POST"]),
    Route("/api/sets/{set_num}/reset", api_reset, methods=["POST"]),
    Route("/api/sets/{set_num}/finish_trip", api_finish_trip, methods=["POST"]),
    Route("/api/sets/{set_num}/prices", api_prices, methods=["GET", "POST"]),
    Route("/api/sets/{set_num}/buyplan", api_buyplan),
    Mount("/static", StaticFiles(directory="app/static"), name="static"),
]

@contextlib.asynccontextmanager
async def lifespan(app):
    db.init()
    yield


app = Starlette(routes=routes, middleware=[Middleware(AuthMiddleware)], lifespan=lifespan)

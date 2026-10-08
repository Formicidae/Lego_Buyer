import contextlib
import hashlib
import hmac
import json
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

from . import db, jobs, lego, rebrickable
from .checklist import build_checklist, render_checklist_html, render_checklist_pdf
from .config import APP_PASSCODE, SECRET, BRICKOWL_API_KEY, REBRICKABLE_API_KEY

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
    return templates.TemplateResponse(request, "index.html", {"set_num": None, "sets": db.list_sets()})


async def set_page(request: Request):
    set_num = request.path_params["set_num"]
    if not db.get_set(set_num):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "index.html", {"set_num": set_num, "sets": db.list_sets()})


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
        try:
            set_num = rebrickable.normalize_set_num(body.get("set_num", ""))
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


async def api_buyplan(request: Request):
    """What's still needed after owned + found, priced against LEGO with the US fee rules applied."""
    set_num = request.path_params["set_num"]
    s = db.get_set(set_num)
    if not s:
        return JSONResponse({"error": "not found"}, status_code=404)
    parts, figs = db.set_items(set_num)
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
    return JSONResponse({
        "lego": plan,
        "lego_lots": len(lego_items),
        "not_sold_by_lego": [i for i in items if i["checked"] and not i["sold_by_lego"]],
        "unpriced": [i for i in items if not i["checked"]],
        "job": jobs.status(f"prices:{set_num}"),
    })


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

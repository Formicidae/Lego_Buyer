"""Printable checklist: the same part cards rendered with print CSS, then turned into a PDF with Chromium."""
import os
import subprocess
import tempfile
import time

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import CHROMIUM_PATH

_env = Environment(loader=FileSystemLoader("app/templates"), autoescape=select_autoescape(["html"]))


def remaining_for(item, count_alt: bool) -> int:
    found = item["owned"] + item["found_exact"] + (item["found_alt"] if count_alt else 0)
    return max(0, item["quantity"] - found)


def build_checklist(set_info, parts, minifigs, mode="trip", order="color"):
    """mode='trip': only what's still missing, grouped by color for walking a Pick-a-Brick wall.
    mode='have': every part in the set with a blank to write how many you own."""
    count_alt = bool(set_info.get("count_alt", 1))
    items = []
    for p in parts:
        if p["is_spare"] and not set_info.get("include_spares"):
            continue
        rem = remaining_for(p, count_alt)
        if mode == "trip" and rem <= 0:
            continue
        items.append({**p, "remaining": rem, "kind": "part"})
    figs = []
    if set_info.get("include_minifigs"):
        for f in minifigs:
            rem = remaining_for(f, count_alt)
            if mode == "trip" and rem <= 0:
                continue
            figs.append({**f, "remaining": rem, "kind": "minifig"})

    for p in items:
        p["lego_value"] = (p.get("lego_price") or 0) * (p["remaining"] if mode == "trip" else p["quantity"])
    if order == "price":
        # Big-ticket parts first, as one flat list.
        items.sort(key=lambda p: (-(p.get("lego_price") or 0), -p["lego_value"], p["color_name"], p["part_name"]))
        groups = [{"color_name": "Highest LEGO price first", "color_rgb": None, "items": items}] if items else []
    elif mode == "trip":
        items.sort(key=lambda p: (p["color_name"], p.get("cat_name") or "", p["part_name"]))
        groups = []
        for p in items:
            if not groups or groups[-1]["color_name"] != p["color_name"]:
                groups.append({"color_name": p["color_name"], "color_rgb": p["color_rgb"], "items": []})
            groups[-1]["items"].append(p)
    else:
        items.sort(key=lambda p: (p.get("cat_name") or "", p["part_name"], p["color_name"]))
        groups = []
        for p in items:
            cat = p.get("cat_name") or "Other"
            if not groups or groups[-1]["color_name"] != cat:
                groups.append({"color_name": cat, "color_rgb": None, "items": []})
            groups[-1]["items"].append(p)

    return {
        "set": set_info,
        "mode": mode,
        "groups": groups,
        "minifigs": figs,
        "total_lots": len(items),
        "total_pieces": sum(p["remaining"] for p in items),
        "generated": time.strftime("%b %d, %Y %I:%M %p"),
        "lego_value": round(sum(p["lego_value"] for p in items), 2),
        "priced": sum(1 for p in items if p.get("lego_price") is not None),
    }


def render_checklist_html(ctx) -> str:
    return _env.get_template("checklist.html").render(**ctx)


def render_checklist_pdf(ctx) -> bytes:
    html = render_checklist_html(ctx)
    if CHROMIUM_PATH:
        return _pdf_via_chromium_cli(html)
    return _pdf_via_playwright(html, ctx)


def _pdf_via_chromium_cli(html: str) -> bytes:
    """Headless Chromium from the command line: light enough for a 1 GB Raspberry Pi."""
    with tempfile.TemporaryDirectory() as td:
        src, out = os.path.join(td, "checklist.html"), os.path.join(td, "checklist.pdf")
        with open(src, "w", encoding="utf-8") as f:
            f.write(html)
        cmd = [
            CHROMIUM_PATH, "--headless=new", "--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage",
            "--hide-scrollbars", "--no-pdf-header-footer", "--run-all-compositor-stages-before-draw",
            "--virtual-time-budget=20000",  # lets part images finish loading before printing
            f"--print-to-pdf={out}", f"--user-data-dir={os.path.join(td, 'profile')}", f"file://{src}",
        ]
        subprocess.run(cmd, check=True, timeout=180, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with open(out, "rb") as f:
            return f.read()


def _pdf_via_playwright(html: str, ctx) -> bytes:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        try:
            page = browser.new_page()
            page.set_content(html, wait_until="load")
            try:
                # Wait for part images (fetched from Rebrickable's CDN); don't let a slow one block the PDF forever.
                page.wait_for_load_state("networkidle", timeout=45000)
            except Exception:
                pass
            page.wait_for_timeout(300)
            return page.pdf(
                format="Letter",
                landscape=True,
                prefer_css_page_size=True,
                print_background=True,
                margin={"top": "0.35in", "bottom": "0.45in", "left": "0.4in", "right": "0.4in"},
                display_header_footer=True,
                header_template="<div></div>",
                footer_template=(
                    "<div style='width:100%;font-size:8px;font-family:sans-serif;color:#777;"
                    "padding:0 0.4in;display:flex;justify-content:space-between'>"
                    f"<span>{ctx['set']['set_num']} · {ctx['set']['name']}</span>"
                    "<span>Page <span class='pageNumber'></span> / <span class='totalPages'></span></span></div>"
                ),
            )
        finally:
            browser.close()

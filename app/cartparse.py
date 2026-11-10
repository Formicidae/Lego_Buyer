"""Parse BrickOwl's "Buy Wishlist" page into stores: from the saved page (.mht / .html, which also gives
every allocated lot with its price), or from text copied off the page / its printed PDF (store totals only)."""
import email
import html as htmlmod
import io
import re
from email import policy

TOTAL_RE = re.compile(r"Total:\s*\$(\d+\.\d{2})")
UNALLOC_RE = re.compile(r"Unallocated\s*\((\d+)\s*Items?\s*in\s*(\d+)\s*Lots?\)", re.I)
WISHLIST_RE = re.compile(r"wishlist=(\d+)")


def pdf_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise ValueError("PDF reading needs the pypdf package (pip install pypdf)") from e
    reader = PdfReader(io.BytesIO(data))
    return "\n".join((pg.extract_text() or "") for pg in reader.pages)


# Figures for a selected store, in either column order the text extractors produce.
FIG_A = re.compile(r"(?P<needed>\d+)\s+(?P<alloc>\d+)\s*/\s*(?P<items>\d+)\s*(?:\(\d+\)\s*)?\$(?P<total>\d+\.\d{2})\s*(?:\(\d+\)\s*)?\(Incl\.\s*\$(?P<ship>\d+\.\d{2})\)")
FIG_B = re.compile(r"\$(?P<total>\d+\.\d{2})\s+(?P<needed>\d+)\s+(?P<alloc>\d+)\s*/\s*(?P<items>\d+)\s*(?:\((?P<fb>\d+)\)\s*)?\(Incl\.\s*\$(?P<ship>\d+\.\d{2})\)")
LEAD_NAME = re.compile(r"([A-Za-z][A-Za-z0-9&'’.,!\- ]{1,70}?)\s*(?:\((?P<fb>\d+)\))?\s*$")
NAME_RE = re.compile(r"([A-Za-z][A-Za-z0-9&'’.,!\- ]{1,70}?)\s*\((\d{1,6})\)")
NOT_NAMES = {"unallocated", "lot", "lots", "item", "items", "including", "incl", "total", "store", "stores", "needed", "allocated"}


def _clean_name(n):
    n = re.split(r"minimum order|Show More|Hide More|limited to \d+\.|shipping\)", n)[-1]
    n = re.sub(r"^(?:\d+\.\d{2}\s*|x\s*|\d+\s+)+", "", n)  # price / count fragments from the item grid
    n = re.sub(r"^(?:Store|Lots|Items|Needed|Allocated|Total|\(Including)\s*", "", n)
    return n.strip(" -,")


def parse_buy_wishlist(text: str):
    flat = re.sub(r"\s+", " ", text)
    names = []
    for m in NAME_RE.finditer(flat):
        n = _clean_name(m.group(1))
        if n and n.lower() not in NOT_NAMES and not n.replace(" ", "").isdigit() and "$" not in n:
            names.append((m.start(), n, m.group(2)))
    figs = sorted([m for m in FIG_A.finditer(flat)] + [m for m in FIG_B.finditer(flat)], key=lambda m: m.start())
    stores = []
    col_rows = [m for m in figs if "fb" not in m.groupdict()]
    # Column-order text (PDF extractors) lists selected stores first, in the same order as their figure rows.
    col_names = iter(names)
    for m in figs:
        n = fb = None
        if "fb" in m.groupdict():  # layout order: the name sits right before the $total
            lead = LEAD_NAME.search(flat[max(0, m.start() - 90):m.start()])
            if lead:
                cand = _clean_name(lead.group(1))
                if cand and cand.lower() not in NOT_NAMES and not cand.replace(" ", "").isdigit():
                    n, fb = cand, (lead.group("fb") or m.group("fb"))
        if not n:
            try:
                _, n, fb = next(col_names)
            except StopIteration:
                continue
        total, ship = float(m.group("total")), float(m.group("ship"))
        stores.append({"name": f"{n} ({fb})" if fb else n, "subtotal": round(total - ship, 2), "shipping": ship, "total": total,
                       "needed_lots": int(m.group("needed")), "lots": int(m.group("alloc")), "items": int(m.group("items"))})
    return _finish(flat, stores)


def _finish(flat, stores):
    totals = TOTAL_RE.findall(flat)
    grand = float(totals[-1]) if totals else round(sum(s["total"] for s in stores), 2)
    un = UNALLOC_RE.search(flat)
    wl = WISHLIST_RE.search(flat)
    return {
        "stores": stores, "total": grand, "lots_covered": sum(s["lots"] for s in stores),
        "lots_missing": int(un.group(2)) if un else 0, "items_missing": int(un.group(1)) if un else 0,
        "url": f"https://www.brickowl.com/wishlist/magic?wishlist={wl.group(1)}" if wl else None,
    }


# ---------- saved page (.mht / .html): stores plus every allocated lot ----------

def is_page(data: bytes, filename: str = "") -> bool:
    head = data[:4096].lower()
    return filename.lower().endswith((".mht", ".mhtml", ".html", ".htm")) or b"<html" in head or b"mime-version:" in head or b"<!doctype html" in head


def page_html(data: bytes) -> str:
    """The page's HTML from a saved .mht (MIME multipart/related) or a plain .html file."""
    head = data[:4096].lower()
    if b"mime-version:" in head or b"content-type: multipart/related" in head:
        msg = email.message_from_bytes(data, policy=policy.default)
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                return part.get_content()
        raise ValueError("No HTML in this .mht file")
    return data.decode("utf-8", "replace")


STORE_RE = re.compile(r'<a href="(https://[^"]+\.brickowl\.com/)" target="_blank">([^<]+)</a>\s*<a href="[^"]*">\((\d+)\)</a>')
FIGS_RE = re.compile(r'<td class="a-center">(\d+)</td><td class="a-center">(\d+)\s*/\s*(\d+)</td><td class="a-center"><span class="price">\$(\d+\.\d{2})</span><div class="f08">\(Incl\. <span class="price">\$(\d+\.\d{2})')
LOT_RE = re.compile(r'title="([^"]+)" target="_blank" href="([^"]+)">.*?(\d+) x\s*<span class="regular-price">\s*<span class="price">\$(\d+\.\d{2,})', re.S)
IDS_RE = re.compile(r"\s*\(([\d\s/a-z]+)\)\s*$")


def parse_lot_title(title: str):
    """'LEGO Dark Stone Gray Plate 2 x 4 (3020)' -> ('Dark Stone Gray Plate 2 x 4', ['3020']).
    The colour is left in the name: the caller matches it against the BrickOwl colour names it knows."""
    name = htmlmod.unescape(title).strip()
    name = re.sub(r"^LEGO\s+", "", name)
    ids = []
    m = IDS_RE.search(name)
    if m:
        ids = [t.strip() for t in m.group(1).split("/") if t.strip()]
        name = name[:m.start()].strip()
    return name, ids


def parse_buy_wishlist_page(data: bytes):
    h = page_html(data)
    stores = []
    for blk in re.split(r'<tr class="(?:odd|even)">', h)[1:]:
        sm = STORE_RE.search(blk)
        fm = FIGS_RE.search(blk)
        if not sm or not fm:
            continue
        lots = []
        for lm in LOT_RE.finditer(blk):
            name, ids = parse_lot_title(lm.group(1))
            lots.append({"name": name, "ids": ids, "qty": int(lm.group(3)), "price": float(lm.group(4)), "url": htmlmod.unescape(lm.group(2))})
        total, ship = float(fm.group(4)), float(fm.group(5))
        stores.append({"name": f"{htmlmod.unescape(sm.group(2))} ({sm.group(3)})", "url": sm.group(1), "subtotal": round(total - ship, 2),
                       "shipping": ship, "total": total, "needed_lots": int(fm.group(1)), "lots": int(fm.group(2)), "items": int(fm.group(3)),
                       "lot_list": lots})
    flat = re.sub(r"<[^>]+>", " ", re.sub(r"<script.*?</script>|<style.*?</style>", "", h, flags=re.S))
    out = _finish(re.sub(r"\s+", " ", htmlmod.unescape(flat)), stores)
    wl = WISHLIST_RE.search(h)
    if wl:
        out["url"] = f"https://www.brickowl.com/wishlist/magic?wishlist={wl.group(1)}"
    return out

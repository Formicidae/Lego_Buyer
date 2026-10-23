"""Parse BrickOwl's "Buy Wishlist" page (text copied from the page, or its printed PDF) into stores."""
import io
import re

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

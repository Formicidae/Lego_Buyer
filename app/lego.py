"""LEGO Pick a Brick prices.

Primary source: the GraphQL search the Pick a Brick page itself uses. It returns price, stock and the
delivery channel, which is the Bestseller ("pab", ships from the US) vs Standard ("bap", ships from
Denmark) split that decides the service-fee minimums.

Fallback: the element product page, which has price and availability in plain HTML but no tier.
"""
import json
import re
import time
from pathlib import Path

import requests

from .config import DATA_DIR

GRAPHQL_URL = "https://www.lego.com/api/graphql/PickABrickQuery"
ELEMENT_PAGE = "https://www.lego.com/en-us/product/element-{}"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36",
    "Accept": "application/json",
    "Content-Type": "application/json",
    "x-locale": "en-US",
    "Origin": "https://www.lego.com",
    "Referer": "https://www.lego.com/en-us/pick-and-build/pick-a-brick",
}

# Last-resort guess, used only if discovery fails entirely.
PAB_QUERY = """
query PickABrickQuery($query: String, $page: Int, $perPage: Int, $sort: SortInput, $includeOutOfStock: Boolean, $filters: [Filter!]) {
  elements(query: $query, page: $page, perPage: $perPage, sort: $sort, includeOutOfStock: $includeOutOfStock, filters: $filters) {
    count
    total
    results {
      ...ElementLeafData
    }
  }
}
fragment ElementLeafData on Element {
  id
  name
  primaryImage(size: 1)
  inStock
  ... on SingleVariantElement {
    variant { ...ElementLeafVariant }
  }
  ... on MultiVariantElement {
    variants { ...ElementLeafVariant }
  }
}
fragment ElementLeafVariant on ElementVariant {
  id
  price { formattedAmount centAmount currencyCode }
  attributes { designNumber colourId deliveryChannel maxOrderQuantity }
}
"""

TIER_NAMES = {"pab": "Bestseller", "bap": "Standard", "bestseller": "Bestseller", "standard": "Standard"}

class LegoError(Exception):
    pass


# ---------- Query discovery ----------
# LEGO doesn't publish its GraphQL schema, and the field names change. The Pick a Brick page's own
# JavaScript contains the exact query it sends, so we pull it from there and cache it on disk.

QUERY_CACHE = Path(DATA_DIR) / "pab_query.graphql"
PAB_PAGE = "https://www.lego.com/en-us/pick-and-build/pick-a-brick"


class QueryNotFound(LegoError):
    pass


def _extract_js_string(js: str, idx: int) -> str:
    """Return the JS string literal that contains position idx (handles ", ', and ` quoting)."""
    i = idx
    while i > 0:
        c = js[i]
        if c in "\"'`" and js[i - 1] != "\\":
            quote = c
            break
        i -= 1
    else:
        raise QueryNotFound("couldn't find start of string literal")
    j = idx
    while j < len(js) - 1:
        j += 1
        if js[j] == quote and js[j - 1] != "\\":
            break
    raw = js[i + 1:j]
    if quote == "`":
        return raw
    try:
        return json.loads('"' + raw.replace('"', '\\"') + '"') if quote == "'" else json.loads('"' + raw + '"')
    except json.JSONDecodeError:
        return raw.encode().decode("unicode_escape")


def discover_query(force=False) -> str:
    if not force and QUERY_CACHE.exists():
        return QUERY_CACHE.read_text()
    h = {**HEADERS, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8", "Accept-Language": "en-US,en;q=0.9"}
    r = _session.get(PAB_PAGE, headers=h, timeout=30)
    html = r.text
    # Script URLs appear as src="..." attributes and inside Next.js manifests; accept both relative and absolute.
    srcs = re.findall(r'(?:src|href)="((?:https?://[^"]+)?/_next/static/[^"]+\.js)"', html)
    srcs += re.findall(r'"((?:https?://[^"]+)?/_next/static/chunks/[^"]+\.js)"', html)
    seen, ordered = set(), []
    for u in srcs:
        u = u if u.startswith("http") else "https://www.lego.com" + u
        if u not in seen:
            seen.add(u)
            ordered.append(u)
    # Page-specific chunks are the likeliest home of the query; check them first.
    ordered.sort(key=lambda u: 0 if "pick" in u.lower() else 1)
    srcs = ordered
    if not srcs:
        snippet = re.sub(r"\s+", " ", html[:300])
        raise QueryNotFound(f"Pick a Brick page had no scripts (HTTP {r.status_code}, {len(html)} chars): {snippet}")
    for u in srcs:
        try:
            js = _session.get(u, headers=h, timeout=30).text
        except requests.RequestException:
            continue
        for m in re.finditer(r"query PickABrickQuery", js):
            try:
                q = _extract_js_string(js, m.start())
            except QueryNotFound:
                continue
            if "elements(" in q or "elements (" in q:
                # Pull in fragments if they live in separate string literals.
                for frag in set(re.findall(r"\.\.\.(\w+)", q)):
                    if f"fragment {frag}" in q:
                        continue
                    fm = re.search(rf"fragment {frag}\b", js)
                    if fm:
                        try:
                            q += "\n" + _extract_js_string(js, fm.start())
                        except QueryNotFound:
                            pass
                QUERY_CACHE.write_text(q)
                return q
    raise QueryNotFound(f"no PickABrickQuery in {len(srcs)} scripts; first few: " + ", ".join(u.rsplit("/", 1)[-1] for u in srcs[:5]))


def query_variables(q: str, element_id: str):
    """Fill the variables the discovered query declares with what we know."""
    decl = re.search(r"query PickABrickQuery\s*\(([^)]*)\)", q)
    names = re.findall(r"\$(\w+)\s*:\s*([^,)]+)", decl.group(1)) if decl else []
    known = {"query": str(element_id), "page": 1, "perPage": 20, "includeOutOfStock": True, "filters": [], "sort": None}
    variables = {}
    for name, typ in names:
        if name in known and known[name] is not None:
            variables[name] = known[name]
        elif typ.strip().endswith("!"):
            raise QueryNotFound(f"query needs unknown required variable ${name}: {typ.strip()}")
    return variables


def _walk(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def _find_in(obj, key):
    for d in _walk(obj):
        if key in d and d[key] not in (None, ""):
            return d[key]
    return None


def _parse_price(obj):
    p = _find_in(obj, "price")
    if isinstance(p, dict):
        if p.get("centAmount") is not None:
            return p["centAmount"] / 100.0
        return _parse_money(p.get("formattedAmount") or p.get("formattedValue") or p.get("value"))
    if isinstance(p, (int, float)):
        return float(p)
    if isinstance(p, str):
        return _parse_money(p)
    return None

# US fee rules (lego.com help, 2026): per-category service fee waived at $14; shipping free at $35.
US_FEES = {"category_min": 14.00, "service_fee": 7.00, "free_ship_min": 35.00, "ship_under_25": 4.95, "ship_25_to_35": 6.95}


_session = requests.Session()


def graphql_lookup(element_id: str, timeout=20):
    """Returns {"price", "tier", "available", "limit", "source": "graphql"} or None if not found."""
    q = discover_query()
    body = {"operationName": "PickABrickQuery", "variables": query_variables(q, element_id), "query": q}
    r = _session.post(GRAPHQL_URL, headers=HEADERS, data=json.dumps(body), timeout=timeout)
    if r.status_code == 400 and QUERY_CACHE.exists():
        # The cached query may be stale; re-discover once.
        q = discover_query(force=True)
        body["query"], body["variables"] = q, query_variables(q, element_id)
        r = _session.post(GRAPHQL_URL, headers=HEADERS, data=json.dumps(body), timeout=timeout)
    if r.status_code != 200:
        raise LegoError(f"GraphQL HTTP {r.status_code}: {r.text[:200]}")
    data = r.json()
    if data.get("errors"):
        raise LegoError("GraphQL errors: " + json.dumps(data["errors"])[:300])
    # Field names vary; find the variant/element whose id matches and read what we need around it.
    for d in _walk(data.get("data") or {}):
        if str(d.get("id") or d.get("elementId") or d.get("variantId") or "") == str(element_id) and ("price" in d or "attributes" in d or "variant" in d):
            price = _parse_price(d)
            ch = _find_in(d, "deliveryChannel")
            ch = (ch or "").lower() if isinstance(ch, str) else None
            stock = _find_in(d, "inStock")
            if stock is None:  # stock usually sits on the element, one level above the variant
                for e in _walk(data.get("data") or {}):
                    if "inStock" in e and any(x is d for x in _walk(e)):
                        stock = e["inStock"]
                        break
            lim = _find_in(d, "maxOrderQuantity") or _find_in(d, "maxQuantity")
            if price is None:
                continue
            return {"price": price, "tier": TIER_NAMES.get(ch, ch.title() if ch else None), "available": bool(stock) if stock is not None else True,
                    "limit": int(lim) if isinstance(lim, (int, float, str)) and str(lim).isdigit() else None, "source": "graphql"}
    return None


_PRICE_RE = re.compile(r'"price"\s*:\s*"?\$?\s*([0-9]+\.[0-9]{2})')
_AVAIL_RE = re.compile(r"Available now|in stock|Out of stock|Sold out|Coming soon|Temporarily out of stock", re.I)
_LIMIT_RE = re.compile(r"Limit\s+(\d+)")


def page_lookup(element_id: str, timeout=20):
    """Scrape the element product page. No tier information."""
    r = _session.get(ELEMENT_PAGE.format(element_id), headers={**HEADERS, "Accept": "text/html"}, timeout=timeout)
    if r.status_code == 404:
        return None
    if r.status_code != 200:
        raise LegoError(f"Element page HTTP {r.status_code}")
    html = r.text
    m = _PRICE_RE.search(html) or re.search(r"\$\s*([0-9]+\.[0-9]{2})", html)
    if not m:
        return None
    avail = _AVAIL_RE.search(html)
    lim = _LIMIT_RE.search(html)
    return {
        "price": float(m.group(1)),
        "tier": None,
        "available": bool(avail and not re.search(r"out of stock|sold out", avail.group(0), re.I)),
        "limit": int(lim.group(1)) if lim else None,
        "source": "page",
    }


def lookup(element_id: str):
    """Try GraphQL, then the page. Returns a dict, None (not sold by LEGO), or raises LegoError."""
    err = None
    try:
        res = graphql_lookup(element_id)
        if res:
            return res
    except (LegoError, requests.RequestException, ValueError) as e:
        err = e
    try:
        # The page is authoritative when it answers: a 404 there means LEGO doesn't sell the element.
        return page_lookup(element_id)
    except (LegoError, requests.RequestException) as e:
        raise LegoError(f"{e}" + (f" (GraphQL: {err})" if err else ""))


def _parse_money(s):
    if not s:
        return None
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)", s.replace(",", ""))
    return float(m.group(1)) if m else None


def price_many(element_ids, on_progress=None, delay=0.6):
    """Look up many elements politely. Yields (element_id, result_or_None, error_or_None)."""
    for i, eid in enumerate(element_ids):
        try:
            res = lookup(eid)
            yield eid, res, None
        except LegoError as e:
            yield eid, None, str(e)
        if on_progress:
            on_progress(i + 1, len(element_ids))
        time.sleep(delay)


def plan_fees(items, fees=US_FEES):
    """Given items with price, tier, qty -> per-tier subtotals and the fees you'd pay as-is.
    Returns a dict the UI shows as progress bars toward each threshold."""
    tiers = {}
    for it in items:
        t = it.get("tier") or "Unknown"
        tiers.setdefault(t, {"subtotal": 0.0, "lots": 0, "pieces": 0})
        tiers[t]["subtotal"] += (it.get("price") or 0) * it.get("qty", 0)
        tiers[t]["lots"] += 1
        tiers[t]["pieces"] += it.get("qty", 0)
    total = sum(t["subtotal"] for t in tiers.values())
    service = 0.0
    for name, t in tiers.items():
        if name in ("Bestseller", "Standard"):
            t["min"] = fees["category_min"]
            t["fee"] = 0.0 if t["subtotal"] >= fees["category_min"] else fees["service_fee"]
            t["short"] = max(0.0, fees["category_min"] - t["subtotal"])
            service += t["fee"]
    if total >= fees["free_ship_min"]:
        shipping = 0.0
    elif total > 25:
        shipping = fees["ship_25_to_35"]
    else:
        shipping = fees["ship_under_25"]
    return {
        "tiers": tiers, "subtotal": round(total, 2), "service_fees": round(service, 2), "shipping": round(shipping, 2),
        "total": round(total + service + shipping, 2), "free_ship_min": fees["free_ship_min"],
        "free_ship_short": round(max(0.0, fees["free_ship_min"] - total), 2),
    }

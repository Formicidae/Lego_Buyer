"""LEGO Pick a Brick prices.

Primary source: the GraphQL search the Pick a Brick page itself uses. It returns price, stock and the
delivery channel, which is the Bestseller ("pab", ships from the US) vs Standard ("bap", ships from
Denmark) split that decides the service-fee minimums.

Fallback: the element product page, which has price and availability in plain HTML but no tier.
"""
import json
import re
import time

import requests

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

TIER_NAMES = {"pab": "Bestseller", "bap": "Standard"}

# US fee rules (lego.com help, 2026): per-category service fee waived at $14; shipping free at $35.
US_FEES = {"category_min": 14.00, "service_fee": 7.00, "free_ship_min": 35.00, "ship_under_25": 4.95, "ship_25_to_35": 6.95}


class LegoError(Exception):
    pass


_session = requests.Session()


def graphql_lookup(element_id: str, timeout=20):
    """Returns {"price", "tier", "available", "limit", "source": "graphql"} or None if not found."""
    body = {
        "operationName": "PickABrickQuery",
        "variables": {"query": str(element_id), "page": 1, "perPage": 20, "includeOutOfStock": True, "filters": []},
        "query": PAB_QUERY,
    }
    r = _session.post(GRAPHQL_URL, headers=HEADERS, data=json.dumps(body), timeout=timeout)
    if r.status_code != 200:
        raise LegoError(f"GraphQL HTTP {r.status_code}: {r.text[:200]}")
    data = r.json()
    if data.get("errors"):
        raise LegoError("GraphQL errors: " + json.dumps(data["errors"])[:300])
    results = (((data.get("data") or {}).get("elements") or {}).get("results")) or []
    for el in results:
        variants = []
        if el.get("variant"):
            variants.append(el["variant"])
        variants.extend(el.get("variants") or [])
        for v in variants:
            if str(v.get("id")) != str(element_id):
                continue
            price = v.get("price") or {}
            cents = price.get("centAmount")
            amount = cents / 100.0 if cents is not None else _parse_money(price.get("formattedAmount"))
            attrs = v.get("attributes") or {}
            ch = (attrs.get("deliveryChannel") or "").lower()
            return {
                "price": amount,
                "tier": TIER_NAMES.get(ch, ch or None),
                "available": bool(el.get("inStock", True)),
                "limit": attrs.get("maxOrderQuantity"),
                "source": "graphql",
            }
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
        res = page_lookup(element_id)
        if res or err is None:
            return res
    except (LegoError, requests.RequestException) as e:
        err = err or e
    raise LegoError(str(err))


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

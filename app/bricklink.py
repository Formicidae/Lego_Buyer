"""BrickLink store API: the Price Guide, used to compare marketplace prices with LEGO's.

Needs the four credentials from bricklink.com/v3/api.page (free with any BrickLink account; the
access token is tied to the public IP the Pi uses). OAuth 1.0 HMAC-SHA1 is signed here by hand so
the Pi needs no extra packages.
"""
import base64
import hashlib
import hmac
import os
import secrets
import time
import urllib.parse

import requests

BL = {
    "consumer_key": os.environ.get("BRICKLINK_CONSUMER_KEY", ""),
    "consumer_secret": os.environ.get("BRICKLINK_CONSUMER_SECRET", ""),
    "token": os.environ.get("BRICKLINK_TOKEN", ""),
    "token_secret": os.environ.get("BRICKLINK_TOKEN_SECRET", ""),
}
BASE = "https://api.bricklink.com/api/store/v1"


class BrickLinkError(Exception):
    pass


def configured():
    return all(BL.values())


def _pct(s):
    return urllib.parse.quote(str(s), safe="~")


def _auth_header(method, url, params):
    oauth = {
        "oauth_consumer_key": BL["consumer_key"],
        "oauth_token": BL["token"],
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_nonce": secrets.token_hex(8),
        "oauth_version": "1.0",
    }
    allp = {**params, **oauth}
    norm = "&".join(f"{_pct(k)}={_pct(allp[k])}" for k in sorted(allp))
    base = f"{method.upper()}&{_pct(url)}&{_pct(norm)}"
    key = f"{_pct(BL['consumer_secret'])}&{_pct(BL['token_secret'])}"
    sig = base64.b64encode(hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()).decode()
    oauth["oauth_signature"] = sig
    return "OAuth " + ", ".join(f'{k}="{_pct(v)}"' for k, v in oauth.items())


def _get(path, **params):
    if not configured():
        raise BrickLinkError("BrickLink API credentials are not set (BRICKLINK_* in /etc/lego-buyer.env).")
    url = BASE + path
    params = {k: v for k, v in params.items() if v is not None}
    r = requests.get(url, params=params, headers={"Authorization": _auth_header("GET", url, params)}, timeout=30)
    data = r.json() if r.content else {}
    meta = data.get("meta") or {}
    if r.status_code != 200 or meta.get("code", 200) >= 300:
        raise BrickLinkError(f"BrickLink {meta.get('code', r.status_code)}: {meta.get('description') or meta.get('message') or r.text[:200]}")
    return data.get("data")


def price_guide(item_type, item_no, color_id=None, new_or_used="N", guide_type="stock", region="north_america"):
    """Current listings (guide_type=stock) or last-6-months sales (guide_type=sold)."""
    d = _get(f"/items/{item_type}/{item_no}/price", color_id=color_id, new_or_used=new_or_used, guide_type=guide_type,
             region=region, currency_code="USD")
    return {
        "avg": float(d.get("avg_price") or 0) or None,
        "qty_avg": float(d.get("qty_avg_price") or 0) or None,
        "min": float(d.get("min_price") or 0) or None,
        "max": float(d.get("max_price") or 0) or None,
        "qty": int(d.get("total_quantity") or 0),
        "lots": int(d.get("unit_quantity") or 0),
    }


def ids_for(part):
    """(BrickLink part no, BrickLink color id) from a part row's Rebrickable external ids, or (None, None)."""
    ext = part.get("external_ids") or {}
    bl_no = ((ext.get("part") or {}).get("BrickLink") or [None])[0] or part.get("part_num")
    bl_col = (((ext.get("color") or {}).get("BrickLink") or {}).get("ext_ids") or [None])[0]
    return bl_no, bl_col

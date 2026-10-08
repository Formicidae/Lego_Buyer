#!/usr/bin/env python3
"""Check that lego.com price lookups work from this machine, and show the raw response shapes.

    cd /opt/lego-buyer && .venv/bin/python deploy/probe-lego.py 6204668 302434

Paste the output back to Claude if anything looks off.
"""
import json
import sys

sys.path.insert(0, ".")
from app import lego  # noqa: E402

if sys.argv[1:2] == ["--find-query"]:
    # Pull the real PickABrickQuery text out of lego.com's JavaScript bundles.
    import re
    import requests
    H = {**lego.HEADERS, "Accept": "text/html,*/*"}
    html = requests.get("https://www.lego.com/en-us/pick-and-build/pick-a-brick", headers=H, timeout=30).text
    srcs = re.findall(r'src="([^"]+/_next/static/[^"]+\.js)"', html)
    srcs = [u if u.startswith("http") else "https://www.lego.com" + u for u in srcs]
    print(f"page ok, {len(srcs)} scripts")
    found = False
    for u in srcs:
        try:
            js = requests.get(u, headers=H, timeout=30).text
        except Exception as e:
            print("skip", u, e); continue
        for m in re.finditer(r"PickABrickQuery", js):
            i = m.start()
            chunk = js[max(0, i - 200): i + 7000]
            if "elements(" in chunk or "fragment" in chunk:
                print("\n===== found in", u, "=====")
                print(chunk)
                found = True
                break
        if found:
            break
    if not found:
        print("No query found in page scripts. Scripts were:")
        for u in srcs: print(" ", u)
    sys.exit(0)

ids = sys.argv[1:] or ["6204668", "302434", "6073026"]
for eid in ids:
    print(f"\n===== element {eid} =====")
    try:
        body = {"operationName": "PickABrickQuery", "variables": {"query": eid, "page": 1, "perPage": 5, "includeOutOfStock": True, "filters": []}, "query": lego.PAB_QUERY}
        r = lego._session.post(lego.GRAPHQL_URL, headers=lego.HEADERS, data=json.dumps(body), timeout=20)
        print("GraphQL HTTP", r.status_code)
        txt = r.text
        print(txt[:1500] + ("…" if len(txt) > 1500 else ""))
    except Exception as e:
        print("GraphQL request failed:", e)
    try:
        print("page_lookup ->", lego.page_lookup(eid))
    except Exception as e:
        print("page_lookup failed:", e)
    try:
        print("lookup ->", lego.lookup(eid))
    except Exception as e:
        print("lookup failed:", e)

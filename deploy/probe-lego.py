#!/usr/bin/env python3
"""Check that lego.com price lookups work from this machine, and show the raw response shapes.

    cd /opt/lego-buyer && .venv/bin/python deploy/probe-lego.py 6204668 302434

Paste the output back to Claude if anything looks off.
"""
import json
import sys

sys.path.insert(0, ".")
from app import lego  # noqa: E402

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

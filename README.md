# Lego Buyer

Find the cheapest way to get every piece for a LEGO set: check what you own, hunt the rest at a
Bricks & Minifigs pick-a-brick wall (with a phone-friendly live checklist two people can update at
once), then buy whatever is left.

**Phase 1 (this build):** set loading from Rebrickable with part images, the Have / Trip / Buy
stages, live multi-phone sync, and a printable PDF checklist.
**Next:** LEGO Pick a Brick prices and bestseller/standard tiers, BrickOwl availability, and the
buy-plan optimizer.

## Deploy on Railway

1. **Create the service.** Railway → New Project → *Deploy from GitHub repo* → pick this repo.
   The `Dockerfile` is detected automatically. The first build takes a few minutes (the image
   includes Chromium for PDF rendering).
2. **Variables.** On the service → *Variables*, add:
   | Name | Value |
   |---|---|
   | `REBRICKABLE_API_KEY` | your key from rebrickable.com → Settings → API |
   | `BRICKOWL_API_KEY` | your key from brickowl.com → Settings → API (used in phase 2) |
   | `APP_PASSCODE` | any word; everyone using the site types it once per phone |
3. **Volume.** Service → *Settings* → *Volumes* → Add Volume, mount path **`/data`**.
   This holds the SQLite database so counts survive every redeploy.
4. **Domain.** Service → *Settings* → *Networking* → *Generate Domain*.
5. Open the domain, enter the passcode, type a set number (e.g. `75424`), Load.

`/health` reports whether each key and the passcode are configured.

## Using it

- **Have** — go through your collection and tap *Have* (or the picture) once per piece you own.
  *All* marks a lot complete.
- **Trip** — at the store, tap *Exact* when you find the exact part, *Alt* for an acceptable
  substitute. *Mistake?* reveals the undo links. Every open phone updates within a few seconds and
  shows who tapped last. *Filters → Open checklist PDF* prints what is still missing, grouped by
  color so you can walk the wall.
- **Buy** — what's left after both passes. Copy a BrickLink wanted-list XML or a CSV.
- Spare parts and minifigures are excluded by default; toggle them under *Filters*. Minifigures
  are treated as whole figures, not broken into parts.
- The ↻ button re-fetches the set from Rebrickable (if the inventory was corrected). Counts are
  kept.

## Run locally

```sh
pip install -r requirements.txt && playwright install chromium
cp .env.example .env   # fill in keys
export $(grep -v '^#' .env | xargs) DATA_DIR=./data
uvicorn app.main:app --reload
```

## Layout

```
app/main.py         Starlette app: routes, passcode auth
app/db.py           SQLite schema + queries (sets, parts, minifigs, progress, revisions)
app/rebrickable.py  Rebrickable API client
app/checklist.py    Printable checklist → HTML → PDF (Chromium)
app/templates/      index.html (the app), checklist.html (print), login.html
app/static/         app.js, style.css (no build step)
```

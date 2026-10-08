# Lego Buyer

Find the cheapest way to get every piece for a LEGO set: check what you own, hunt the rest at a
Bricks & Minifigs pick-a-brick wall (with a phone-friendly live checklist two people can update at
once), then buy whatever is left.

**Phase 1 (this build):** set loading from Rebrickable with part images, the Have / Trip / Buy
stages, live multi-phone sync, and a printable PDF checklist.
**Next:** LEGO Pick a Brick prices and bestseller/standard tiers, BrickOwl availability, and the
buy-plan optimizer.

## Run it on a Raspberry Pi (free, always on)

Works on a Pi 4 (any RAM) with Raspberry Pi OS, no monitor needed.

1. **Flash the SD card headless.** In Raspberry Pi Imager pick *Raspberry Pi OS Lite (64-bit)*,
   click the gear / "Edit settings": hostname `legopi`, your username and password, your Wi-Fi
   name and password, and under *Services* enable SSH with password authentication. Write the card.
2. **Boot and connect.** Put the card in, power the Pi, wait ~2 minutes, then from your laptop:
   `ssh YOURUSER@legopi.local`.
3. **Install:**
   ```sh
   curl -fsSL https://raw.githubusercontent.com/Formicidae/Lego_Buyer/main/deploy/pi-install.sh -o pi-install.sh
   bash pi-install.sh
   ```
   It asks for the Rebrickable and BrickOwl keys and a passcode, installs the app as a service on
   port 8000, sets up a nightly database backup, and offers to set up **Tailscale Funnel**, which
   gives the Pi a stable public `https://legopi.<your-tailnet>.ts.net` address so both phones work
   from the store. The passcode gate protects it.
4. **Update later:** `bash /opt/lego-buyer/deploy/update.sh`.

Phones keep working through short outages: the app caches the set and part images and queues taps
made while offline, syncing them when the Pi is reachable again.

## Deploy on Railway (paid alternative, ~$5/month)

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

"""Sync places from OpenStreetMap into the database.

The Procfile runs this every day at 11:00 UTC (the `cron` line). You can also run it
by hand:

    python populate_db.py            # fetch from OSM and save changes
    python populate_db.py --dry-run  # fetch and report, change nothing
    python populate_db.py --cache    # use osm_cache.json from the last run

What it does:
- Adds new cafes, bakeries, bars, groceries ... and public restrooms.
- Updates the name, address, position and tags of places it already knows.
- Never deletes a place. Codes and votes stay. A place that OSM no longer
  lists keeps an old `osm_seen_at`, so you can find closed places later.

Places from before the sync existed have no `osm_id`. The first run links
them to OSM by an exact (name, address) match.
"""
import argparse
import json
import os
import sys
import time

import requests

from app import app, db, CoffeeShop, utcnow
import neighborhoods

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
# Greater Seattle: south, west, north, east
SEATTLE_BBOX = "47.45,-122.55,47.82,-122.20"
CACHE_FILE = "osm_cache.json"
USER_AGENT = "got2gosea.com daily sync (https://got2gosea.com)"
RETRIES = 3
RETRY_WAIT_S = 60

# `nwr` asks for nodes, ways and relations. Many cafes are drawn as a
# building outline (a way), and the old node-only query missed them.
OVERPASS_QUERY = f"""
[out:json][timeout:180];
(
  nwr["shop"~"coffee|tea_shop|pastry|bakery|supermarket|grocery"]({SEATTLE_BBOX});
  nwr["amenity"~"^(cafe|fast_food|bar)$"]({SEATTLE_BBOX});
  nwr["amenity"="toilets"]({SEATTLE_BBOX});
  nwr["leisure"="park"]["name"]({SEATTLE_BBOX});
);
out center tags;
"""

# Parks only name the restrooms inside them. They do not become places.
# A park restroom can sit far from the park's center point, so allow more
# distance for parks than for shops.
PARK_NAME_MAX_M = 400
SHOP_NAME_MAX_M = 150

# OSM tags that we keep in CoffeeShop.tags_json.
KEEP_TAGS = (
    "amenity", "shop", "opening_hours", "wheelchair", "changing_table",
    "unisex", "gender_segregated", "fee", "access", "operator",
    "toilets", "toilets:access", "toilets:wheelchair", "internet_access",
    "website",
)

# Stop without saving if OSM returns far fewer places than we already
# link to it. This guards against a partial or broken Overpass response.
MIN_RESULT_SHARE = 0.5


def fetch_elements(use_cache):
    if use_cache and os.path.exists(CACHE_FILE):
        print(f"Using cached OSM data from {CACHE_FILE}")
        with open(CACHE_FILE) as f:
            return json.load(f)
    # Overpass often answers 429 or 5xx when it is busy. Wait and retry.
    for attempt in range(1, RETRIES + 1):
        print(f"Fetching from the Overpass API (attempt {attempt}) ...")
        try:
            resp = requests.post(OVERPASS_URL, data={"data": OVERPASS_QUERY},
                                 headers={"User-Agent": USER_AGENT}, timeout=240)
            resp.raise_for_status()
            elements = resp.json().get("elements", [])
            break
        except (requests.RequestException, ValueError) as e:
            if attempt == RETRIES:
                raise
            print(f"Failed: {e}. Retrying in {RETRY_WAIT_S} s.")
            time.sleep(RETRY_WAIT_S)
    with open(CACHE_FILE, "w") as f:
        json.dump(elements, f)
    return elements


def build_address(tags):
    """Same format as the original import, so old rows still match."""
    street = " ".join(filter(None, [tags.get("addr:housenumber"), tags.get("addr:street")]))
    city = " ".join(filter(None, [tags.get("addr:city"), tags.get("addr:state", "WA"),
                                  tags.get("addr:postcode")]))
    address = ", ".join(filter(None, [street, city]))
    return address or tags.get("addr:full", "Address not available")


def position(el):
    return (el.get("lat", (el.get("center") or {}).get("lat")),
            el.get("lon", (el.get("center") or {}).get("lon")))


def is_park(el):
    tags = el.get("tags") or {}
    return tags.get("leisure") == "park" and not (tags.get("amenity") or tags.get("shop"))


def restroom_name(lat, lng, parks, shops):
    """'Public restroom, Cal Anderson Park' or 'Public restroom near Victrola'.
    Unique names give each restroom page its own title in search results."""
    def closest(points, max_m):
        best, best_m = None, max_m
        for name, plat, plng in points:
            m = neighborhoods.distance_km(lat, lng, plat, plng) * 1000
            if m < best_m:
                best, best_m = name, m
        return best
    park = closest(parks, PARK_NAME_MAX_M)
    if park:
        return f"Public restroom, {park}"
    shop = closest(shops, SHOP_NAME_MAX_M)
    if shop:
        return f"Public restroom near {shop}"
    return "Public restroom"


def to_place(el, parks=(), shops=()):
    """Turn one Overpass element into a dict, or None to skip it."""
    tags = el.get("tags") or {}
    lat, lng = position(el)
    if lat is None or lng is None or is_park(el):
        return None

    is_restroom = tags.get("amenity") == "toilets"
    if is_restroom:
        if tags.get("access") in ("private", "no"):
            return None
        hood = neighborhoods.nearest(lat, lng)
        name = tags.get("name") or restroom_name(lat, lng, parks, shops)
        address = build_address(tags) if tags.get("addr:street") else ""
        if not address:
            address = f"{hood['name']}, Seattle, WA" if hood else "Seattle, WA"
    else:
        if not tags.get("name"):
            return None
        name = tags["name"]
        address = build_address(tags)

    kept = {k: tags[k] for k in KEEP_TAGS if k in tags}
    return {
        "osm_id": f"{el['type']}/{el['id']}",
        "name": name[:100],
        "address": address[:200],
        "lat": lat,
        "lng": lng,
        "category": "restroom" if is_restroom else "shop",
        "tags_json": json.dumps(kept, sort_keys=True) if kept else None,
    }


def pop_nearby_same_name(unlinked, p, max_m=100):
    """Second chance for old rows: same name, less than max_m meters away.
    This catches a cafe whose address changed, or that OSM redrew."""
    for key, shop in unlinked.items():
        if key[0] == p["name"] and neighborhoods.distance_km(
                shop.lat, shop.lng, p["lat"], p["lng"]) * 1000 <= max_m:
            return unlinked.pop(key)
    return None


def sync(elements, dry_run=False):
    def named_points(els):
        out = []
        for el in els:
            lat, lng = position(el)
            if lat is not None and (el.get("tags") or {}).get("name"):
                out.append((el["tags"]["name"], lat, lng))
        return out
    parks = named_points(el for el in elements if is_park(el))
    shops = named_points(el for el in elements if not is_park(el)
                         and (el.get("tags") or {}).get("amenity") != "toilets")
    places = [p for p in (to_place(el, parks, shops) for el in elements) if p]
    places = list({p["osm_id"]: p for p in places}.values())

    linked = CoffeeShop.query.filter(CoffeeShop.osm_id.isnot(None)).count()
    if linked and len(places) < linked * MIN_RESULT_SHARE:
        print(f"Only {len(places)} places from OSM, but {linked} are linked. "
              "The response looks incomplete. Nothing saved.")
        return 1

    by_osm = {s.osm_id: s for s in CoffeeShop.query.filter(CoffeeShop.osm_id.isnot(None))}
    unlinked = {}
    for s in CoffeeShop.query.filter(CoffeeShop.osm_id.is_(None)):
        unlinked.setdefault((s.name, s.address), s)

    now = utcnow()
    added = updated = linked_now = 0
    for p in places:
        shop = by_osm.get(p["osm_id"])
        if shop is None:
            shop = unlinked.pop((p["name"], p["address"]), None)
            if shop is None:
                shop = pop_nearby_same_name(unlinked, p)
            if shop is not None:
                shop.osm_id = p["osm_id"]
                linked_now += 1
        if shop is None:
            db.session.add(CoffeeShop(osm_seen_at=now, **p))
            added += 1
            continue
        changed = False
        for field in ("name", "address", "lat", "lng", "category", "tags_json"):
            if getattr(shop, field) != p[field]:
                setattr(shop, field, p[field])
                changed = True
        updated += changed
        shop.osm_seen_at = now

    restrooms = sum(1 for p in places if p["category"] == "restroom")
    print(f"OSM places: {len(places)} ({restrooms} public restrooms). "
          f"Added {added}, updated {updated}, linked {linked_now} old rows. "
          f"{len(unlinked)} old rows have no OSM match and stay as they are.")
    if dry_run:
        db.session.rollback()
        print("Dry run: nothing saved.")
    else:
        db.session.commit()
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="Report changes without saving.")
    parser.add_argument("--cache", action="store_true", help=f"Use {CACHE_FILE} if it exists.")
    args = parser.parse_args()
    try:
        elements = fetch_elements(use_cache=args.cache)
    except (requests.RequestException, ValueError) as e:
        print(f"Overpass request failed: {e}. Nothing saved.")
        return 1
    with app.app_context():
        return sync(elements, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())

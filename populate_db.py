import requests
import json
import os
import argparse
from app import app, db, CoffeeShop

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
# Bounding box for Greater Seattle: (min_lat, min_lon, max_lat, max_lon)
SEATTLE_BOUNDING_BOX = "47.45,-122.55,47.82,-122.20"

OVERPASS_QUERY = f""" 
[out:json];
(
  node["shop"~"coffee|tea_shop|pastry|bakery|supermarket|grocery"]({SEATTLE_BOUNDING_BOX});
  node["amenity"~"cafe|fast_food|bar"]({SEATTLE_BOUNDING_BOX});
);
out center;
"""

CACHE_FILE = "osm_coffee_shops_cache.json"

def get_coffee_shops_from_osm(use_cache=True):
    if use_cache and os.path.exists(CACHE_FILE):
        print(f"Loading coffee shops from cache: {CACHE_FILE}")
        with open(CACHE_FILE, "r") as f:
            return json.load(f)
    
    print("Fetching coffee shops from OpenStreetMap API...")
    try:
        response = requests.post(OVERPASS_URL, data=OVERPASS_QUERY)
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching data from Overpass API: {e}")
        return []

    coffee_shops = []
    for element in data.get("elements", []):
        if "tags" in element and "name" in element["tags"]:
            # Construct address
            tags = element["tags"]
            address_parts = [
                tags.get("addr:housenumber"),
                tags.get("addr:street"),
                tags.get("addr:city"),
                tags.get("addr:state", "WA"),
                tags.get("addr:postcode")
            ]
            address = ", ".join(filter(None, [" ".join(filter(None, address_parts[:2])), 
                                             " ".join(filter(None, address_parts[2:]))]))
            if not address:
                address = tags.get("addr:full", "Address not available")

            coffee_shops.append({
                "name": element["tags"]["name"],
                "lat": element.get("lat"),
                "lng": element.get("lon"),
                "address": address
            })
    
    with open(CACHE_FILE, "w") as f:
        json.dump(coffee_shops, f, indent=4)
    print(f"Saved {len(coffee_shops)} coffee shops to cache: {CACHE_FILE}")
    return coffee_shops

def populate_db():
    parser = argparse.ArgumentParser(description='Populate the database with coffee shops from OpenStreetMap.')
    parser.add_argument('--no-cache', action='store_true', help='Do not use the cache file and fetch fresh data.')
    args = parser.parse_args()

    with app.app_context():
        # Get coffee shops from OpenStreetMap
        osm_coffee_shops = get_coffee_shops_from_osm(use_cache=not args.no_cache)

        existing_shops = {
            (shop.name, shop.address): shop for shop in CoffeeShop.query.all()
        }
        
        new_shops_count = 0
        for shop_data in osm_coffee_shops:
            if (shop_data["name"], shop_data["address"]) not in existing_shops:
                coffee_shop = CoffeeShop(
                    name=shop_data["name"],
                    address=shop_data["address"],
                    lat=shop_data["lat"],
                    lng=shop_data["lng"]
                )
                db.session.add(coffee_shop)
                new_shops_count += 1

        if new_shops_count > 0:
            db.session.commit()
            print(f"Added {new_shops_count} new coffee shops to the database.")
        else:
            print("No new coffee shops to add.")

if __name__ == "__main__":
    populate_db()
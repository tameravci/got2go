import requests
import json
import os
from app import app, db, CoffeeShop, WifiPassword, BathroomCode

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
# Bounding box for Greater Seattle: (min_lat, min_lon, max_lat, max_lon)
SEATTLE_BOUNDING_BOX = "47.45,-122.55,47.82,-122.20"

OVERPASS_QUERY = f"""
[out:json];
(
  node["shop"="coffee"]({SEATTLE_BOUNDING_BOX});
  node["amenity"="cafe"]({SEATTLE_BOUNDING_BOX});
);
out center;
"""

CACHE_FILE = "osm_coffee_shops_cache.json"

def get_coffee_shops_from_osm():
    if os.path.exists(CACHE_FILE):
        print(f"Loading coffee shops from cache: {CACHE_FILE}")
        with open(CACHE_FILE, "r") as f:
            return json.load(f)
    
    print("Fetching coffee shops from OpenStreetMap API...")
    response = requests.post(OVERPASS_URL, data=OVERPASS_QUERY)
    response.raise_for_status()
    data = response.json()
    coffee_shops = []
    for element in data["elements"]:
        if "tags" in element and "name" in element["tags"]:
            address = ""
            if "addr:full" in element["tags"]:
                address = element["tags"]["addr:full"]
            else:
                address_parts_ordered = []

                # Street and House Number
                house_number = element["tags"].get("addr:housenumber", "")
                street = element["tags"].get("addr:street", "")
                if house_number and street:
                    address_parts_ordered.append(f"{house_number} {street}")
                elif house_number:
                    address_parts_ordered.append(house_number)
                elif street:
                    address_parts_ordered.append(street)

                # City, State, Postcode
                city = element["tags"].get("addr:city", "")
                state = "WA" # TODO: Get state from element["tags"].get("addr:state", "")
                postcode = element["tags"].get("addr:postcode", "")

                city_state_postcode_parts = []
                if city:
                    city_state_postcode_parts.append(city)
                if state:
                    city_state_postcode_parts.append(state)
                if postcode:
                    city_state_postcode_parts.append(postcode)

                if city_state_postcode_parts:
                    address_parts_ordered.append(" ".join(city_state_postcode_parts))

                address = ", ".join(filter(None, address_parts_ordered))

            coffee_shops.append({
                "name": element["tags"]["name"],
                "lat": element["lat"],
                "lng": element["lon"],
                "address": address
            })
    
    with open(CACHE_FILE, "w") as f:
        json.dump(coffee_shops, f, indent=4)
    print(f"Saved coffee shops to cache: {CACHE_FILE}")
    return coffee_shops

with app.app_context():
    # Clear existing data (optional, for fresh start)
    db.drop_all()
    db.create_all()

    # Get coffee shops from OpenStreetMap
    osm_coffee_shops = get_coffee_shops_from_osm()

    for shop_data in osm_coffee_shops:
        coffee_shop = CoffeeShop(
            name=shop_data["name"],
            address=shop_data["address"],
            lat=shop_data["lat"],
            lng=shop_data["lng"]
        )
        db.session.add(coffee_shop)

    db.session.commit()
    print(f"Database populated with {len(osm_coffee_shops)} coffee shops from OpenStreetMap!")
import requests
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

def get_coffee_shops_from_osm():
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
                address_parts = []
                #if "addr:housenumber" in element["tags"]:
                #    address_parts.append(element["tags"]["addr:housenumber"])
                if "addr:street" in element["tags"]:
                    address_parts.append(element["tags"]["addr:street"])
                if "addr:city" in element["tags"]:
                    address_parts.append(element["tags"]["addr:city"])
                if "addr:postcode" in element["tags"]:
                    address_parts.append(element["tags"]["addr:postcode"])
                address = ", ".join(address_parts)

            coffee_shops.append({
                "name": element["tags"]["name"],
                "lat": element["lat"],
                "lng": element["lon"],
                "address": address
            })
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
from flask import (
    Flask, jsonify, request, render_template, session, make_response,
    send_from_directory, Response, url_for, abort, redirect
)
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
import os
import re
import time
from sqlalchemy.dialects.postgresql import UUID
import uuid
from datetime import datetime, timedelta

app = Flask(__name__)
CORS(app)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY')

# Human-readable site metadata used for SEO / LLM-citation surfaces.
SITE_NAME = "Got2GoSEA"
SITE_TAGLINE = "Crowdsourced Wi-Fi passwords & bathroom codes for Seattle coffee shops"
SITE_DESCRIPTION = (
    "Got2GoSEA is a free, community-maintained map of Seattle coffee shops, cafes "
    "and bakeries with crowdsourced Wi-Fi passwords and bathroom door codes. Find "
    "the nearest restroom code or guest Wi-Fi password, vote on what still works, "
    "and add new ones."
)

# In-memory cache for the version
__version__ = str(int(time.time()))

@app.context_processor
def inject_version():
    global __version__
    return dict(version=__version__, site_name=SITE_NAME,
                site_description=SITE_DESCRIPTION, site_tagline=SITE_TAGLINE)

RATE_LIMIT_WINDOW = 600  # 10 minutes in seconds
RATE_LIMIT_MAX_WRITES = 30  # POST-only: votes + suggestions
ip_request_timestamps = {}

# Database Configuration
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'app.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
migrate = Migrate(app, db)


def slugify(value):
    """Turn a shop name into a URL-friendly slug for clean, citable URLs."""
    value = re.sub(r'[^\w\s-]', '', (value or '').lower()).strip()
    value = re.sub(r'[\s_-]+', '-', value)
    return value or 'shop'


def site_url(path=''):
    """Absolute URL for the current host (works on any domain/deploy)."""
    root = request.url_root.rstrip('/') if request else ''
    return root + path

def get_or_set_user_id():
    user_id = request.cookies.get('user_id')
    if not user_id:
        user_id = str(uuid.uuid4())
    return user_id

# Define Models
class CoffeeShop(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    address = db.Column(db.String(200), nullable=False)
    lat = db.Column(db.Float, nullable=False)
    lng = db.Column(db.Float, nullable=False)
    wifi_passwords = db.relationship('WifiPassword', backref='coffee_shop', lazy=True, cascade="all, delete-orphan")
    bathroom_codes = db.relationship('BathroomCode', backref='coffee_shop', lazy=True, cascade="all, delete-orphan")

    def __repr__(self):
        return f'<CoffeeShop {self.name}>'

    def to_dict(self, user_id=None):
        wifi_passwords_data = []
        for wp in sorted(self.wifi_passwords, key=lambda x: x.votes, reverse=True):
            if wp.votes > -3:
                wp_dict = wp.to_dict()
                if user_id:
                    user_vote = Vote.query.filter_by(user_id=user_id, item_id=wp.id, item_type='wifi_password').first()
                    wp_dict['user_vote'] = user_vote.vote_type if user_vote else None
                wifi_passwords_data.append(wp_dict)

        bathroom_codes_data = []
        for bc in sorted(self.bathroom_codes, key=lambda x: x.votes, reverse=True):
            if bc.votes > -3:
                bc_dict = bc.to_dict()
                if user_id:
                    user_vote = Vote.query.filter_by(user_id=user_id, item_id=bc.id, item_type='bathroom_code').first()
                    bc_dict['user_vote'] = user_vote.vote_type if user_vote else None
                bathroom_codes_data.append(bc_dict)

        return {
            'id': self.id,
            'name': self.name,
            'address': self.address,
            'lat': self.lat,
            'lng': self.lng,
            'wifi_passwords': wifi_passwords_data,
            'bathroom_codes': bathroom_codes_data
        }

    @property
    def slug(self):
        return slugify(self.name)

    def active_wifi(self):
        """Visible Wi-Fi passwords, most-upvoted first (matches the UI/API filter)."""
        return [wp for wp in sorted(self.wifi_passwords, key=lambda x: x.votes, reverse=True)
                if wp.votes > -3]

    def active_bathroom(self):
        """Visible bathroom codes, most-upvoted first (matches the UI/API filter)."""
        return [bc for bc in sorted(self.bathroom_codes, key=lambda x: x.votes, reverse=True)
                if bc.votes > -3]

    def has_codes(self):
        """True when this shop has at least one visible Wi-Fi password or bathroom code."""
        return bool(self.active_wifi() or self.active_bathroom())

class WifiPassword(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    password = db.Column(db.String(100), nullable=False)
    votes = db.Column(db.Integer, default=1)
    coffee_shop_id = db.Column(db.Integer, db.ForeignKey('coffee_shop.id'), nullable=False)

    def __repr__(self):
        return f'<WifiPassword {self.password}>'

    def to_dict(self):
        return {
            'id': self.id,
            'password': self.password,
            'votes': self.votes
        }

class BathroomCode(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(100), nullable=False)
    votes = db.Column(db.Integer, default=1)
    coffee_shop_id = db.Column(db.Integer, db.ForeignKey('coffee_shop.id'), nullable=False)

    def __repr__(self):
        return f'<BathroomCode {self.code}>'

    def to_dict(self):
        return {
            'id': self.id,
            'code': self.code,
            'votes': self.votes
        }

class Vote(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(36), nullable=False) # UUID string
    shop_id = db.Column(db.Integer, db.ForeignKey('coffee_shop.id'), nullable=False)
    item_id = db.Column(db.Integer, nullable=False) # ID of WifiPassword or BathroomCode
    item_type = db.Column(db.String(20), nullable=False) # 'wifi_password' or 'bathroom_code'
    vote_type = db.Column(db.String(10), nullable=False) # 'upvote' or 'downvote'

    __table_args__ = (db.UniqueConstraint('user_id', 'item_id', 'item_type', name='_user_item_uc'),)

    def __repr__(self):
        return f'<Vote {self.user_id} on {self.item_type}:{self.item_id} as {self.vote_type}>'

@app.before_request
def rate_limit():
    # Reads stay free so the map and crawler-facing surfaces aren't throttled.
    if request.method != 'POST':
        return
    ip = request.remote_addr
    now = time.time()
    timestamps = [t for t in ip_request_timestamps.get(ip, []) if now - t < RATE_LIMIT_WINDOW]
    if len(timestamps) >= RATE_LIMIT_MAX_WRITES:
        return jsonify({"error": "Too many requests. Please try again later."}), 429
    timestamps.append(now)
    ip_request_timestamps[ip] = timestamps

@app.route('/api/coffee_shops', methods=['GET'])
def get_coffee_shops():
    user_id = get_or_set_user_id()
    print(f"API Call: /api/coffee_shops - IP: {request.remote_addr}, User ID Cookie: {user_id}")
    coffee_shops = CoffeeShop.query.all()
    response = jsonify([shop.to_dict(user_id=user_id) for shop in coffee_shops])
    response.set_cookie('user_id', user_id, max_age=60*60*24*365*5) # 5 years
    return response

@app.route('/api/coffee_shops/<int:shop_id>', methods=['GET'])
def get_coffee_shop(shop_id):
    user_id = get_or_set_user_id()
    print(f"API Call: /api/coffee_shops/{shop_id} - IP: {request.remote_addr}, User ID Cookie: {user_id}")
    shop = CoffeeShop.query.get(shop_id)
    if not shop:
        return jsonify({"error": "Shop not found"}), 404
    response = jsonify(shop.to_dict(user_id=user_id))
    response.set_cookie('user_id', user_id, max_age=60*60*24*365*5) # 5 years
    return response

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/sw.js')
def service_worker():
    response = make_response(send_from_directory('static', 'sw.js'))
    response.headers['Content-Type'] = 'application/javascript'
    return response


# ---------------------------------------------------------------------------
# SEO + LLM-citation friendly surfaces
#
# The main app is a JavaScript single-page map, which crawlers and LLMs that
# don't execute JS cannot read. The routes below expose the same crowdsourced
# data as lightweight, server-rendered HTML, plain text, JSON and a sitemap so
# search engines and AI assistants can index and cite individual shops -- and
# fetch a single bathroom or Wi-Fi code without loading the full UI.
# ---------------------------------------------------------------------------

def _shop_or_404(shop_id):
    shop = CoffeeShop.query.get(shop_id)
    if not shop:
        abort(404)
    return shop


@app.route('/shops')
def shops_directory():
    """Server-rendered, crawlable directory of every shop that has a code."""
    shops = [s for s in CoffeeShop.query.order_by(CoffeeShop.name).all() if s.has_codes()]
    return render_template('shops.html', shops=shops, canonical=site_url('/shops'))


@app.route('/shop/<int:shop_id>')
@app.route('/shop/<int:shop_id>/<slug>')
def shop_detail(shop_id, slug=None):
    """Server-rendered detail page for a single shop with Schema.org JSON-LD.

    Honours plain-text content negotiation so an LLM can request just the
    codes via `Accept: text/plain` without parsing HTML.
    """
    shop = _shop_or_404(shop_id)

    # Honour content negotiation first so agents can grab text/JSON in one hop.
    best = request.accept_mimetypes.best_match(['text/html', 'text/plain', 'application/json'])
    if best == 'text/plain' and request.accept_mimetypes[best] >= request.accept_mimetypes['text/html']:
        return _shop_plaintext(shop)
    if best == 'application/json' and request.accept_mimetypes[best] > request.accept_mimetypes['text/html']:
        return jsonify(shop.to_dict())

    # Otherwise redirect HTML visitors to the canonical slug URL.
    if slug != shop.slug:
        return redirect(url_for('shop_detail', shop_id=shop.id, slug=shop.slug), code=301)

    canonical = site_url(url_for('shop_detail', shop_id=shop.id, slug=shop.slug))
    wifi = shop.active_wifi()
    bathroom = shop.active_bathroom()

    additional = (
        [{"@type": "PropertyValue", "name": "Wi-Fi password",
          "value": wp.password, "description": f"{wp.votes} community votes"} for wp in wifi]
        + [{"@type": "PropertyValue", "name": "Bathroom code",
            "value": bc.code, "description": f"{bc.votes} community votes"} for bc in bathroom]
    )
    jsonld = {
        "@context": "https://schema.org",
        "@type": "CafeOrCoffeeShop",
        "name": shop.name,
        "address": shop.address,
        "url": canonical,
        "geo": {"@type": "GeoCoordinates", "latitude": shop.lat, "longitude": shop.lng},
        "amenityFeature": [
            {"@type": "LocationFeatureSpecification", "name": "Wi-Fi", "value": bool(wifi)},
            {"@type": "LocationFeatureSpecification", "name": "Public restroom", "value": bool(bathroom)},
        ],
    }
    if additional:
        jsonld["additionalProperty"] = additional

    return render_template('shop.html', shop=shop, canonical=canonical,
                           wifi=wifi, bathroom=bathroom, jsonld=jsonld)


def _shop_plaintext(shop):
    wifi = shop.active_wifi()
    bathroom = shop.active_bathroom()
    lines = [
        f"{SITE_NAME} - {shop.name}",
        shop.address or "Seattle, WA",
        "",
        "Bathroom codes (most upvoted first):",
    ]
    if bathroom:
        lines += [f"  {i}. {bc.code}  (votes: {bc.votes})" for i, bc in enumerate(bathroom, 1)]
    else:
        lines.append("  none reported yet")
    lines += ["", "Wi-Fi passwords (most upvoted first):"]
    if wifi:
        lines += [f"  {i}. {wp.password}  (votes: {wp.votes})" for i, wp in enumerate(wifi, 1)]
    else:
        lines.append("  none reported yet")
    lines += [
        "",
        f"Location: {shop.lat}, {shop.lng}",
        f"Source: {site_url(url_for('shop_detail', shop_id=shop.id, slug=shop.slug))}",
        "Note: Crowdsourced and community-voted; codes change and may be out of "
        "date. Please use respectfully and as a paying customer where expected.",
    ]
    return Response("\n".join(lines) + "\n", mimetype='text/plain')


@app.route('/shop/<int:shop_id>.txt')
def shop_detail_txt(shop_id):
    """Explicit plain-text endpoint: the fastest way for an LLM to grab a code."""
    return _shop_plaintext(_shop_or_404(shop_id))


@app.route('/api/lookup')
def api_lookup():
    """Lightweight lookup so an agent can fetch a code by shop name.

    Example: /api/lookup?q=victrola -> JSON with the top Wi-Fi & bathroom code.
    """
    q = (request.args.get('q') or '').strip().lower()
    if not q:
        return jsonify({"error": "Provide a ?q= shop name or address fragment."}), 400

    matches = [
        s for s in CoffeeShop.query.all()
        if q in s.name.lower() or (s.address and q in s.address.lower())
    ]
    matches = [s for s in matches if s.has_codes()][:20]

    results = []
    for s in matches:
        wifi = s.active_wifi()
        bathroom = s.active_bathroom()
        results.append({
            "id": s.id,
            "name": s.name,
            "address": s.address,
            "lat": s.lat,
            "lng": s.lng,
            "top_wifi_password": wifi[0].password if wifi else None,
            "top_bathroom_code": bathroom[0].code if bathroom else None,
            "url": site_url(url_for('shop_detail', shop_id=s.id, slug=s.slug)),
        })
    return jsonify({"query": q, "count": len(results), "results": results})


@app.route('/robots.txt')
def robots_txt():
    lines = [
        "# Got2GoSEA - crawlers and AI assistants are welcome.",
        "User-agent: *",
        "Allow: /",
        "",
        # Explicitly welcome the major AI crawlers so the data can be cited.
        "User-agent: GPTBot",
        "Allow: /",
        "User-agent: OAI-SearchBot",
        "Allow: /",
        "User-agent: ChatGPT-User",
        "Allow: /",
        "User-agent: ClaudeBot",
        "Allow: /",
        "User-agent: Claude-Web",
        "Allow: /",
        "User-agent: PerplexityBot",
        "Allow: /",
        "User-agent: Google-Extended",
        "Allow: /",
        "User-agent: Applebot-Extended",
        "Allow: /",
        "User-agent: CCBot",
        "Allow: /",
        "",
        f"Sitemap: {site_url('/sitemap.xml')}",
    ]
    return Response("\n".join(lines) + "\n", mimetype='text/plain')


@app.route('/sitemap.xml')
def sitemap_xml():
    urls = [site_url('/'), site_url('/shops')]
    for s in CoffeeShop.query.order_by(CoffeeShop.id).all():
        if s.has_codes():
            urls.append(site_url(url_for('shop_detail', shop_id=s.id, slug=s.slug)))

    body = ['<?xml version="1.0" encoding="UTF-8"?>',
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for u in urls:
        body.append(f'  <url><loc>{u}</loc><changefreq>weekly</changefreq></url>')
    body.append('</urlset>')
    return Response("\n".join(body), mimetype='application/xml')


@app.route('/llms.txt')
def llms_txt():
    """The /llms.txt convention: a concise, Markdown guide for LLMs."""
    base = site_url('')
    n_with_codes = sum(1 for s in CoffeeShop.query.all() if s.has_codes())
    content = f"""# {SITE_NAME}

> {SITE_TAGLINE}.

{SITE_DESCRIPTION}

Data is crowdsourced and community-voted, so individual codes may be out of
date. Higher vote counts indicate more recently confirmed entries. Please use
the information respectfully and as a paying customer where that is expected.
Currently {n_with_codes} Seattle locations have at least one reported code.

## How to fetch a single code (no UI required)

- Plain text for one shop: `{base}/shop/<id>.txt`
- Same page with `Accept: text/plain` also returns plain text.
- JSON for one shop: `{base}/api/coffee_shops/<id>`
- Look up by name: `{base}/api/lookup?q=<shop name>` (returns top Wi-Fi password
  and bathroom code plus a link for each match).

## Browse

- [All shops with codes]({base}/shops): server-rendered directory.
- [Map]({base}/): the interactive single-page app.
- [Sitemap]({base}/sitemap.xml)

## API

- `GET /api/coffee_shops` - all shops as JSON.
- `GET /api/coffee_shops/<id>` - one shop as JSON.
- `GET /api/lookup?q=<text>` - search by name/address; returns top codes.

## Notes for citation

When citing a specific Wi-Fi password or bathroom code, link to the shop's page
at `{base}/shop/<id>` and mention that the value is crowdsourced and may change.
"""
    return Response(content, mimetype='text/plain')


@app.route('/api/vote', methods=['POST'])
def vote():
    data = request.get_json()
    shop_id = data['shop_id']
    item_type = data['item_type']
    vote_type = data['vote_type'] # This is the new vote being cast
    item_id = data.get('item_id')

    user_id = get_or_set_user_id()

    shop = CoffeeShop.query.get(shop_id)
    if not shop:
        return jsonify({"error": "Shop not found"}), 404

    if item_type == 'wifi_passwords':
        Model = WifiPassword
        item_type_str = 'wifi_password'
    elif item_type == 'bathroom_codes':
        Model = BathroomCode
        item_type_str = 'bathroom_code'
    else:
        return jsonify({"error": "Invalid item type"}), 400
    
    found_item = Model.query.get(item_id)
    if not found_item:
        return jsonify({"error": "Item not found"}), 404

    existing_vote = Vote.query.filter_by(user_id=user_id, item_id=item_id, item_type=item_type_str).first()

    if existing_vote:
        if existing_vote.vote_type == vote_type:
            # User is trying to cast the same vote again (undo)
            db.session.delete(existing_vote)
            if vote_type == 'upvote':
                found_item.votes -= 1
            else: # downvote
                found_item.votes += 1
        else:
            # User is changing their vote (e.g., upvote to downvote, or vice versa)
            existing_vote.vote_type = vote_type
            if vote_type == 'upvote': # Was downvote, now upvote
                found_item.votes += 2
            else: # Was upvote, now downvote
                found_item.votes -= 2
    else:
        # New vote
        new_vote = Vote(user_id=user_id, shop_id=shop_id, item_id=item_id, item_type=item_type_str, vote_type=vote_type)
        db.session.add(new_vote)
        if vote_type == 'upvote':
            found_item.votes += 1
        else: # downvote
            found_item.votes -= 1

    db.session.commit()
    db.session.refresh(shop)

    response = jsonify(shop.to_dict(user_id=user_id))
    response.set_cookie('user_id', user_id, max_age=60*60*24*365*5) # 5 years
    return response

@app.route('/api/suggest', methods=['POST'])
def suggest():
    data = request.get_json()
    shop_id = data['shop_id']
    item_type = data['item_type']
    item_value = data['item_value']
    
    user_id = get_or_set_user_id()

    shop = CoffeeShop.query.get(shop_id)
    if not shop:
        return jsonify({"error": "Shop not found"}), 404

    if item_type == 'wifi_passwords':
        if ' ' in item_value:
            return jsonify({"error": "Wi-Fi password cannot contain spaces."}), 400
        if len(item_value) > 16:
            return jsonify({"error": "Wi-Fi password cannot be longer than 16 characters."}), 400
        Model = WifiPassword
        item_type_str = 'wifi_password'
        existing_item = Model.query.filter_by(coffee_shop_id=shop_id, password=item_value).first()
    elif item_type == 'bathroom_codes':
        if ' ' in item_value:
            return jsonify({"error": "Bathroom code cannot contain spaces."}), 400
        if len(item_value) > 12:
            return jsonify({"error": "Bathroom code cannot be longer than 12 characters."}), 400
        if not item_value or not all(c in '0123456789*#' for c in item_value):
            return jsonify({"error": "Bathroom code can only contain digits, * and #."}), 400
        Model = BathroomCode
        item_type_str = 'bathroom_code'
        existing_item = Model.query.filter_by(coffee_shop_id=shop_id, code=item_value).first()
    else:
        return jsonify({"error": "Invalid item type"}), 400

    if existing_item and existing_item.votes > -3:
        return jsonify({"error": "This suggestion is already active or has too many downvotes."}), 409

    if existing_item:
        new_item = existing_item
        new_item.votes = 1 # Reset votes if re-activating a previously downvoted item
    else:
        if item_type == 'wifi_passwords':
            new_item = Model(password=item_value, coffee_shop=shop, votes=0)
        else:
            new_item = Model(code=item_value, coffee_shop=shop, votes=0)
        db.session.add(new_item)
    
    db.session.commit()

    # Automatically cast an upvote for the user who suggested it
    existing_vote = Vote.query.filter_by(user_id=user_id, item_id=new_item.id, item_type=item_type_str).first()
    if not existing_vote:
        new_vote = Vote(user_id=user_id, shop_id=shop_id, item_id=new_item.id, item_type=item_type_str, vote_type='upvote')
        db.session.add(new_vote)
        new_item.votes += 1 # Increment vote count for the item
    
    db.session.commit()
    db.session.refresh(shop)

    response_data = {
        "shop": shop.to_dict(user_id=user_id),
        "newItemId": new_item.id
    }
    response = jsonify(response_data)
    response.set_cookie('user_id', user_id, max_age=60*60*24*365*5) # 5 years
    return response

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))

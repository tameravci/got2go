from flask import (
    Flask, jsonify, request, render_template, session, make_response,
    send_from_directory, Response, url_for, abort, redirect
)
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
import hashlib
import json
import os
import re
import statistics
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from sqlalchemy import or_, text
from sqlalchemy.orm import selectinload

import neighborhoods

app = Flask(__name__)
# Other sites may read the public JSON API. Writes stay same-origin only.
CORS(app, resources={r"/api/*": {"origins": "*", "methods": ["GET"]}})
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
                site_description=SITE_DESCRIPTION, site_tagline=SITE_TAGLINE,
                abs_url=abs_url, neighborhood_list=neighborhoods.NEIGHBORHOODS,
                feature_labels=feature_labels)

RATE_LIMIT_WINDOW = 600  # 10 minutes in seconds
RATE_LIMIT_MAX_WRITES = 30  # POST-only: votes + suggestions

# Codes at or below this score are hidden everywhere.
HIDE_AT_VOTES = -3
# Upper bound on shops returned for one map viewport.
MAX_SHOPS_PER_QUERY = 1000

# Database Configuration
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.environ.get(
    'DATABASE_PATH', os.path.join(basedir, 'app.db'))
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
migrate = Migrate(app, db)


def slugify(value):
    """Turn a shop name into a URL-friendly slug for clean, citable URLs."""
    value = re.sub(r'[^\w\s-]', '', (value or '').lower()).strip()
    value = re.sub(r'[\s_-]+', '-', value)
    return value or 'shop'


# Search engines must see one site only: https on the apex domain.
CANONICAL_ROOT = "https://got2gosea.com"
CANONICAL_HOSTS = {"got2gosea.com", "www.got2gosea.com"}


def site_url(path=''):
    """Absolute URL. On the production domain it is always the https apex
    URL, even when the request came in over http. Elsewhere (localhost,
    piku preview) it uses the request host."""
    if request and request.host.split(':')[0] in CANONICAL_HOSTS:
        return CANONICAL_ROOT + path
    root = request.url_root.rstrip('/') if request else CANONICAL_ROOT
    return root + path


def abs_url(endpoint, **values):
    """Template helper: url_for() plus the canonical root."""
    return site_url(url_for(endpoint, **values))


def feature_labels(tags):
    """Readable labels for the OSM tags we keep (wheelchair, fee ...)."""
    labels = []
    wheelchair = tags.get('wheelchair') or tags.get('toilets:wheelchair')
    if wheelchair == 'yes':
        labels.append('Wheelchair accessible')
    elif wheelchair == 'limited':
        labels.append('Limited wheelchair access')
    elif wheelchair == 'no':
        labels.append('Not wheelchair accessible')
    if tags.get('changing_table') == 'yes':
        labels.append('Changing table')
    if tags.get('unisex') == 'yes' or tags.get('gender_segregated') == 'no':
        labels.append('All-gender')
    if tags.get('fee') == 'yes':
        labels.append('Fee required')
    elif tags.get('fee') == 'no':
        labels.append('Free')
    if 'customers' in (tags.get('access'), tags.get('toilets:access')):
        labels.append('Customers only')
    if tags.get('toilets') == 'yes' and tags.get('amenity') != 'toilets':
        labels.append('Has a restroom')
    return labels


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso(dt):
    return dt.isoformat() + 'Z' if dt else None


@app.template_filter('ago')
def time_ago(dt):
    """'today', '3 days ago', '2 months ago' for a naive UTC datetime."""
    if not dt:
        return ''
    days = (utcnow() - dt).days
    if days < 1:
        return 'today'
    if days < 2:
        return 'yesterday'
    if days < 45:
        return f'{days} days ago'
    if days < 365:
        return f'{days // 30} months ago'
    return f'{days // 365} year{"s" if days >= 730 else ""} ago'

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
    # 'shop' (cafe, bakery, bar...) or 'restroom' (a public toilet).
    category = db.Column(db.String(16), nullable=False, default='shop', server_default='shop')
    # OpenStreetMap element, for example 'node/123' or 'way/456'.
    osm_id = db.Column(db.String(32))
    # Last time the daily OSM sync saw this place. Old values hint that it closed.
    osm_seen_at = db.Column(db.DateTime)
    # A few OSM tags as JSON (opening hours, wheelchair access, fee ...).
    tags_json = db.Column(db.Text)
    wifi_passwords = db.relationship('WifiPassword', backref='coffee_shop', lazy=True, cascade="all, delete-orphan")
    bathroom_codes = db.relationship('BathroomCode', backref='coffee_shop', lazy=True, cascade="all, delete-orphan")

    def __repr__(self):
        return f'<CoffeeShop {self.name}>'

    @property
    def tags(self):
        try:
            return json.loads(self.tags_json) if self.tags_json else {}
        except ValueError:
            return {}

    @property
    def is_restroom(self):
        return self.category == 'restroom'

    @property
    def neighborhood(self):
        return neighborhoods.nearest(self.lat, self.lng)

    def to_dict(self, votes=None, speeds=None):
        """Serialize the shop. `votes` maps (item_type, item_id) to the
        current user's vote, as returned by user_votes_for(). `speeds` maps
        shop id to a Wi-Fi speed summary, as returned by speed_summaries()."""
        wifi_passwords_data = []
        for wp in self.active_wifi():
            wp_dict = wp.to_dict()
            if votes is not None:
                wp_dict['user_vote'] = votes.get(('wifi_password', wp.id))
            wifi_passwords_data.append(wp_dict)

        bathroom_codes_data = []
        for bc in self.active_bathroom():
            bc_dict = bc.to_dict()
            if votes is not None:
                bc_dict['user_vote'] = votes.get(('bathroom_code', bc.id))
            bathroom_codes_data.append(bc_dict)

        if speeds is None:
            speeds = speed_summaries([self.id])
        hood = self.neighborhood
        return {
            'id': self.id,
            'name': self.name,
            'address': self.address,
            'lat': self.lat,
            'lng': self.lng,
            'category': self.category,
            'tags': self.tags,
            'neighborhood': hood['name'] if hood else None,
            'url': url_for('shop_detail', shop_id=self.id, slug=self.slug),
            'wifi_speed': speeds.get(self.id),
            'wifi_passwords': wifi_passwords_data,
            'bathroom_codes': bathroom_codes_data
        }

    def last_confirmed(self):
        """Newest confirmation time across visible codes, or None."""
        times = [i.last_confirmed_at for i in self.active_wifi() + self.active_bathroom()
                 if i.last_confirmed_at]
        return max(times) if times else None

    @property
    def slug(self):
        return slugify(self.name)

    def active_wifi(self):
        """Visible Wi-Fi passwords, most-upvoted first (matches the UI/API filter)."""
        return [wp for wp in sorted(self.wifi_passwords, key=lambda x: x.votes, reverse=True)
                if wp.votes > HIDE_AT_VOTES]

    def active_bathroom(self):
        """Visible bathroom codes, most-upvoted first (matches the UI/API filter)."""
        return [bc for bc in sorted(self.bathroom_codes, key=lambda x: x.votes, reverse=True)
                if bc.votes > HIDE_AT_VOTES]

    def has_codes(self):
        """True when this shop has at least one visible Wi-Fi password or bathroom code."""
        return bool(self.active_wifi() or self.active_bathroom())

class WifiPassword(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    password = db.Column(db.String(100), nullable=False)
    votes = db.Column(db.Integer, default=1)
    coffee_shop_id = db.Column(db.Integer, db.ForeignKey('coffee_shop.id'), nullable=False)
    # The network name. Optional: older passwords have none.
    ssid = db.Column(db.String(32))
    created_at = db.Column(db.DateTime)
    # Set when the code is added and on each upvote ("it worked").
    last_confirmed_at = db.Column(db.DateTime)

    def __repr__(self):
        return f'<WifiPassword {self.password}>'

    def to_dict(self):
        return {
            'id': self.id,
            'password': self.password,
            'ssid': self.ssid,
            'votes': self.votes,
            'created_at': iso(self.created_at),
            'last_confirmed_at': iso(self.last_confirmed_at),
        }

class BathroomCode(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(100), nullable=False)
    votes = db.Column(db.Integer, default=1)
    coffee_shop_id = db.Column(db.Integer, db.ForeignKey('coffee_shop.id'), nullable=False)
    created_at = db.Column(db.DateTime)
    # Set when the code is added and on each upvote ("it worked").
    last_confirmed_at = db.Column(db.DateTime)

    def __repr__(self):
        return f'<BathroomCode {self.code}>'

    def to_dict(self):
        return {
            'id': self.id,
            'code': self.code,
            'votes': self.votes,
            'created_at': iso(self.created_at),
            'last_confirmed_at': iso(self.last_confirmed_at),
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

class WriteEvent(db.Model):
    """One POST from one IP. The rate limiter counts these rows.

    The rows live in the database, so every worker process shares them and
    they survive a deploy. Rows older than the window are deleted.
    """
    id = db.Column(db.Integer, primary_key=True)
    ip = db.Column(db.String(64), nullable=False, index=True)
    ts = db.Column(db.Float, nullable=False, index=True)


class SpeedTest(db.Model):
    """One Wi-Fi speed test that a visitor ran at a shop."""
    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey('coffee_shop.id'), nullable=False, index=True)
    user_id = db.Column(db.String(36), nullable=False)
    mbps = db.Column(db.Float, nullable=False)
    ping_ms = db.Column(db.Float)
    created_at = db.Column(db.DateTime, nullable=False)
    # Salted hash of the visitor's public IP. Visitors on the same cafe
    # Wi-Fi share one public IP; a test over cellular comes from another.
    ip_hash = db.Column(db.String(16))


def hash_ip(ip):
    salt = app.config.get('SECRET_KEY') or 'got2go'
    return hashlib.sha256(f'{salt}:{ip}'.encode()).hexdigest()[:16]


def same_network_tests(tests):
    """Keep only tests from the most common network, once two or more
    tests share one. This drops tests that ran over cellular data, which
    the browser cannot always detect (for example on iPhones)."""
    counts = {}
    for t in tests:
        if t.ip_hash:
            counts[t.ip_hash] = counts.get(t.ip_hash, 0) + 1
    if not counts:
        return tests
    top, n = max(counts.items(), key=lambda kv: kv[1])
    return [t for t in tests if t.ip_hash == top] if n >= 2 else tests


SPEED_WINDOW_DAYS = 180   # ignore older tests
SPEED_MAX_TESTS = 10      # median of the newest N tests
SPEED_MAX_DISTANCE_M = 200  # the visitor must be this close to the shop
SPEED_TEST_MAX_MB = 5
# navigator.connection.type values that mean "not on Wi-Fi".
CELLULAR_TYPES = {'cellular', 'bluetooth', 'wimax', 'none'}
SSID_MAX = 32


def speed_summaries(shop_ids):
    """Return {shop_id: {'mbps', 'ping_ms', 'tests', 'last_tested_at'}}."""
    if not shop_ids:
        return {}
    since = utcnow() - timedelta(days=SPEED_WINDOW_DAYS)
    rows = (SpeedTest.query
            .filter(SpeedTest.shop_id.in_(shop_ids), SpeedTest.created_at >= since)
            .order_by(SpeedTest.created_at.desc()).all())
    grouped = {}
    for row in rows:
        grouped.setdefault(row.shop_id, []).append(row)
    out = {}
    for shop_id, tests in grouped.items():
        tests = same_network_tests(tests[:SPEED_MAX_TESTS])
        pings = [t.ping_ms for t in tests if t.ping_ms is not None]
        out[shop_id] = {
            'mbps': round(statistics.median(t.mbps for t in tests), 1),
            'ping_ms': round(statistics.median(pings)) if pings else None,
            'tests': len(tests),
            'last_tested_at': iso(tests[0].created_at),
        }
    return out


def has_codes_clause():
    """SQL filter: the shop has at least one visible Wi-Fi password or code."""
    return or_(
        CoffeeShop.wifi_passwords.any(WifiPassword.votes > HIDE_AT_VOTES),
        CoffeeShop.bathroom_codes.any(BathroomCode.votes > HIDE_AT_VOTES),
    )


def listed_clause():
    """SQL filter for places worth showing on the zoomed-out map and in the
    sitemap: shops with a code, plus every public restroom."""
    return or_(has_codes_clause(), CoffeeShop.category == 'restroom')


def shops_query():
    """Base shop query. It loads all codes in two extra queries in total."""
    return CoffeeShop.query.options(
        selectinload(CoffeeShop.wifi_passwords),
        selectinload(CoffeeShop.bathroom_codes),
    )


def user_votes_for(user_id, shops):
    """Return {(item_type, item_id): vote_type} for one user in one query."""
    shop_ids = [s.id for s in shops]
    if not user_id or not shop_ids:
        return {}
    rows = Vote.query.filter(Vote.user_id == user_id, Vote.shop_id.in_(shop_ids)).all()
    return {(v.item_type, v.item_id): v.vote_type for v in rows}


def parse_bbox(raw):
    """Parse 'south,west,north,east' into four floats. Return None if bad."""
    try:
        south, west, north, east = (float(x) for x in raw.split(','))
    except (AttributeError, ValueError):
        return None
    if not (-90 <= south <= north <= 90 and -180 <= west <= east <= 180):
        return None
    return south, west, north, east


def shop_response(shops, user_id):
    votes = user_votes_for(user_id, shops)
    speeds = speed_summaries([s.id for s in shops])
    response = jsonify([shop.to_dict(votes=votes, speeds=speeds) for shop in shops])
    response.set_cookie('user_id', user_id, max_age=60*60*24*365*5)  # 5 years
    return response


@app.before_request
def guard_writes():
    # Reads stay free so the map and crawler-facing surfaces aren't throttled.
    if request.method != 'POST':
        return

    # Block cross-site form posts. Browsers send Origin on every POST.
    origin = request.headers.get('Origin')
    if origin and urlparse(origin).netloc != request.host:
        return jsonify({"error": "Cross-origin writes are not allowed."}), 403

    ip = request.remote_addr or 'unknown'
    now = time.time()
    WriteEvent.query.filter(WriteEvent.ts < now - RATE_LIMIT_WINDOW).delete()
    if WriteEvent.query.filter_by(ip=ip).count() >= RATE_LIMIT_MAX_WRITES:
        db.session.commit()
        return jsonify({"error": "Too many requests. Please try again later."}), 429
    db.session.add(WriteEvent(ip=ip, ts=now))
    db.session.commit()

@app.route('/api/coffee_shops', methods=['GET'])
def get_coffee_shops():
    """List shops.

    Optional filters:
      bbox=south,west,north,east  only shops inside the map viewport
      has_codes=1                 only shops with a visible code, plus
                                  public restrooms (the zoomed-out map)
      q=text                      name or address contains the text
    With no filters it returns every shop (older cached clients use that).
    """
    user_id = get_or_set_user_id()
    query = shops_query()

    raw_bbox = request.args.get('bbox')
    if raw_bbox is not None:
        bbox = parse_bbox(raw_bbox)
        if bbox is None:
            return jsonify({"error": "bbox must be south,west,north,east"}), 400
        south, west, north, east = bbox
        query = query.filter(CoffeeShop.lat.between(south, north),
                             CoffeeShop.lng.between(west, east))

    if request.args.get('has_codes') == '1':
        query = query.filter(listed_clause())

    q = (request.args.get('q') or '').strip()
    if q:
        like = f"%{q}%"
        query = query.filter(or_(CoffeeShop.name.ilike(like), CoffeeShop.address.ilike(like)))

    if raw_bbox is not None or q:
        query = query.limit(MAX_SHOPS_PER_QUERY)

    return shop_response(query.all(), user_id)

@app.route('/api/coffee_shops/<int:shop_id>', methods=['GET'])
def get_coffee_shop(shop_id):
    user_id = get_or_set_user_id()
    shop = db.session.get(CoffeeShop, shop_id)
    if not shop:
        return jsonify({"error": "Shop not found"}), 404
    response = jsonify(shop.to_dict(votes=user_votes_for(user_id, [shop])))
    response.set_cookie('user_id', user_id, max_age=60*60*24*365*5) # 5 years
    return response

def listed_shops():
    """Shops with a code plus public restrooms, sorted by name."""
    return shops_query().filter(listed_clause()).order_by(CoffeeShop.name).all()


def group_by_neighborhood(shops):
    """Return {slug: [shops]} for shops that fall inside a neighborhood."""
    groups = {}
    for shop in shops:
        hood = shop.neighborhood
        if hood:
            groups.setdefault(hood['slug'], []).append(shop)
    return groups


@app.route('/')
def index():
    # The sidebar starts with real links to every shop page that has a
    # code. Crawlers follow them; the map script replaces the list later.
    coded = shops_query().filter(has_codes_clause()).order_by(CoffeeShop.name).all()
    return render_template('index.html', coded_shops=coded, canonical=site_url('/'))

@app.route('/sw.js')
def service_worker():
    response = make_response(send_from_directory('static', 'sw.js'))
    response.headers['Content-Type'] = 'application/javascript'
    # Never let the SW script itself be HTTP-cached, so browsers always see a
    # new sw.js on deploy and can update the worker (and self-heal stale users).
    response.headers['Cache-Control'] = 'no-cache'
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
    shop = db.session.get(CoffeeShop, shop_id)
    if not shop:
        abort(404)
    return shop


@app.route('/shops')
def shops_directory():
    """Server-rendered, crawlable directory of every shop that has a code."""
    shops = shops_query().filter(has_codes_clause()).order_by(CoffeeShop.name).all()
    return render_template('shops.html', shops=shops, canonical=site_url('/shops'),
                           groups=group_by_neighborhood(listed_shops()))


@app.route('/seattle')
def neighborhood_index():
    """Landing page that links to every neighborhood page."""
    groups = group_by_neighborhood(listed_shops())
    hoods = [neighborhoods.BY_SLUG[slug] | {'count': len(groups[slug])}
             for slug, *_ in neighborhoods.NEIGHBORHOODS if slug in groups]
    return render_template('neighborhoods.html', hoods=hoods,
                           canonical=site_url('/seattle'))


@app.route('/seattle/<slug>')
def neighborhood_detail(slug):
    """For example /seattle/capitol-hill: bathroom codes, Wi-Fi passwords and
    public restrooms in one neighborhood."""
    hood = neighborhoods.BY_SLUG.get(slug)
    if not hood:
        abort(404)
    shops = group_by_neighborhood(listed_shops()).get(slug, [])
    if not shops:
        abort(404)
    coded = [s for s in shops if s.has_codes() and not s.is_restroom]
    restrooms = [s for s in shops if s.is_restroom]
    speeds = speed_summaries([s.id for s in coded])
    return render_template('neighborhood.html', hood=hood, coded=coded,
                           restrooms=restrooms, speeds=speeds,
                           canonical=site_url(url_for('neighborhood_detail', slug=slug)))


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
    hood = shop.neighborhood
    speed = speed_summaries([shop.id]).get(shop.id)

    # "More in <neighborhood>": internal links help crawlers find pages.
    nearby = []
    if hood:
        nearby = [s for s in group_by_neighborhood(listed_shops()).get(hood['slug'], [])
                  if s.id != shop.id]
        nearby.sort(key=lambda s: (not s.has_codes(), neighborhoods.distance_km(
            shop.lat, shop.lng, s.lat, s.lng)))
        nearby = nearby[:8]

    additional = (
        [{"@type": "PropertyValue", "name": "Wi-Fi password",
          "value": wp.password, "description": f"{wp.votes} community votes"} for wp in wifi]
        + [{"@type": "PropertyValue", "name": "Bathroom code",
            "value": bc.code, "description": f"{bc.votes} community votes"} for bc in bathroom]
    )
    jsonld = {
        "@context": "https://schema.org",
        "@type": "Place" if shop.is_restroom else "CafeOrCoffeeShop",
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
    if shop.tags.get('opening_hours'):
        jsonld["openingHours"] = shop.tags['opening_hours']

    return render_template('shop.html', shop=shop, canonical=canonical,
                           wifi=wifi, bathroom=bathroom, jsonld=jsonld,
                           hood=hood, nearby=nearby, speed=speed,
                           last_confirmed=shop.last_confirmed())


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
        lines += [f"  {i}. {wp.password}" + (f"  (network: {wp.ssid})" if wp.ssid else "")
                  + f"  (votes: {wp.votes})" for i, wp in enumerate(wifi, 1)]
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

    like = f"%{q}%"
    matches = (shops_query()
               .filter(or_(CoffeeShop.name.ilike(like), CoffeeShop.address.ilike(like)))
               .filter(has_codes_clause())
               .limit(20).all())

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
    shops = listed_shops()
    groups = group_by_neighborhood(shops)
    urls = [(site_url('/'), None), (site_url('/shops'), None), (site_url('/seattle'), None)]
    urls += [(site_url(url_for('neighborhood_detail', slug=slug)), None)
             for slug, *_ in neighborhoods.NEIGHBORHOODS if slug in groups]
    for s in sorted(shops, key=lambda s: s.id):
        urls.append((site_url(url_for('shop_detail', shop_id=s.id, slug=s.slug)),
                     s.last_confirmed()))

    body = ['<?xml version="1.0" encoding="UTF-8"?>',
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for u, lastmod in urls:
        mod = f'<lastmod>{lastmod.date().isoformat()}</lastmod>' if lastmod else ''
        body.append(f'  <url><loc>{u}</loc>{mod}<changefreq>weekly</changefreq></url>')
    body.append('</urlset>')
    return Response("\n".join(body), mimetype='application/xml')


@app.route('/llms.txt')
def llms_txt():
    """The /llms.txt convention: a concise, Markdown guide for LLMs."""
    base = site_url('')
    n_with_codes = CoffeeShop.query.filter(has_codes_clause()).count()
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
- [Neighborhoods]({base}/seattle): bathroom codes, Wi-Fi passwords and public
  restrooms grouped by Seattle neighborhood, for example `{base}/seattle/capitol-hill`.
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
    data = request.get_json(silent=True) or {}
    shop_id = data.get('shop_id')
    item_type = data.get('item_type')
    vote_type = data.get('vote_type')  # This is the new vote being cast
    item_id = data.get('item_id')
    if vote_type not in ('upvote', 'downvote') or not isinstance(item_id, int) \
            or not isinstance(shop_id, int):
        return jsonify({"error": "Invalid vote."}), 400

    user_id = get_or_set_user_id()

    shop = db.session.get(CoffeeShop, shop_id)
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
    
    found_item = db.session.get(Model, item_id)
    if not found_item or found_item.coffee_shop_id != shop_id:
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
                found_item.last_confirmed_at = utcnow()
            else: # Was upvote, now downvote
                found_item.votes -= 2
    else:
        # New vote
        new_vote = Vote(user_id=user_id, shop_id=shop_id, item_id=item_id, item_type=item_type_str, vote_type=vote_type)
        db.session.add(new_vote)
        if vote_type == 'upvote':
            found_item.votes += 1
            found_item.last_confirmed_at = utcnow()
        else: # downvote
            found_item.votes -= 1

    db.session.commit()
    db.session.refresh(shop)

    response = jsonify(shop.to_dict(votes=user_votes_for(user_id, [shop])))
    response.set_cookie('user_id', user_id, max_age=60*60*24*365*5) # 5 years
    return response

@app.route('/api/suggest', methods=['POST'])
def suggest():
    data = request.get_json(silent=True) or {}
    shop_id = data.get('shop_id')
    item_type = data.get('item_type')
    item_value = data.get('item_value')
    ssid = data.get('ssid')
    if not isinstance(shop_id, int) or not isinstance(item_value, str) or not item_value:
        return jsonify({"error": "Invalid suggestion."}), 400
    ssid = ssid.strip() if isinstance(ssid, str) else ''
    if len(ssid) > SSID_MAX:
        return jsonify({"error": f"Network name cannot be longer than {SSID_MAX} characters."}), 400

    user_id = get_or_set_user_id()

    shop = db.session.get(CoffeeShop, shop_id)
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

    if existing_item and existing_item.votes > HIDE_AT_VOTES:
        # The password is already listed. Accept a network name it lacks.
        if item_type == 'wifi_passwords' and ssid and not existing_item.ssid:
            existing_item.ssid = ssid
            db.session.commit()
            return jsonify({"shop": shop.to_dict(votes=user_votes_for(user_id, [shop])),
                            "newItemId": existing_item.id})
        return jsonify({"error": "This suggestion is already active or has too many downvotes."}), 409

    now = utcnow()
    if existing_item:
        new_item = existing_item
        new_item.votes = 1 # Reset votes if re-activating a previously downvoted item
    else:
        if item_type == 'wifi_passwords':
            new_item = Model(password=item_value, coffee_shop=shop, votes=0)
        else:
            new_item = Model(code=item_value, coffee_shop=shop, votes=0)
        new_item.created_at = now
        db.session.add(new_item)
    if item_type == 'wifi_passwords' and ssid:
        new_item.ssid = ssid
    new_item.last_confirmed_at = now
    
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
        "shop": shop.to_dict(votes=user_votes_for(user_id, [shop])),
        "newItemId": new_item.id
    }
    response = jsonify(response_data)
    response.set_cookie('user_id', user_id, max_age=60*60*24*365*5) # 5 years
    return response

@app.route('/api/wifi_ssid', methods=['POST'])
def set_wifi_ssid():
    """Add the network name to a listed password that has none yet."""
    data = request.get_json(silent=True) or {}
    shop_id, item_id, ssid = data.get('shop_id'), data.get('item_id'), data.get('ssid')
    ssid = ssid.strip() if isinstance(ssid, str) else ''
    if not isinstance(shop_id, int) or not isinstance(item_id, int) or not 0 < len(ssid) <= SSID_MAX:
        return jsonify({"error": f"Enter a network name of 1 to {SSID_MAX} characters."}), 400
    item = db.session.get(WifiPassword, item_id)
    if not item or item.coffee_shop_id != shop_id:
        return jsonify({"error": "Item not found"}), 404
    if item.ssid:
        return jsonify({"error": "This password already has a network name."}), 409
    item.ssid = ssid
    db.session.commit()
    user_id = get_or_set_user_id()
    shop = db.session.get(CoffeeShop, shop_id)
    response = jsonify(shop.to_dict(votes=user_votes_for(user_id, [shop])))
    response.set_cookie('user_id', user_id, max_age=60*60*24*365*5)
    return response


@app.route('/api/ping')
def api_ping():
    """Tiny response for the speed test latency check."""
    response = make_response('', 204)
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.route('/api/speedtest/download', methods=['POST'])
def speedtest_download():
    """Random bytes for the speed test. POST, so the write guard rate-limits
    it and other sites cannot use it to burn bandwidth."""
    try:
        mb = int(request.args.get('mb', 1))
    except ValueError:
        mb = 1
    mb = max(1, min(SPEED_TEST_MAX_MB, mb))
    response = Response(os.urandom(mb * 1024 * 1024), mimetype='application/octet-stream')
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.route('/api/speedtest', methods=['POST'])
def speedtest_result():
    """Save a speed test result. The visitor must be at the shop."""
    data = request.get_json(silent=True) or {}
    shop_id, mbps, ping_ms = data.get('shop_id'), data.get('mbps'), data.get('ping_ms')
    lat, lng = data.get('lat'), data.get('lng')
    numbers = (mbps, lat, lng) + ((ping_ms,) if ping_ms is not None else ())
    if not isinstance(shop_id, int) or not all(isinstance(n, (int, float)) for n in numbers):
        return jsonify({"error": "Invalid speed test."}), 400
    if not 0 < mbps < 5000 or (ping_ms is not None and not 0 <= ping_ms < 10000):
        return jsonify({"error": "Invalid speed test."}), 400

    shop = db.session.get(CoffeeShop, shop_id)
    if not shop:
        return jsonify({"error": "Shop not found"}), 404
    if neighborhoods.distance_km(lat, lng, shop.lat, shop.lng) * 1000 > SPEED_MAX_DISTANCE_M:
        return jsonify({"error": "You need to be at the shop to test its Wi-Fi."}), 400

    user_id = get_or_set_user_id()
    recent = SpeedTest.query.filter(
        SpeedTest.shop_id == shop_id, SpeedTest.user_id == user_id,
        SpeedTest.created_at >= utcnow() - timedelta(hours=1)).first()
    if recent:
        return jsonify({"error": "You tested this shop in the last hour. Thanks!"}), 409

    if data.get('connection') in CELLULAR_TYPES:
        return jsonify({"error": "Join the cafe's Wi-Fi first. This test does not run on cellular data."}), 400

    db.session.add(SpeedTest(shop_id=shop_id, user_id=user_id, mbps=float(mbps),
                             ip_hash=hash_ip(request.remote_addr or ''),
                             ping_ms=float(ping_ms) if ping_ms is not None else None,
                             created_at=utcnow()))
    db.session.commit()
    response = jsonify(shop.to_dict(votes=user_votes_for(user_id, [shop])))
    response.set_cookie('user_id', user_id, max_age=60*60*24*365*5)
    return response


# Columns added after the first release. SQLite cannot add them through
# create_all(), so ensure_schema() adds any that are missing.
ADDED_COLUMNS = {
    'coffee_shop': [
        ('category', "VARCHAR(16) NOT NULL DEFAULT 'shop'"),
        ('osm_id', 'VARCHAR(32)'),
        ('osm_seen_at', 'DATETIME'),
        ('tags_json', 'TEXT'),
    ],
    'wifi_password': [('created_at', 'DATETIME'), ('last_confirmed_at', 'DATETIME'),
                      ('ssid', 'VARCHAR(32)')],
    'speed_test': [('ip_hash', 'VARCHAR(16)')],
    'bathroom_code': [('created_at', 'DATETIME'), ('last_confirmed_at', 'DATETIME')],
}


def ensure_schema():
    """Create missing tables (write_event) and the indexes the map queries use.
    Existing tables are left as they are."""
    db.create_all()
    for table, columns in ADDED_COLUMNS.items():
        have = {row[1] for row in db.session.execute(text(f'PRAGMA table_info({table})'))}
        for name, ddl in columns:
            if name not in have:
                db.session.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {ddl}'))
    db.session.execute(text(
        'CREATE UNIQUE INDEX IF NOT EXISTS ux_coffee_shop_osm_id ON coffee_shop (osm_id) '
        'WHERE osm_id IS NOT NULL'))
    db.session.execute(text(
        'CREATE INDEX IF NOT EXISTS ix_coffee_shop_lat_lng ON coffee_shop (lat, lng)'))
    db.session.execute(text(
        'CREATE INDEX IF NOT EXISTS ix_vote_user_shop ON vote (user_id, shop_id)'))
    db.session.commit()


with app.app_context():
    ensure_schema()


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))

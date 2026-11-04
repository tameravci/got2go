import os
import tempfile

import pytest

# Point the app at a throwaway database before it is imported.
_tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
os.environ['DATABASE_PATH'] = _tmp.name

import app as app_module  # noqa: E402
from app import app, db, CoffeeShop, WifiPassword, BathroomCode, WriteEvent  # noqa: E402


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.app_context():
        db.drop_all()
        app_module.ensure_schema()
        coded = CoffeeShop(id=1, name='Victrola', address='Pike St', lat=47.614, lng=-122.32)
        empty = CoffeeShop(id=2, name='Empty Cafe', address='Pine St', lat=47.615, lng=-122.33)
        far = CoffeeShop(id=3, name='Far Cafe', address='Ballard', lat=47.668, lng=-122.38)
        db.session.add_all([coded, empty, far])
        db.session.add(WifiPassword(id=10, password='coffee123', votes=2, coffee_shop=coded))
        db.session.add(BathroomCode(id=20, code='1234', votes=1, coffee_shop=coded))
        db.session.add(BathroomCode(id=21, code='9999', votes=-3, coffee_shop=far))
        db.session.commit()
    with app.test_client() as c:
        yield c


def ids(resp):
    return sorted(s['id'] for s in resp.get_json())


def test_all_shops_without_filters(client):
    assert ids(client.get('/api/coffee_shops')) == [1, 2, 3]


def test_bbox_filter(client):
    r = client.get('/api/coffee_shops?bbox=47.60,-122.35,47.62,-122.30')
    assert ids(r) == [1, 2]


def test_has_codes_ignores_hidden_codes(client):
    # Shop 3 only has a code at -3 votes, which is hidden.
    assert ids(client.get('/api/coffee_shops?has_codes=1')) == [1]


def test_bad_bbox(client):
    assert client.get('/api/coffee_shops?bbox=1,2,3').status_code == 400


def test_search(client):
    assert ids(client.get('/api/coffee_shops?q=pine')) == [2]


def vote(client, vote_type, item_id=10, shop_id=1, headers=None):
    return client.post('/api/vote', json={
        'shop_id': shop_id, 'item_type': 'wifi_passwords',
        'vote_type': vote_type, 'item_id': item_id}, headers=headers or {})


def wifi_votes(resp):
    return resp.get_json()['wifi_passwords'][0]


def test_vote_cycle(client):
    w = wifi_votes(vote(client, 'upvote'))
    assert (w['votes'], w['user_vote']) == (3, 'upvote')
    w = wifi_votes(vote(client, 'downvote'))
    assert (w['votes'], w['user_vote']) == (1, 'downvote')
    w = wifi_votes(vote(client, 'downvote'))  # same vote again undoes it
    assert (w['votes'], w['user_vote']) == (2, None)


def test_vote_rejects_item_from_other_shop(client):
    assert vote(client, 'upvote', item_id=10, shop_id=2).status_code == 404


def test_vote_rejects_bad_vote_type(client):
    assert vote(client, 'sideways').status_code == 400


def test_suggest_bathroom_code(client):
    r = client.post('/api/suggest', json={
        'shop_id': 2, 'item_type': 'bathroom_codes', 'item_value': '42#'})
    assert r.status_code == 200
    code = r.get_json()['shop']['bathroom_codes'][0]
    assert (code['code'], code['votes'], code['user_vote']) == ('42#', 1, 'upvote')


def test_cross_origin_post_blocked(client):
    r = vote(client, 'upvote', headers={'Origin': 'https://evil.example'})
    assert r.status_code == 403


def test_same_origin_post_allowed(client):
    r = vote(client, 'upvote', headers={'Origin': 'http://localhost'})
    assert r.status_code == 200


def test_rate_limit(client, monkeypatch):
    monkeypatch.setattr(app_module, 'RATE_LIMIT_MAX_WRITES', 3)
    codes = [vote(client, 'upvote').status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]


def test_rate_limit_prunes_old_rows(client, monkeypatch):
    with app.app_context():
        db.session.add(WriteEvent(ip='127.0.0.1', ts=0))
        db.session.commit()
    vote(client, 'upvote')
    with app.app_context():
        assert WriteEvent.query.filter(WriteEvent.ts == 0).count() == 0


# ---- Freshness ---------------------------------------------------------

def test_upvote_sets_last_confirmed(client):
    w = wifi_votes(vote(client, 'upvote'))
    assert w['last_confirmed_at'] is not None


def test_suggest_sets_timestamps(client):
    r = client.post('/api/suggest', json={
        'shop_id': 2, 'item_type': 'wifi_passwords', 'item_value': 'latte42'})
    w = r.get_json()['shop']['wifi_passwords'][0]
    assert w['created_at'] and w['last_confirmed_at']


# ---- Restrooms -----------------------------------------------------------

def add_restroom():
    with app.app_context():
        db.session.add(CoffeeShop(id=4, name='Public restroom', address='Capitol Hill, Seattle, WA',
                                  lat=47.6250, lng=-122.3200, category='restroom',
                                  tags_json='{"amenity": "toilets", "wheelchair": "yes"}'))
        db.session.commit()


def test_restroom_shows_when_zoomed_out(client):
    add_restroom()
    r = client.get('/api/coffee_shops?has_codes=1')
    assert ids(r) == [1, 4]
    restroom = [s for s in r.get_json() if s['id'] == 4][0]
    assert restroom['category'] == 'restroom'
    assert restroom['tags']['wheelchair'] == 'yes'
    assert restroom['neighborhood'] == 'Capitol Hill'


# ---- SEO -----------------------------------------------------------------

def test_canonical_is_https_apex_on_production_host(client):
    r = client.get('/shops', base_url='http://got2gosea.com')
    assert b'<link rel="canonical" href="https://got2gosea.com/shops">' in r.data
    r = client.get('/sitemap.xml', base_url='http://www.got2gosea.com')
    assert b'http://' not in r.data.replace(b'http://www.sitemaps.org', b'')


def test_homepage_links_to_shop_pages(client):
    r = client.get('/')
    assert b'href="/shop/1/victrola"' in r.data
    assert b'href="/shop/2/' not in r.data  # no codes, no link


def test_shop_title_matches_search_pattern(client):
    r = client.get('/shop/1/victrola')
    assert b'<title>Victrola Wi-Fi password &amp; bathroom code (Capitol Hill, Seattle)' in r.data
    assert b'worked' in r.data or b'votes' in r.data


def test_neighborhood_pages(client):
    add_restroom()
    r = client.get('/seattle/capitol-hill')
    assert r.status_code == 200
    assert b'Victrola' in r.data and b'Public restroom' in r.data
    assert client.get('/seattle/nowhere').status_code == 404
    assert client.get('/seattle/ballard').status_code == 404  # no listed shops there
    assert b'/seattle/capitol-hill' in client.get('/seattle').data
    assert b'/seattle/capitol-hill' in client.get('/sitemap.xml').data


# ---- Speed test ----------------------------------------------------------

def speed(client, **overrides):
    body = {'shop_id': 1, 'mbps': 42.5, 'ping_ms': 18, 'lat': 47.614, 'lng': -122.32}
    body.update(overrides)
    return client.post('/api/speedtest', json=body)


def test_speed_test_saved_and_summarized(client):
    r = speed(client)
    assert r.status_code == 200
    assert r.get_json()['wifi_speed']['mbps'] == 42.5
    assert r.get_json()['wifi_speed']['tests'] == 1


def test_speed_test_requires_being_at_shop(client):
    assert speed(client, lat=47.70, lng=-122.32).status_code == 400


def test_speed_test_once_per_hour(client):
    assert speed(client).status_code == 200
    assert speed(client).status_code == 409


def test_speed_test_rejects_bad_numbers(client):
    assert speed(client, mbps=-1).status_code == 400
    assert speed(client, mbps='fast').status_code == 400


def test_speed_download_size_capped(client):
    r = client.post('/api/speedtest/download?mb=99')
    assert len(r.data) == 5 * 1024 * 1024


# ---- Schema upgrade ------------------------------------------------------

def test_ensure_schema_adds_missing_columns(client):
    from sqlalchemy import text
    with app.app_context():
        db.session.execute(text('DROP TABLE wifi_password'))
        db.session.execute(text(
            'CREATE TABLE wifi_password (id INTEGER PRIMARY KEY, password VARCHAR(100) NOT NULL, '
            'votes INTEGER, coffee_shop_id INTEGER NOT NULL)'))
        db.session.commit()
        app_module.ensure_schema()
        cols = {row[1] for row in db.session.execute(text('PRAGMA table_info(wifi_password)'))}
        assert {'created_at', 'last_confirmed_at'} <= cols


# ---- OSM sync ------------------------------------------------------------

def test_osm_sync_links_adds_and_never_deletes(client):
    import populate_db
    elements = [
        # Matches the existing Victrola row by name and position.
        {'type': 'node', 'id': 1, 'lat': 47.6141, 'lon': -122.3201,
         'tags': {'name': 'Victrola', 'amenity': 'cafe', 'addr:street': 'Pike St'}},
        # A new cafe drawn as a building (way with a center point).
        {'type': 'way', 'id': 2, 'center': {'lat': 47.62, 'lon': -122.31},
         'tags': {'name': 'New Cafe', 'amenity': 'cafe'}},
        # A public restroom with no name.
        {'type': 'node', 'id': 3, 'lat': 47.6250, 'lon': -122.3200,
         'tags': {'amenity': 'toilets', 'wheelchair': 'yes'}},
        # A private toilet: skipped.
        {'type': 'node', 'id': 4, 'lat': 47.6, 'lon': -122.3,
         'tags': {'amenity': 'toilets', 'access': 'private'}},
    ]
    with app.app_context():
        assert populate_db.sync(elements) == 0
        assert db.session.get(CoffeeShop, 1).osm_id == 'node/1'
        assert len(db.session.get(CoffeeShop, 1).wifi_passwords) == 1  # codes kept
        assert CoffeeShop.query.filter_by(osm_id='way/2').one().name == 'New Cafe'
        restroom = CoffeeShop.query.filter_by(osm_id='node/3').one()
        assert (restroom.category, restroom.address) == ('restroom', 'Capitol Hill, Seattle, WA')
        assert CoffeeShop.query.filter_by(osm_id='node/4').count() == 0
        assert db.session.get(CoffeeShop, 3) is not None  # not in OSM, still kept

        # A second run with the same data changes nothing.
        before = CoffeeShop.query.count()
        assert populate_db.sync(elements) == 0
        assert CoffeeShop.query.count() == before


def test_osm_sync_refuses_tiny_response(client):
    import populate_db
    with app.app_context():
        for i in (1, 2, 3):
            db.session.get(CoffeeShop, i).osm_id = f'node/{i}'
        db.session.commit()
        assert populate_db.sync([]) == 1


# ---- Network name (SSID) -------------------------------------------------

def test_suggest_with_ssid(client):
    r = client.post('/api/suggest', json={'shop_id': 2, 'item_type': 'wifi_passwords',
                                          'item_value': 'pw123', 'ssid': ' EmptyGuest '})
    assert r.get_json()['shop']['wifi_passwords'][0]['ssid'] == 'EmptyGuest'


def test_suggest_existing_password_adds_missing_ssid(client):
    r = client.post('/api/suggest', json={'shop_id': 1, 'item_type': 'wifi_passwords',
                                          'item_value': 'coffee123', 'ssid': 'VictrolaGuest'})
    assert r.status_code == 200
    assert r.get_json()['shop']['wifi_passwords'][0]['ssid'] == 'VictrolaGuest'


def test_set_ssid_once(client):
    r = client.post('/api/wifi_ssid', json={'shop_id': 1, 'item_id': 10, 'ssid': 'VictrolaGuest'})
    assert r.status_code == 200 and r.get_json()['wifi_passwords'][0]['ssid'] == 'VictrolaGuest'
    r = client.post('/api/wifi_ssid', json={'shop_id': 1, 'item_id': 10, 'ssid': 'Other'})
    assert r.status_code == 409
    assert client.post('/api/wifi_ssid', json={'shop_id': 2, 'item_id': 10, 'ssid': 'X'}).status_code == 404
    assert client.post('/api/wifi_ssid', json={'shop_id': 1, 'item_id': 10, 'ssid': 'x' * 33}).status_code == 400


# ---- Cellular filtering ----------------------------------------------------

def test_speed_test_rejects_cellular(client):
    assert speed(client, connection='cellular').status_code == 400
    assert speed(client, connection='wifi').status_code == 200


def test_speed_summary_keeps_most_common_network(client):
    from app import SpeedTest, utcnow, speed_summaries
    with app.app_context():
        for i, (mbps, ip) in enumerate([(50, 'cafe'), (55, 'cafe'), (60, 'cafe'), (300, 'lte')]):
            db.session.add(SpeedTest(shop_id=1, user_id=f'u{i}', mbps=mbps,
                                     created_at=utcnow(), ip_hash=ip))
        db.session.commit()
        summary = speed_summaries([1])[1]
        assert (summary['mbps'], summary['tests']) == (55, 3)


# ---- No code needed / open network ---------------------------------------

def test_suggest_no_code_bathroom(client):
    r = client.post('/api/suggest', json={'shop_id': 2, 'item_type': 'bathroom_codes', 'no_code': True})
    item = r.get_json()['shop']['bathroom_codes'][0]
    assert (item['no_code'], item['code'], item['votes']) == (True, '', 1)
    again = client.post('/api/suggest', json={'shop_id': 2, 'item_type': 'bathroom_codes', 'no_code': True})
    assert again.status_code == 409


def test_suggest_open_network_with_ssid(client):
    r = client.post('/api/suggest', json={'shop_id': 2, 'item_type': 'wifi_passwords',
                                          'no_code': True, 'ssid': 'CafeFree'})
    item = r.get_json()['shop']['wifi_passwords'][0]
    assert (item['no_code'], item['ssid']) == (True, 'CafeFree')
    assert b'Open network (no password)' in client.get('/shop/2/x', follow_redirects=True).data
    assert b'Open network (no password)' in client.get('/shop/2.txt').data


def test_typed_no_password_is_rejected_with_hint(client):
    r = client.post('/api/suggest', json={'shop_id': 2, 'item_type': 'wifi_passwords',
                                          'item_value': 'no-password'})
    assert r.status_code == 400 and 'No password' in r.get_json()['error']


def test_convert_placeholders(client):
    import convert_placeholders
    with app.app_context():
        db.session.add(BathroomCode(id=30, code='0000', votes=3, coffee_shop_id=2))
        db.session.add(WifiPassword(id=31, password='no-password', votes=2, coffee_shop_id=2))
        db.session.add(WifiPassword(id=32, password='<open-network>', votes=1, coffee_shop_id=2))
        db.session.commit()
        n = convert_placeholders.convert(BathroomCode, 'code', lambda v: v == '0000', 'bathroom_code')
        n += convert_placeholders.convert(
            WifiPassword, 'password', lambda v: v.lower() in app_module.NO_PASSWORD_WORDS, 'wifi_password')
        db.session.commit()
        assert n == 3
        bc = db.session.get(BathroomCode, 30)
        assert (bc.no_code, bc.code, bc.votes) == (True, '', 3)
        # The two open-network rows at one shop merge into one, votes added.
        open_rows = WifiPassword.query.filter_by(coffee_shop_id=2, no_code=True).all()
        assert len(open_rows) == 1 and open_rows[0].votes == 3
        # Real codes stay as they are.
        assert db.session.get(BathroomCode, 20).code == '1234'

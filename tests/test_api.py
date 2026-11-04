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

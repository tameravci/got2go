from flask import Flask, jsonify, request, render_template, session, make_response
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
import os
import uuid

app = Flask(__name__)
CORS(app)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'a_very_secret_key_that_should_be_in_env_vars')

app = Flask(__name__)
CORS(app)

# Database Configuration
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'app.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
migrate = Migrate(app, db)

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

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'address': self.address,
            'lat': self.lat,
            'lng': self.lng,
            'wifi_passwords': [wp.to_dict() for wp in sorted(self.wifi_passwords, key=lambda x: x.votes, reverse=True) if wp.votes > 0],
            'bathroom_codes': [bc.to_dict() for bc in sorted(self.bathroom_codes, key=lambda x: x.votes, reverse=True) if bc.votes > 0]
        }

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

# In-memory data store for IP-based vote tracking (will be cleared on server restart)
votes_db = {}

@app.route('/api/coffee_shops', methods=['GET'])
def get_coffee_shops():
    user_id = request.cookies.get('user_id', 'N/A')
    print(f"API Call: /api/coffee_shops - IP: {request.remote_addr}, User ID Cookie: {user_id}")
    coffee_shops = CoffeeShop.query.all()
    return jsonify([shop.to_dict() for shop in coffee_shops])

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/vote', methods=['POST'])
def vote():
    data = request.get_json()
    shop_id = data['shop_id']
    item_type = data['item_type']
    vote_type = data['vote_type']
    
    user_id = get_or_set_user_id()
    user_ip = request.remote_addr
    user_identifier = f"{user_ip}_{user_id}"
    
    item_id = data.get('item_id')

    print(f"API Call: /api/vote - IP: {request.remote_addr}, User ID Cookie: {user_id}")
    print(f"Voting for shop {shop_id} with item type {item_type} and vote type {vote_type}")
    print(f"User Identifier: {user_identifier}")
    print(f"Item ID: {item_id}")

    shop = CoffeeShop.query.get(shop_id)
    if not shop:
        return jsonify({"error": "Shop not found"}), 404

    if item_type == 'wifi_passwords':
        Model = WifiPassword
    elif item_type == 'bathroom_codes':
        Model = BathroomCode
    else:
        return jsonify({"error": "Invalid item type"}), 400
    
    found_item = Model.query.get(item_id)
    if not found_item:
        return jsonify({"error": "Item not found"}), 404

    if vote_type == 'upvote':
        vote_prefix = f"{user_identifier}_{shop_id}_{item_type}_"
        for key, value in list(votes_db.items()):
            if key.startswith(vote_prefix) and value == 'upvote':
                existing_item_id_str = key.split('_')[-1]
                if existing_item_id_str.isdigit():
                    existing_item_id = int(existing_item_id_str)
                    if existing_item_id != item_id:
                        old_item = Model.query.get(existing_item_id)
                        if old_item:
                            old_item.votes -= 1
                        del votes_db[key]

    vote_key = f"{user_identifier}_{shop_id}_{item_type}_{item_id}"
    previous_vote = votes_db.get(vote_key)

    if previous_vote == vote_type:
        # Undo vote
        if vote_type == 'upvote':
            found_item.votes -= 1
        else: # downvote
            found_item.votes += 1
        del votes_db[vote_key]
    elif previous_vote:
        # Change vote
        if vote_type == 'upvote': # Was a downvote, now an upvote
            found_item.votes += 2
        else: # Was an upvote, now a downvote
            found_item.votes -= 2
        votes_db[vote_key] = vote_type
    else:
        # New vote
        if vote_type == 'upvote':
            found_item.votes += 1
        else: # downvote
            found_item.votes -= 1
        votes_db[vote_key] = vote_type

    db.session.commit()
    
    response = jsonify(shop.to_dict())
    response.set_cookie('user_id', user_id, max_age=60*60*24*365*5) # 5 years
    return response

@app.route('/api/suggest', methods=['POST'])
def suggest():
    data = request.get_json()
    shop_id = data['shop_id']
    item_type = data['item_type']
    item_value = data['item_value']
    
    user_id = get_or_set_user_id()
    user_ip = request.remote_addr
    user_identifier = f"{user_ip}_{user_id}"

    print(f"API Call: /api/suggest - IP: {request.remote_addr}, User ID Cookie: {user_id}")

    shop = CoffeeShop.query.get(shop_id)
    if not shop:
        return jsonify({"error": "Shop not found"}), 404

    if item_type == 'wifi_passwords':
        Model = WifiPassword
        existing_item = Model.query.filter_by(coffee_shop_id=shop_id, password=item_value).first()
    elif item_type == 'bathroom_codes':
        Model = BathroomCode
        existing_item = Model.query.filter_by(coffee_shop_id=shop_id, code=item_value).first()
    else:
        return jsonify({"error": "Invalid item type"}), 400

    if existing_item and existing_item.votes > 0:
        return jsonify({"error": "This suggestion is already active."}), 409

    vote_prefix = f"{user_identifier}_{shop_id}_{item_type}_"
    for key, value in list(votes_db.items()):
        if key.startswith(vote_prefix) and value == 'upvote':
            existing_item_id_str = key.split('_')[-1]
            if existing_item_id_str.isdigit():
                existing_item_id = int(existing_item_id_str)
                if existing_item and existing_item_id == existing_item.id:
                    continue
                old_item = Model.query.get(existing_item_id)
                if old_item:
                    old_item.votes -= 1
                del votes_db[key]

    if existing_item:
        new_item = existing_item
        new_item.votes = 1
    else:
        if item_type == 'wifi_passwords':
            new_item = Model(password=item_value, coffee_shop=shop, votes=1)
        else:
            new_item = Model(code=item_value, coffee_shop=shop, votes=1)
        db.session.add(new_item)
    
    db.session.commit()

    vote_key = f"{user_identifier}_{shop_id}_{item_type}_{new_item.id}"
    votes_db[vote_key] = 'upvote'

    db.session.refresh(shop)

    response_data = {
        "shop": shop.to_dict(),
        "newItemId": new_item.id
    }
    response = jsonify(response_data)
    response.set_cookie('user_id', user_id, max_age=60*60*24*365*5) # 5 years
    return response

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=8080)

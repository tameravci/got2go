from flask import Flask, jsonify, request, render_template
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
import os

app = Flask(__name__)
CORS(app)

# Database Configuration
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'app.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
migrate = Migrate(app, db)

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
    coffee_shops = CoffeeShop.query.all()
    return jsonify([shop.to_dict() for shop in coffee_shops])

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/vote', methods=['POST'])
def vote():
    data = request.get_json()
    shop_id = data['shop_id']
    item_type = data['item_type'] # 'wifi_passwords' or 'bathroom_codes'
    item_value = data['item_value']
    vote_type = data['vote_type'] # 'upvote' or 'downvote'
    user_ip = request.remote_addr

    vote_key = f"{user_ip}_{shop_id}_{item_type}_{item_value}"

    shop = CoffeeShop.query.get(shop_id)
    if not shop:
        return jsonify({"error": "Shop not found"}), 404

    if item_type == 'wifi_passwords':
        item_list = shop.wifi_passwords
        found_item = next((item for item in item_list if item.password == item_value), None)
    elif item_type == 'bathroom_codes':
        item_list = shop.bathroom_codes
        found_item = next((item for item in item_list if item.code == item_value), None)
    else:
        return jsonify({"error": "Invalid item type"}), 400

    if not found_item:
        return jsonify({"error": "Item not found"}), 404

    # Check if user has already voted on this item
    if vote_key in votes_db:
        if votes_db[vote_key] == vote_type: # Same vote type, so it's an undo
            if vote_type == 'upvote':
                found_item.votes -= 1
            else:
                found_item.votes += 1
            del votes_db[vote_key]
            db.session.commit()
            return jsonify(shop.to_dict())
        else: # Different vote type, prevent changing vote directly
            return jsonify({"error": "You have already voted on this item. Please undo your previous vote first."}), 429
    else: # No previous vote, proceed with new vote
        if vote_type == 'upvote':
            found_item.votes += 1
        else:
            found_item.votes -= 1
        votes_db[vote_key] = vote_type
        db.session.commit()
        return jsonify(shop.to_dict())

@app.route('/api/suggest', methods=['POST'])
def suggest():
    data = request.get_json()
    shop_id = data['shop_id']
    item_type = data['item_type']
    item_value = data['item_value']
    user_ip = request.remote_addr

    shop = CoffeeShop.query.get(shop_id)
    if not shop:
        return jsonify({"error": "Shop not found"}), 404

    if item_type == 'wifi_passwords':
        # Check if the suggestion already exists
        existing_item = WifiPassword.query.filter_by(coffee_shop_id=shop_id, password=item_value).first()
        if existing_item:
            return jsonify({"error": "This suggestion already exists."}), 409
        new_item = WifiPassword(password=item_value, coffee_shop=shop)
    elif item_type == 'bathroom_codes':
        # Check if the suggestion already exists
        existing_item = BathroomCode.query.filter_by(coffee_shop_id=shop_id, code=item_value).first()
        if existing_item:
            return jsonify({"error": "This suggestion already exists."}), 409
        new_item = BathroomCode(code=item_value, coffee_shop=shop)
    else:
        return jsonify({"error": "Invalid item type"}), 400

    db.session.add(new_item)
    db.session.commit()

    # Automatically register an upvote from the suggester
    vote_key = f"{user_ip}_{shop_id}_{item_type}_{item_value}"
    votes_db[vote_key] = 'upvote'

    db.session.refresh(shop) # Refresh the shop to get the new item in the list

    response_data = {
        "shop": shop.to_dict(),
        "newItemId": new_item.id
    }
    return jsonify(response_data)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5010)
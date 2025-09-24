from flask import Flask, jsonify, request, render_template, session, make_response, send_from_directory
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
import os
import time
from sqlalchemy.dialects.postgresql import UUID
import uuid
from datetime import datetime, timedelta

app = Flask(__name__)
CORS(app)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY')

# In-memory cache for the version
__version__ = str(int(time.time()))

@app.context_processor
def inject_version():
    global __version__
    return dict(version=__version__)

RATE_LIMIT_WINDOW = 600  # 10 minutes in seconds
RATE_LIMIT_MAX_REQUESTS = 10
ip_request_timestamps = {}

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

#@app.before_request
def rate_limit():
    ip = request.remote_addr
    current_time = time.time()

    if ip not in ip_request_timestamps:
        ip_request_timestamps[ip] = []

    # Remove timestamps older than the window
    ip_request_timestamps[ip] = [
        t for t in ip_request_timestamps[ip] if current_time - t < RATE_LIMIT_WINDOW
    ]

    if len(ip_request_timestamps[ip]) >= RATE_LIMIT_MAX_REQUESTS:
        return jsonify({"error": "Too many requests. Please try again later."}), 429

    ip_request_timestamps[ip].append(current_time)

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

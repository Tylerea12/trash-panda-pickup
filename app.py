from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_socketio import SocketIO, emit, join_room
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
import uuid
import random
import os
import time


# Flag for local vs production mode
IS_PRODUCTION = os.environ.get("RENDER", False)

flask_app = Flask(__name__)
flask_app.config['SECRET_KEY'] = 'raccoon-secret'

basedir = os.path.abspath(os.path.dirname(__file__))
flask_app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{os.path.join(basedir, 'instance', 'pandas.db')}"
flask_app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(flask_app)
migrate = Migrate(flask_app, db)

socketio = SocketIO(flask_app, cors_allowed_origins="*")


ROOMS = {}

TRASH_ITEMS = [
    "bottle_cap", "broken_glass", "candy_wrapper", "cardboard", "chip_bag", "chopsticks",
    "cigarette", "clothing_item", "coffee_cup", "default", "face_mask", "fast_food_wrapper",
    "flyer", "food_container", "napkin", "paper_bag", "plastic_bag", "plastic_bottle",
    "receipt", "soda_can", "straw"
]

# Models

class Game(db.Model):
    id = db.Column(db.String, primary_key=True)
    player1_id = db.Column(db.Integer, db.ForeignKey('user.id', name='fk_game_player1'))
    player2_id = db.Column(db.Integer, db.ForeignKey('user.id', name='fk_game_player2'), nullable=True)
    winner_id = db.Column(db.Integer, db.ForeignKey('user.id', name='fk_game_winner'), nullable=True)
    items = db.Column(db.String)
    time = db.Column(db.Integer)
    game_start = db.Column(db.DateTime, default=datetime.utcnow)

class ItemPickup(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    player_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    item = db.Column(db.String(50))
    count = db.Column(db.Integer, default=0)
    player = db.relationship("User", backref="pickups")


# Updated User model (replaces Player)
class User(db.Model):
    __tablename__ = 'user'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    wins = db.Column(db.Integer, default=0)
    losses = db.Column(db.Integer, default=0)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


with flask_app.app_context():
    db.create_all()

@flask_app.route('/')
def home():
    if "username" not in session:
        return redirect(url_for("login", next=request.path))

    return render_template('home.html', username=session['username'])

@flask_app.route("/register", methods=["GET", "POST"])
def register():
    next_url = request.args.get("next")

    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        existing_user = User.query.filter_by(username=username).first()

        if existing_user:
            return render_template("register.html", error="Username already taken", next=next_url)

        user = User(username=username)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        session["username"] = user.username

        return redirect(next_url or url_for("home"))

    return render_template("register.html", next=next_url)


@flask_app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]

        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            session["username"] = username
            next_page = request.form.get("next") or url_for("home")
            return redirect(next_page)

        return render_template("login.html", error="Invalid username or password.")

    return render_template("login.html")

@flask_app.before_request
def protect_routes():
    protected_routes = ["/solo-game", "/create-room", "/stats"]
    if any(request.path.startswith(p) for p in protected_routes):
        if "username" not in session:
            return redirect(url_for("login", next=request.path))

@flask_app.route("/accept-invite/<room_id>")
def accept_invite(room_id):
    if "username" not in session:
        # User isn't logged in → send to login and remember where they were going
        return redirect(url_for("login", next=f"/accept-invite/{room_id}"))

    # User is logged in → send them to the real waiting room
    return redirect(url_for("challenge_friend", room_id=room_id))


@flask_app.route('/logout')
def logout():
    session.pop('username', None)
    return redirect(url_for('login'))

@flask_app.route('/start-game')
def start_game():
    username = session.get('username')
    if not username:
        return "Username is required", 400

    user = User.query.filter_by(username=username).first()
    if not user:
        return "User not found", 404

    size = request.args.get('size', 'medium')
    time_str = request.args.get('time', '300')
    time = -1 if time_str == '-1' else int(time_str)

    sizes = {'snack': 5, 'medium': 10, 'feast': 15}
    item_count = sizes.get(size, 10)
    selected_items = random.sample(TRASH_ITEMS, item_count)

    game_id = str(uuid.uuid4())
    game = Game(id=game_id, player1_id=user.id, items=','.join(selected_items))
    db.session.add(game)
    db.session.commit()

    return redirect(url_for('play_game', game_id=game_id, time=time, username=username))


@flask_app.route('/play/<game_id>')
def play_game(game_id):
    if "username" not in session:
        return redirect(url_for("login", next=request.path))

    mode = request.args.get("mode", "self")

    if IS_PRODUCTION:
        game = Game.query.get_or_404(game_id)
        items = game.items.split(",")
        game_time = game.time
        game_start = int(game.game_start.timestamp()) if game.game_start else None
    else:
        game_data = ROOMS.get(game_id)
        if not game_data:
            return "Room not found", 404
        items = game_data["items"]
        game_time = game_data["time"]
        game_start = int(datetime.utcnow().timestamp())

    return render_template(
        "index.html",
        items=items,
        time=game_time,
        game_id=game_id,
        username=session["username"],
        game_start=game_start
    )


@flask_app.route('/create-room')
def create_room():
    if "username" not in session:
        return redirect(url_for("login", next=request.path))

    room_id = uuid.uuid4().hex[:6]
    session['room_id'] = room_id

    size = request.args.get("size", "medium")
    time = int(request.args.get("time", 300))
    sizes = {'snack': 5, 'medium': 10, 'feast': 15}
    item_count = sizes.get(size, 10)
    selected_items = random.sample(TRASH_ITEMS, item_count)

    if IS_PRODUCTION:
        user = User.query.filter_by(username=session["username"]).first()
        if not user:
            return redirect(url_for("login", next=request.path))

        game = Game(
            id=room_id,
            player1_id=user.id,
            items=','.join(selected_items),
            time=time,
            game_start=datetime.utcnow()
        )
        db.session.add(game)
        db.session.commit()

    else:
        ROOMS[room_id] = {
            "items": selected_items,
            "time": time
        }

    return redirect(url_for("challenge_friend", room_id=room_id))

@flask_app.route('/join/<room_id>')
def join_room_view(room_id):
    if "username" not in session:
        return redirect(url_for("login", next=request.path))

    session['room_id'] = room_id
    return redirect(url_for('play_game', game_id=room_id, mode="friend"))

@flask_app.route('/api/player/<username>')
def api_player_stats(username):
    user = User.query.filter_by(username=username).first()
    if not user:
        return jsonify({'error': 'User not found'}), 404
    return jsonify({
        'username': user.username,
        'wins': user.wins,
        'losses': user.losses
    })

@flask_app.route('/solo-game')
def solo_game():
    username = session.get("username")
    size = request.args.get('size', 'medium')
    time = int(request.args.get('time', 300))

    sizes = {'snack': 5, 'medium': 10, 'feast': 15}
    item_count = sizes.get(size, 10)
    selected_items = random.sample(TRASH_ITEMS, item_count)

    return render_template("index.html", items=selected_items, time=time, game_id="", username=username)


@flask_app.route('/challenge-friend')
def challenge_friend():
    username = session.get("username")
    room_id = request.args.get("room_id")

    if not room_id:
        room_id = uuid.uuid4().hex[:6]
        invite_url = url_for('accept_invite', room_id=room_id, _external=True)
        return render_template("waiting_room.html", room_id=room_id, invite_url=invite_url, username=username, is_host=True)

    invite_url = url_for('accept_invite', room_id=room_id, _external=True)
    return render_template("waiting_room.html", room_id=room_id, invite_url=invite_url, username=username, is_host=False)


@flask_app.route('/api/report-items', methods=['POST'])
def report_items():
    data = request.get_json()
    username = data.get("username")
    items = data.get("items", [])

    user = User.query.filter_by(username=username).first()
    if not user:
        return jsonify({"error": "User not found"}), 404

    for item in items:
        record = ItemPickup.query.filter_by(player_id=user.id, item=item).first()
        if record:
            record.count += 1
        else:
            record = ItemPickup(player_id=user.id, item=item, count=1)
            db.session.add(record)

    db.session.commit()
    return jsonify({"status": "ok"})

@flask_app.route('/api/player/<username>/item-stats')
def item_stats(username):
    user = User.query.filter_by(username=username).first()
    if not user:
        return jsonify({"error": "User not found"}), 404

    stats = {pickup.item: pickup.count for pickup in user.pickups}
    return jsonify(stats)

ROOM_CONNECTIONS = {}  # tracks how many people are in each room

@socketio.on('join_room')
def handle_join(data):
    room = data['room']
    username = data.get('username')
    join_room(room)
    print(f"✅ {username} joined room {room}")

    ROOM_CONNECTIONS[room] = ROOM_CONNECTIONS.get(room, 0) + 1

    if ROOM_CONNECTIONS[room] == 2:
        emit('start_game', {}, room=room)
    else:
        emit('waiting', {}, room=room)

@socketio.on('player_won')
def handle_win(data):
    room = data.get('room')
    username = data.get('username')
    if not room or not username:
        return

    game = Game.query.get(room)
    if not game:
        return

    winner = User.query.filter_by(username=username).first()
    if not winner:
        return

    game.winner_id = winner.id
    winner.wins += 1

    if game.player1_id != winner.id and game.player1_id:
        opponent = User.query.get(game.player1_id)
        if opponent:
            opponent.losses += 1
    elif game.player2_id != winner.id and game.player2_id:
        opponent = User.query.get(game.player2_id)
        if opponent:
            opponent.losses += 1

    db.session.commit()
    print(f"🏁 {data['username']} WON in room {data['room']}")
    emit('opponent_lost', {}, room=data['room'], include_self=False)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    socketio.run(flask_app, host="0.0.0.0", port=port)


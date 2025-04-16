from flask import Flask, render_template, request, redirect, url_for, jsonify
from flask_socketio import SocketIO, emit, join_room
from flask_sqlalchemy import SQLAlchemy
import uuid
import random
import os

flask_app = Flask(__name__)
flask_app.config['SECRET_KEY'] = 'raccoon-secret'
basedir = os.path.abspath(os.path.dirname(__file__))
flask_app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{os.path.join(basedir, 'instance', 'pandas.db')}"
flask_app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

socketio = SocketIO(flask_app, cors_allowed_origins="*")
db = SQLAlchemy(flask_app)

# Models
class Player(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True)
    wins = db.Column(db.Integer, default=0)
    losses = db.Column(db.Integer, default=0)

class Game(db.Model):
    id = db.Column(db.String(36), primary_key=True)
    player1_id = db.Column(db.Integer, db.ForeignKey('player.id'), nullable=True)
    player2_id = db.Column(db.Integer, db.ForeignKey('player.id'), nullable=True)
    winner_id = db.Column(db.Integer, nullable=True)
    items = db.Column(db.Text)

TRASH_ITEMS = [
    "bottle_cap",
    "broken_glass",
    "candy_wrapper",
    "cardboard",
    "chip_bag",
    "chopsticks",
    "cigarette",
    "clothing_item",
    "coffee_cup",
    "default",
    "face_mask",
    "fast_food_wrapper",
    "flyer",
    "food_container",
    "napkin",
    "paper_bag",
    "plastic_bag",
    "plastic_bottle",
    "receipt",
    "soda_can",
    "straw"
]

with flask_app.app_context():
    db.create_all()

@flask_app.route('/')
def home():
    return render_template('home.html')

@flask_app.route('/start-game')
def start_game():
    username = request.args.get('username', '').strip()
    if not username:
        return "Username is required", 400

    player = Player.query.filter_by(username=username).first()
    if not player:
        player = Player(username=username)
        db.session.add(player)
        db.session.commit()

    size = request.args.get('size', 'medium')
    time_str = request.args.get('time', '300')
    time = -1 if time_str == '-1' else int(time_str)

    sizes = {'snack': 5, 'medium': 10, 'feast': 15}
    item_count = sizes.get(size, 10)
    selected_items = random.sample(TRASH_ITEMS, item_count)

    game_id = str(uuid.uuid4())
    game = Game(id=game_id, player1_id=player.id, items=','.join(selected_items))
    db.session.add(game)
    db.session.commit()

    return redirect(url_for('play_game', game_id=game_id, time=time, username=username))

@flask_app.route('/play/<game_id>')
def play_game(game_id):
    game = Game.query.get_or_404(game_id)
    items = game.items.split(',')
    time = int(request.args.get('time', 300))
    username = request.args.get('username', '')
    return render_template('index.html', items=items, time=time, game_id=game_id, username=username)

@flask_app.route('/api/player/<username>')
def api_player_stats(username):
    player = Player.query.filter_by(username=username).first()
    if not player:
        return jsonify({'error': 'Player not found'}), 404
    return jsonify({
        'username': player.username,
        'wins': player.wins,
        'losses': player.losses
    })

@socketio.on('join_room')
def handle_join(data):
    join_room(data['room'])
    emit('joined', {'msg': f"Player joined room {data['room']}"}, room=data['room'])

@socketio.on('player_won')
def handle_win(data):
    room = data.get('room')
    username = data.get('username')

    if not room or not username:
        return

    game = Game.query.get(room)
    if not game:
        return

    winner = Player.query.filter_by(username=username).first()
    if not winner:
        return

    game.winner_id = winner.id
    winner.wins += 1

    if game.player1_id != winner.id and game.player1_id:
        opponent = Player.query.get(game.player1_id)
        if opponent:
            opponent.losses += 1
    elif game.player2_id != winner.id and game.player2_id:
        opponent = Player.query.get(game.player2_id)
        if opponent:
            opponent.losses += 1

    db.session.commit()
    emit('opponent_lost', {}, room=room, include_self=False)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    socketio.run(flask_app, host='0.0.0.0', port=port)

import os, uuid, sqlite3, html
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_bcrypt import Bcrypt

app = Flask(__name__)
app.config['SECRET_KEY'] = 'sorek_hub_2026_secret'
bcrypt = Bcrypt(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# --- DATABASE ---
def get_db():
    db = sqlite3.connect('database.db')
    db.row_factory = sqlite3.Row
    return db

def init_db():
    with get_db() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS users 
            (id INTEGER PRIMARY KEY AUTOINCREMENT, 
             pseudo TEXT UNIQUE, password TEXT, wins INTEGER DEFAULT 0)''')
        db.commit()

init_db()

# --- ETAT DU SERVEUR ---
connected_users = {} # sid -> {pseudo, wins, status, room}
games = {}           # room_id -> {type, p1, p2, state...}

@app.route('/')
def index():
    return render_template('index.html')

# --- AUTHENTIFICATION ---
@socketio.on('register')
def register(data):
    pseudo = html.escape(data['pseudo'].strip())
    if len(pseudo) < 3: return emit('auth_res', {'ok': False, 'msg': 'Pseudo trop court'})
    pw = bcrypt.generate_password_hash(data['pw']).decode('utf-8')
    try:
        with get_db() as db:
            db.execute("INSERT INTO users (pseudo, password) VALUES (?, ?)", (pseudo, pw))
            db.commit()
        emit('auth_res', {'ok': True, 'msg': 'Compte créé ! Connecte-toi.'})
    except:
        emit('auth_res', {'ok': False, 'msg': 'Ce pseudo existe déjà.'})

@socketio.on('login_attempt')
def login(data):
    pseudo = data['pseudo'].strip()
    with get_db() as db:
        user = db.execute("SELECT * FROM users WHERE pseudo = ?", (pseudo,)).fetchone()
        if user and bcrypt.check_password_hash(user['password'], data['pw']):
            connected_users[request.sid] = {
                'pseudo': user['pseudo'], 'wins': user['wins'], 
                'status': '🟢 Libre', 'room': None
            }
            emit('login_success', {'pseudo': user['pseudo'], 'wins': user['wins']})
            broadcast_users()
        else:
            emit('auth_res', {'ok': False, 'msg': 'Identifiants invalides.'})

@socketio.on('disconnect')
def disc():
    if request.sid in connected_users:
        leave_current_game(request.sid)
        del connected_users[request.sid]
        broadcast_users()

def broadcast_users():
    data = {sid: {'pseudo': u['pseudo'], 'wins': u['wins'], 'status': u['status']} 
            for sid, u in connected_users.items()}
    emit('update_users', data, broadcast=True)

# --- GESTION DES JEUX ---
@socketio.on('envoyer_defi')
def send_defi(data):
    target = data['target_id']
    if target in connected_users and not connected_users[target]['room']:
        emit('reception_defi', {
            'from_id': request.sid, 
            'from_name': connected_users[request.sid]['pseudo'], 
            'game': data['game_type']
        }, room=target)

@socketio.on('accepter_defi')
def accept(data):
    p1, p2 = data['challenger_id'], request.sid
    g_type = data['game']
    if p1 in connected_users and p2 in connected_users:
        rid = f"game_{uuid.uuid4().hex[:6]}"
        for p in [p1, p2]:
            join_room(rid, sid=p)
            connected_users[p]['room'] = rid
            connected_users[p]['status'] = f"🔴 En Duel ({g_type})"
        
        games[rid] = {'type': g_type, 'p1': p1, 'p2': p2, 'board': [None]*9, 'scores': {p1:0, p2:0}}
        emit('start_game', {'room': rid, 'game': g_type, 'role': 'p1', 'opp': connected_users[p2]['pseudo']}, room=p1)
        emit('start_game', {'room': rid, 'game': g_type, 'role': 'p2', 'opp': connected_users[p1]['pseudo']}, room=p2)
        broadcast_users()

@socketio.on('game_action')
def game_action(data):
    rid = connected_users[request.sid]['room']
    if rid in games:
        emit('game_update', data, room=rid, include_self=False)

@socketio.on('game_win')
def game_win(data):
    if request.sid in connected_users:
        with get_db() as db:
            db.execute("UPDATE users SET wins = wins + 1 WHERE pseudo = ?", (connected_users[request.sid]['pseudo'],))
            db.commit()
            row = db.execute("SELECT wins FROM users WHERE pseudo = ?", (connected_users[request.sid]['pseudo'],)).fetchone()
            connected_users[request.sid]['wins'] = row['wins']
        emit('update_my_score', {'wins': row['wins']})
        broadcast_users()

def leave_current_game(sid):
    rid = connected_users[sid]['room']
    if rid:
        emit('opp_left', room=rid)
        if rid in games: del games[rid]
        # On libère tout le monde dans la room
        for cid, u in connected_users.items():
            if u['room'] == rid:
                u['room'] = None
                u['status'] = '🟢 Libre'

@socketio.on('quit_game')
def on_quit(data):
    leave_current_game(request.sid)
    broadcast_users()

# --- PAINT & CHAT ---
@socketio.on('draw_data')
def draw(data):
    emit('draw_remote', data, broadcast=True, include_self=False)

@socketio.on('chat_msg')
def chat_msg(data):
    if request.sid in connected_users:
        emit('new_msg', {'u': connected_users[request.sid]['pseudo'], 'm': html.escape(data['m'])}, broadcast=True)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port)

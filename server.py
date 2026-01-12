import os, uuid, sqlite3
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_bcrypt import Bcrypt

app = Flask(__name__)
app.config['SECRET_KEY'] = 'sorek_ultra_secure_2024'
bcrypt = Bcrypt(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

def init_db():
    with sqlite3.connect('users.db') as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS users 
                     (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                      pseudo TEXT UNIQUE NOT NULL, 
                      password TEXT NOT NULL, 
                      clicks INTEGER DEFAULT 0,
                      multiplier INTEGER DEFAULT 1,
                      wins INTEGER DEFAULT 0)''')
        conn.commit()

init_db()
connected_users = {} # sid: {pseudo, room, multiplier}

@app.route('/')
def index():
    return render_template('index.html')

# --- AUTHENTIFICATION ---
@socketio.on('login_action')
def auth_logic(data):
    p, pwd, type_auth = data['pseudo'].strip(), data['password'], data['type']
    if len(p) < 2 or len(pwd) < 4:
        return emit('auth_error', "Identifiants trop courts !")

    with sqlite3.connect('users.db') as conn:
        conn.row_factory = sqlite3.Row
        user = conn.execute("SELECT * FROM users WHERE pseudo = ?", (p,)).fetchone()

        if type_auth == 'register':
            if user: return emit('auth_error', "Pseudo déjà pris !")
            hash_pw = bcrypt.generate_password_hash(pwd).decode('utf-8')
            conn.execute("INSERT INTO users (pseudo, password) VALUES (?, ?)", (p, hash_pw))
            conn.commit()
            user = conn.execute("SELECT * FROM users WHERE pseudo = ?", (p,)).fetchone()

        if user and bcrypt.check_password_hash(user['password'], pwd):
            connected_users[request.sid] = {'pseudo': p, 'room': None, 'mult': user['multiplier']}
            emit('login_ok', {
                'pseudo': p, 
                'clicks': user['clicks'], 
                'mult': user['multiplier'],
                'wins': user['wins']
            })
            update_global_data()
        else:
            emit('auth_error', "Pseudo ou mot de passe incorrect.")

# --- CLICKER LOGIC ---
@socketio.on('add_click')
def add_click():
    if request.sid in connected_users:
        p = connected_users[request.sid]['pseudo']
        with sqlite3.connect('users.db') as conn:
            conn.execute("UPDATE users SET clicks = clicks + multiplier WHERE pseudo = ?", (p,))
            conn.commit()
            res = conn.execute("SELECT clicks FROM users WHERE pseudo = ?", (p,)).fetchone()
            emit('update_score', {'clicks': res[0]})

@socketio.on('buy_upgrade')
def buy_up():
    if request.sid in connected_users:
        p = connected_users[request.sid]['pseudo']
        with sqlite3.connect('users.db') as conn:
            u = conn.execute("SELECT clicks, multiplier FROM users WHERE pseudo = ?", (p,)).fetchone()
            cost = u[1] * 100 # Prix augmente
            if u[0] >= cost:
                conn.execute("UPDATE users SET clicks = clicks - ?, multiplier = multiplier + 1 WHERE pseudo = ?", (cost, p))
                conn.commit()
                res = conn.execute("SELECT clicks, multiplier FROM users WHERE pseudo = ?", (p,)).fetchone()
                connected_users[request.sid]['mult'] = res[1]
                emit('login_ok', {'pseudo': p, 'clicks': res[0], 'mult': res[1]})

# --- MULTIJOUEUR & DEFIS ---
def update_global_data():
    with sqlite3.connect('users.db') as conn:
        leaderboard = conn.execute("SELECT pseudo, clicks FROM users ORDER BY clicks DESC LIMIT 5").fetchall()
        lb_list = [{"p": r[0], "c": r[1]} for r in leaderboard]
        users_list = {sid: u['pseudo'] for sid, u in connected_users.items()}
        emit('global_update', {'users': users_list, 'lb': lb_list}, broadcast=True)

@socketio.on('send_defi')
def defi(data):
    target = data['target_sid']
    if target in connected_users:
        emit('receive_defi', {'from_sid': request.sid, 'from_name': connected_users[request.sid]['pseudo'], 'game': data['game']}, room=target)

@socketio.on('accept_defi')
def accept(data):
    p1, p2 = data['opp_sid'], request.sid
    rid = f"room_{uuid.uuid4().hex[:6]}"
    join_room(rid, sid=p1); join_room(rid, sid=p2)
    connected_users[p1]['room'] = rid; connected_users[p2]['room'] = rid
    emit('start_game', {'game': data['game'], 'room': rid, 'opp': connected_users[p2]['pseudo'], 'turn': True}, room=p1)
    emit('start_game', {'game': data['game'], 'room': rid, 'opp': connected_users[p1]['pseudo'], 'turn': False}, room=p2)

@socketio.on('game_move')
def g_move(data):
    emit('opp_move', data, room=data['room'], include_self=False)

@socketio.on('msg')
def chat(data):
    if request.sid in connected_users:
        emit('new_msg', {'p': connected_users[request.sid]['pseudo'], 'm': data['m']}, broadcast=True)

@socketio.on('disconnect')
def disc():
    if request.sid in connected_users:
        del connected_users[request.sid]
        update_global_data()

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))

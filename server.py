import os, uuid, sqlite3
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_bcrypt import Bcrypt

app = Flask(__name__)
app.config['SECRET_KEY'] = 'sorek_secret_key_super_secure'
bcrypt = Bcrypt(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet', max_http_buffer_size=1e7)

# --- GESTION BASE DE DONNÉES ---
def init_db():
    with sqlite3.connect('users.db') as conn:
        c = conn.cursor()
        # Création de la table si elle n'existe pas
        c.execute('''CREATE TABLE IF NOT EXISTS users 
                     (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                      pseudo TEXT UNIQUE NOT NULL, 
                      password TEXT NOT NULL, 
                      wins INTEGER DEFAULT 0)''')
        conn.commit()

init_db() # On lance la création au démarrage

# --- VARIABLES LIVE (RAM) ---
connected_users = {} # Stocke la session active : {sid: {'pseudo': 'Toka', 'room': ...}}
shifumi_data = {}

@app.route('/')
def index():
    return render_template('index.html')

# --- AUTHENTIFICATION ---
@socketio.on('register')
def register(data):
    pseudo = data['pseudo'].strip()
    password = data['password']
    
    if len(pseudo) < 2 or len(password) < 4:
        return emit('auth_error', "Pseudo ou mot de passe trop court.")

    # On crypte le mot de passe
    pw_hash = bcrypt.generate_password_hash(password).decode('utf-8')

    try:
        with sqlite3.connect('users.db') as conn:
            conn.execute("INSERT INTO users (pseudo, password) VALUES (?, ?)", (pseudo, pw_hash))
            conn.commit()
        emit('auth_success', {'pseudo': pseudo, 'msg': "Compte créé ! Connecte-toi."})
        # On connecte l'utilisateur directement
        login({'pseudo': pseudo, 'password': password})
    except sqlite3.IntegrityError:
        emit('auth_error', "Ce pseudo est déjà pris !")

@socketio.on('login')
def login(data):
    pseudo = data['pseudo'].strip()
    password = data['password']

    with sqlite3.connect('users.db') as conn:
        # On cherche l'utilisateur (row_factory permet d'accéder par nom de colonne)
        conn.row_factory = sqlite3.Row 
        user = conn.execute("SELECT * FROM users WHERE pseudo = ?", (pseudo,)).fetchone()
        
        if user and bcrypt.check_password_hash(user['password'], password):
            # Succès ! On enregistre la session en RAM
            connected_users[request.sid] = {
                'id': user['id'],
                'pseudo': user['pseudo'],
                'room': None,
                'wins': user['wins']
            }
            emit('login_ok', {'pseudo': user['pseudo'], 'wins': user['wins']})
            emit('update_users', get_public_users(), broadcast=True)
        else:
            emit('auth_error', "Pseudo ou mot de passe incorrect.")

def get_public_users():
    # On renvoie juste ce qu'il faut afficher (pas les mots de passe !)
    return {sid: {'pseudo': u['pseudo']} for sid, u in connected_users.items()}

# --- GESTION DECONNEXION ---
@socketio.on('disconnect')
def on_disc():
    if request.sid in connected_users:
        rid = connected_users[request.sid]['room']
        if rid:
            emit('fin_duel', room=rid)
            if rid in shifumi_data: del shifumi_data[rid]
        del connected_users[request.sid]
        emit('update_users', get_public_users(), broadcast=True)

# --- DUELS & JEUX (Reste identique mais utilise connected_users) ---
@socketio.on('envoyer_defi')
def send_d(data):
    target = data['target_id']
    if target in connected_users:
        emit('reception_defi', {
            'from_id': request.sid,
            'from_name': connected_users[request.sid]['pseudo'],
            'game': data['game_type']
        }, room=target)

@socketio.on('accepter_defi')
def accept_d(data):
    p1, p2 = data['challenger_id'], request.sid
    if p1 in connected_users and p2 in connected_users:
        rid = f"room_{uuid.uuid4().hex[:6]}"
        join_room(rid, sid=p1); join_room(rid, sid=p2)
        connected_users[p1]['room'] = rid; connected_users[p2]['room'] = rid
        
        if data['game'] == 'Shifumi': shifumi_data[rid] = {}
        
        emit('start_game', {'game': data['game'], 'room': rid, 'opp': connected_users[p2]['pseudo'], 'turn': True}, room=p1)
        emit('start_game', {'game': data['game'], 'room': rid, 'opp': connected_users[p1]['pseudo'], 'turn': False}, room=p2)

@socketio.on('quitter_jeu')
def quit_g(data):
    rid = data.get('room')
    if rid:
        emit('fin_duel', room=rid)
        leave_room(rid) 
        # On remet les joueurs 'libres'
        if request.sid in connected_users: connected_users[request.sid]['room'] = None

# --- JEUX DATA ---
@socketio.on('coup_morpion')
def move_m(data):
    emit('receive_move_morpion', data, room=data['room'], include_self=False)

@socketio.on('coup_shifumi')
def move_s(data):
    rid = data['room']
    if rid in shifumi_data:
        shifumi_data[rid][request.sid] = data['move']
        if len(shifumi_data[rid]) == 2:
            p = list(shifumi_data[rid].keys())
            emit('resultat_shifumi', {'p1': shifumi_data[rid][p[0]], 'p2': shifumi_data[rid][p[1]]}, room=rid)
            shifumi_data[rid] = {}

# --- PAINT & TCHAT ---
@socketio.on('draw_data')
def draw(data):
    emit('draw_remote', data, broadcast=True, include_self=False)

@socketio.on('clear_canvas')
def clear():
    emit('canvas_cleared', broadcast=True, include_self=False)

@socketio.on('msg')
def chat(data):
    if request.sid in connected_users:
        emit('new_msg', {'p': connected_users[request.sid]['pseudo'], 'm': data['m']}, broadcast=True)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port)

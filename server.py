import os, uuid
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_bcrypt import Bcrypt
from supabase import create_client, Client

app = Flask(__name__)
app.config['SECRET_KEY'] = 'sorek_cloud_2026_final'
bcrypt = Bcrypt(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# --- CONFIGURATION SUPABASE ---
SUPABASE_URL = "https://rzzhkdzjnjeeoqbtlles.supabase.co"
SUPABASE_KEY = "9MjZLGabGOUWEZuo"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

connected_users = {} # sid: {pseudo, mult, room}

@app.route('/')
def index():
    return render_template('index.html')

# --- AUTHENTIFICATION ---
@socketio.on('login_action')
def auth_logic(data):
    p, pwd, type_auth = data['pseudo'].strip(), data['password'], data['type']
    res = supabase.table("users").select("*").eq("pseudo", p).execute()
    user = res.data[0] if res.data else None

    if type_auth == 'register':
        if user: return emit('auth_error', "Ce pseudo existe déjà !")
        hash_pw = bcrypt.generate_password_hash(pwd).decode('utf-8')
        supabase.table("users").insert({"pseudo": p, "password": hash_pw}).execute()
        res = supabase.table("users").select("*").eq("pseudo", p).execute()
        user = res.data[0]

    if user and bcrypt.check_password_hash(user['password'], pwd):
        connected_users[request.sid] = {'pseudo': p, 'mult': user['multiplier'], 'room': None}
        emit('login_ok', {'pseudo': p, 'clicks': user['clicks'], 'mult': user['multiplier']})
        broadcast_global_data()
    else:
        emit('auth_error', "Identifiants invalides.")

# --- CLICKER & LEADERBOARD ---
@socketio.on('add_click')
def add_click():
    if request.sid in connected_users:
        u = connected_users[request.sid]
        res = supabase.table("users").select("clicks").eq("pseudo", u['pseudo']).execute()
        new_total = res.data[0]['clicks'] + u['mult']
        supabase.table("users").update({"clicks": new_total}).eq("pseudo", u['pseudo']).execute()
        emit('update_score', {'clicks': new_total})
        broadcast_leaderboard()

@socketio.on('buy_upgrade')
def buy_up():
    if request.sid in connected_users:
        p = connected_users[request.sid]['pseudo']
        res = supabase.table("users").select("clicks", "multiplier").eq("pseudo", p).execute()
        u_data = res.data[0]
        cost = u_data['multiplier'] * 100
        if u_data['clicks'] >= cost:
            new_mult = u_data['multiplier'] + 1
            new_clicks = u_data['clicks'] - cost
            supabase.table("users").update({"clicks": new_clicks, "multiplier": new_mult}).eq("pseudo", p).execute()
            connected_users[request.sid]['mult'] = new_mult
            emit('login_ok', {'pseudo': p, 'clicks': new_clicks, 'mult': new_mult})
            broadcast_leaderboard()

def broadcast_leaderboard():
    res = supabase.table("users").select("pseudo", "clicks").order("clicks", desc=True).limit(10).execute()
    emit('update_leaderboard', {'lb': [{"p": r['pseudo'], "c": r['clicks']} for r in res.data]}, broadcast=True)

def broadcast_global_data():
    broadcast_leaderboard()
    users_list = {sid: u['pseudo'] for sid, u in connected_users.items()}
    emit('update_users_list', {'users': users_list}, broadcast=True)

# --- CHAT & JEUX (CORRIGÉ SANS RELOAD) ---
@socketio.on('msg')
def chat(data):
    if request.sid in connected_users:
        emit('new_msg', {'p': connected_users[request.sid]['pseudo'], 'm': data['m']}, broadcast=True)

@socketio.on('send_defi')
def defi(data):
    if data['target_sid'] in connected_users:
        emit('receive_defi', {'from_sid': request.sid, 'from_name': connected_users[request.sid]['pseudo']}, room=data['target_sid'])

@socketio.on('accept_defi')
def accept(data):
    p1, p2 = data['opp_sid'], request.sid
    rid = f"room_{uuid.uuid4().hex[:6]}"
    join_room(rid, sid=p1); join_room(rid, sid=p2)
    connected_users[p1]['room'] = rid; connected_users[p2]['room'] = rid
    emit('start_game', {'room': rid, 'opp': connected_users[p2]['pseudo'], 'turn': True}, room=p1)
    emit('start_game', {'room': rid, 'opp': connected_users[p1]['pseudo'], 'turn': False}, room=p2)

@socketio.on('game_move')
def g_move(data):
    emit('opp_move', data, room=data['room'], include_self=False)

@socketio.on('quitter_jeu')
def quitter(data):
    rid = data.get('room')
    if rid:
        emit('fin_duel', room=rid)
        leave_room(rid)

@socketio.on('disconnect')
def disc():
    if request.sid in connected_users:
        del connected_users[request.sid]
        broadcast_global_data()

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))

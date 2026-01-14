import os, random
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit
from flask_bcrypt import Bcrypt
from supabase import create_client, Client

app = Flask(__name__)
app.config['SECRET_KEY'] = 'sorek_empire_final'
bcrypt = Bcrypt(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# --- CONFIG SUPABASE ---
SUPABASE_URL = "https://TON_ID.supabase.co"
SUPABASE_KEY = "TA_CLE_SERVICE_ROLE"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

connected_users = {} 

RANKS = [(0, "Vagabond 🛖"), (1000, "Citoyen 🏠"), (5000, "Chevalier ⚔️"), (20000, "Seigneur 🏰"), (100000, "Roi 👑")]

def get_rank(clicks):
    for score, title in reversed(RANKS):
        if clicks >= score: return title
    return RANKS[0][1]

@app.route('/')
def index(): return render_template('index.html')

@socketio.on('login_action')
def auth_logic(data):
    try:
        p, pwd, t = data['pseudo'].strip(), data['password'], data['type']
        res = supabase.table("users").select("*").eq("pseudo", p).execute()
        user = res.data[0] if res.data else None

        if t == 'register' and not user:
            hpw = bcrypt.generate_password_hash(pwd).decode('utf-8')
            supabase.table("users").insert({"pseudo": p, "password": hpw, "clicks": 0, "multiplier": 1}).execute()
            user = supabase.table("users").select("*").eq("pseudo", p).execute().data[0]

        if user and bcrypt.check_password_hash(user['password'], pwd):
            rank = get_rank(user['clicks'])
            connected_users[request.sid] = {'pseudo': p, 'mult': user['multiplier'], 'guild': user.get('guild_name'), 'rank': rank}
            emit('login_ok', {'pseudo': p, 'clicks': user['clicks'], 'mult': user['multiplier'], 'guild': user.get('guild_name'), 'rank': rank})
        else: emit('auth_error', "Erreur d'identifiants")
    except Exception as e: emit('auth_error', str(e))

@socketio.on('add_click')
def add_click():
    sid = request.sid
    if sid in connected_users:
        u = connected_users[sid]
        res = supabase.table("users").select("clicks").eq("pseudo", u['pseudo']).execute()
        new_val = res.data[0]['clicks'] + u['mult']
        supabase.table("users").update({"clicks": new_val}).eq("pseudo", u['pseudo']).execute()
        
        if u['guild']:
            g_res = supabase.table("guilds").select("total_clicks").eq("name", u['guild']).execute()
            if g_res.data:
                supabase.table("guilds").update({"total_clicks": g_res.data[0]['total_clicks'] + u['mult']}).eq("name", u['guild']).execute()
        
        rank = get_rank(new_val)
        connected_users[sid]['rank'] = rank
        emit('update_score', {'clicks': new_val, 'rank': rank})

@socketio.on('create_guild')
def create_g(data):
    sid = request.sid
    p = connected_users[sid]['pseudo']
    name = data['name'].strip()
    try:
        supabase.table("guilds").insert({"name": name, "total_clicks": 0}).execute()
        supabase.table("users").update({"guild_name": name}).eq("pseudo", p).execute()
        connected_users[sid]['guild'] = name
        emit('update_full_state', {'guild': name}, room=sid)
        emit('notif', f"Guilde {name} créée !")
    except: emit('notif', "Nom déjà pris ou erreur.")

@socketio.on('msg')
def handle_msg(d):
    u = connected_users.get(request.sid)
    if u: emit('new_msg', {'p': u['pseudo'], 'm': d['m'], 'r': u['rank']}, broadcast=True)

@socketio.on('spin_wheel')
def spin():
    sid = request.sid
    p = connected_users[sid]['pseudo']
    res = supabase.table("users").select("clicks").eq("pseudo", p).execute()
    clicks = res.data[0]['clicks']
    if clicks < 500: return emit('notif', "Pas assez de clics !")
    
    win = random.random() < 0.3
    gain = 2500 if win else -500
    new_total = clicks + gain
    supabase.table("users").update({"clicks": new_total}).eq("pseudo", p).execute()
    emit('spin_result', {'outcome': 'gagné' if win else 'perdu', 'clicks': new_total})

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000)


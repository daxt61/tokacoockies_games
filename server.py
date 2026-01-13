import os, uuid
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_bcrypt import Bcrypt
from supabase import create_client, Client

app = Flask(__name__)
app.config['SECRET_KEY'] = 'sorek_v5_final_2026'
bcrypt = Bcrypt(app)
# Mode eventlet pour la stabilité des connexions simultanées
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# --- CONFIGURATION SUPABASE ---
SUPABASE_URL = "https://TON_ID.supabase.co"
SUPABASE_KEY = "TA_CLE_SERVICE_ROLE"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

connected_users = {} # sid: {pseudo, mult, guild}
pending_reqs = {} # leader_pseudo: [applicants]

@app.route('/')
def index():
    return render_template('index.html')

@socketio.on('login_action')
def auth_logic(data):
    try:
        p, pwd, t = data['pseudo'].strip(), data['password'], data['type']
        res = supabase.table("users").select("*").eq("pseudo", p).execute()
        user = res.data[0] if res.data else None

        if t == 'register':
            if user: return emit('auth_error', "Pseudo déjà pris !")
            hpw = bcrypt.generate_password_hash(pwd).decode('utf-8')
            supabase.table("users").insert({"pseudo": p, "password": hpw}).execute()
            user = supabase.table("users").select("*").eq("pseudo", p).execute().data[0]

        if user and bcrypt.check_password_hash(user['password'], pwd):
            connected_users[request.sid] = {'pseudo': p, 'mult': user['multiplier'], 'guild': user.get('guild_name')}
            emit('login_ok', {'pseudo': p, 'clicks': user['clicks'], 'mult': user['multiplier'], 'guild': user.get('guild_name')})
            broadcast_global()
            send_relative_lb(p, request.sid)
        else:
            emit('auth_error', "Identifiants invalides.")
    except Exception as e:
        emit('auth_error', "Erreur serveur.")

# --- CLICKER & CLASSEMENT RELATIF ---
@socketio.on('add_click')
def add_click():
    sid = request.sid
    if sid in connected_users:
        u = connected_users[sid]
        # Update DB User
        res = supabase.table("users").select("clicks").eq("pseudo", u['pseudo']).execute()
        new_val = res.data[0]['clicks'] + u['mult']
        supabase.table("users").update({"clicks": new_val}).eq("pseudo", u['pseudo']).execute()
        
        # Update DB Guild
        if u['guild']:
            g_res = supabase.table("guilds").select("total_clicks").eq("name", u['guild']).execute()
            if g_res.data:
                g_val = g_res.data[0]['total_clicks'] + u['mult']
                supabase.table("guilds").update({"total_clicks": g_val}).eq("name", u['guild']).execute()
        
        emit('update_score', {'clicks': new_val})
        send_relative_lb(u['pseudo'], sid)

def send_relative_lb(pseudo, sid):
    res = supabase.table("users").select("pseudo", "clicks").order("clicks", desc=True).execute()
    all_u = res.data
    idx = next((i for i, x in enumerate(all_u) if x['pseudo'] == pseudo), 0)
    start, end = max(0, idx - 5), min(len(all_u), idx + 6)
    emit('relative_lb', {'lb': all_u[start:end], 'pos': idx + 1}, room=sid)

# --- BOUTIQUE ---
@socketio.on('buy_upgrade')
def buy():
    sid = request.sid
    if sid in connected_users:
        p = connected_users[sid]['pseudo']
        res = supabase.table("users").select("clicks", "multiplier").eq("pseudo", p).execute()
        d = res.data[0]
        cost = d['multiplier'] * 100
        if d['clicks'] >= cost:
            nm, nc = d['multiplier'] + 1, d['clicks'] - cost
            supabase.table("users").update({"multiplier": nm, "clicks": nc}).eq("pseudo", p).execute()
            connected_users[sid]['mult'] = nm
            emit('login_ok', {'pseudo': p, 'clicks': nc, 'mult': nm, 'guild': connected_users[sid]['guild']})

# --- GUILDES & TRAHISON ---
@socketio.on('create_guild')
def create_g(data):
    p = connected_users[request.sid]['pseudo']
    name = data['name'].strip()
    try:
        supabase.table("guilds").insert({"name": name, "leader": p}).execute()
        supabase.table("users").update({"guild_name": name}).eq("pseudo", p).execute()
        connected_users[request.sid]['guild'] = name
        emit('login_ok', {'pseudo': p, 'clicks': 0, 'mult': 1, 'guild': name}) # On refresh les infos
    except: emit('auth_error', "Nom de guilde déjà pris.")

@socketio.on('trahir_guilde')
def trahir():
    p = connected_users[request.sid]['pseudo']
    supabase.table("users").update({"guild_name": None, "clicks": 0, "multiplier": 1}).eq("pseudo", p).execute()
    connected_users[request.sid]['guild'] = None
    connected_users[request.sid]['mult'] = 1
    emit('login_ok', {'pseudo': p, 'clicks': 0, 'mult': 1, 'guild': None})

# --- TCHAT & LISTE ---
def broadcast_global():
    ulist = {sid: u['pseudo'] for sid, u in connected_users.items()}
    emit('update_users_list', {'users': ulist}, broadcast=True)

@socketio.on('get_full_lb')
def get_lb():
    u_res = supabase.table("users").select("pseudo", "clicks").order("clicks", desc=True).limit(20).execute()
    g_res = supabase.table("guilds").select("name", "total_clicks").order("total_clicks", desc=True).limit(20).execute()
    emit('full_lb_data', {'users': u_res.data, 'guilds': g_res.data})

@socketio.on('msg')
def chat(d):
    if request.sid in connected_users:
        emit('new_msg', {'p': connected_users[request.sid]['pseudo'], 'm': d['m']}, broadcast=True)

@socketio.on('disconnect')
def disc():
    if request.sid in connected_users:
        del connected_users[request.sid]
        broadcast_global()

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))

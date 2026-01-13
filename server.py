import os, uuid, random
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_bcrypt import Bcrypt
from supabase import create_client, Client

app = Flask(__name__)
app.config['SECRET_KEY'] = 'sorek_v5_mega'
bcrypt = Bcrypt(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# CONFIG SUPABASE
SUPABASE_URL = "https://TON_ID.supabase.co"
SUPABASE_KEY = "TA_CLE_SERVICE_ROLE"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

connected_users = {} # sid: {pseudo, mult, guild}
pending_requests = {} # leader_pseudo: [list_of_applicants]

@app.route('/')
def index(): return render_template('index.html')

@socketio.on('login_action')
def auth_logic(data):
    p, pwd, t = data['pseudo'].strip(), data['password'], data['type']
    res = supabase.table("users").select("*").eq("pseudo", p).execute()
    user = res.data[0] if res.data else None

    if t == 'register':
        if user: return emit('auth_error', "Pseudo pris !")
        h = bcrypt.generate_password_hash(pwd).decode('utf-8')
        supabase.table("users").insert({"pseudo": p, "password": h}).execute()
        user = supabase.table("users").select("*").eq("pseudo", p).execute().data[0]

    if user and bcrypt.check_password_hash(user['password'], pwd):
        connected_users[request.sid] = {'pseudo': p, 'mult': user['multiplier'], 'guild': user.get('guild_name')}
        emit('login_ok', {'pseudo': p, 'clicks': user['clicks'], 'mult': user['multiplier'], 'guild': user.get('guild_name')})
        
        # Vérifier si des gens veulent join sa guilde
        if p in pending_requests and pending_requests[p]:
            for applicant in pending_requests[p]:
                emit('guild_request', {'from': applicant}, room=request.sid)
        
        broadcast_global_data()
    else: emit('auth_error', "Erreur login")

# --- CLICKER & GUILD LOGIC ---
@socketio.on('add_click')
def add_click():
    sid = request.sid
    if sid in connected_users:
        u = connected_users[sid]
        # Update User
        res = supabase.table("users").select("clicks").eq("pseudo", u['pseudo']).execute()
        new_total = res.data[0]['clicks'] + u['mult']
        supabase.table("users").update({"clicks": new_total}).eq("pseudo", u['pseudo']).execute()
        
        # Update Guild si existe
        if u['guild']:
            g_res = supabase.table("guilds").select("total_clicks").eq("name", u['guild']).execute()
            if g_res.data:
                new_g_total = g_res.data[0]['total_clicks'] + u['mult']
                supabase.table("guilds").update({"total_clicks": new_g_total}).eq("name", u['guild']).execute()
        
        emit('update_score', {'clicks': new_total})
        send_relative_lb(u['pseudo'])

@socketio.on('create_guild')
def create_g(data):
    p = connected_users[request.sid]['pseudo']
    name = data['name'].strip()
    try:
        supabase.table("guilds").insert({"name": name, "leader": p}).execute()
        supabase.table("users").update({"guild_name": name}).eq("pseudo", p).execute()
        connected_users[request.sid]['guild'] = name
        emit('guild_update', {'guild': name})
        broadcast_global_data()
    except: emit('notif', "Erreur : Nom déjà pris ou tu as déjà une guilde.")

@socketio.on('join_guild_request')
def join_req(data):
    p = connected_users[request.sid]['pseudo']
    target_guild = data['guild']
    res = supabase.table("guilds").select("leader").eq("name", target_guild).execute()
    if res.data:
        leader = res.data[0]['leader']
        if leader not in pending_requests: pending_requests[leader] = []
        pending_requests[leader].append(p)
        # Si leader est co, on le prévient
        for sid, info in connected_users.items():
            if info['pseudo'] == leader:
                emit('guild_request', {'from': p}, room=sid)
        emit('notif', "Demande envoyée au chef !")

@socketio.on('answer_guild')
def answer(data):
    leader = connected_users[request.sid]['pseudo']
    applicant = data['user']
    if data['accept']:
        g_res = supabase.table("guilds").select("name").eq("leader", leader).execute()
        g_name = g_res.data[0]['name']
        supabase.table("users").update({"guild_name": g_name}).eq("pseudo", applicant).execute()
        emit('notif', f"Tu as rejoint la guilde {g_name}", room=get_sid(applicant))
    if leader in pending_requests:
        pending_requests[leader] = [x for x in pending_requests[leader] if x != applicant]

@socketio.on('trahir_guilde')
def trahir():
    p = connected_users[request.sid]['pseudo']
    supabase.table("users").update({"guild_name": None, "clicks": 0, "multiplier": 1}).eq("pseudo", p).execute()
    connected_users[request.sid]['guild'] = None
    connected_users[request.sid]['mult'] = 1
    emit('login_ok', {'pseudo': p, 'clicks': 0, 'mult': 1, 'guild': None})
    emit('notif', "Tu as tout perdu en trahissant ta guilde...")

# --- CLASSEMENTS ---
def send_relative_lb(pseudo):
    # Récupérer tout le monde trié par clics
    res = supabase.table("users").select("pseudo", "clicks").order("clicks", desc=True).execute()
    all_users = res.data
    idx = next((i for i, item in enumerate(all_users) if item["pseudo"] == pseudo), -1)
    
    start = max(0, idx - 5)
    end = min(len(all_users), idx + 6)
    emit('relative_lb', {'lb': all_users[start:end], 'my_pos': idx + 1})

@socketio.on('get_full_lb')
def full_lb():
    u_res = supabase.table("users").select("pseudo", "clicks").order("clicks", desc=True).limit(20).execute()
    g_res = supabase.table("guilds").select("name", "total_clicks").order("total_clicks", desc=True).limit(20).execute()
    emit('full_lb_data', {'users': u_res.data, 'guilds': g_res.data})

def broadcast_global_data():
    users_list = {sid: u['pseudo'] for sid, u in connected_users.items()}
    emit('update_users_list', {'users': users_list}, broadcast=True)

def get_sid(pseudo):
    for s, i in connected_users.items():
        if i['pseudo'] == pseudo: return s
    return None

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))

import os, random
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit
from flask_bcrypt import Bcrypt
from supabase import create_client, Client

app = Flask(__name__)
app.config['SECRET_KEY'] = 'sorek_v8_final'
bcrypt = Bcrypt(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# --- CONFIG SUPABASE (Remets tes clés) ---
SUPABASE_URL = "https://TON_ID.supabase.co"
SUPABASE_KEY = "TA_CLE_SERVICE_ROLE"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

connected_users = {} 

def get_rank(c):
    r = [(0,"Vagabond"),(1000,"Citoyen"),(5000,"Chevalier"),(20000,"Seigneur"),(100000,"Roi")]
    for s, t in reversed(r):
        if c >= s: return t
    return "Vagabond"

def send_lb():
    res = supabase.table("users").select("pseudo", "clicks").order("clicks", desc=True).limit(5).execute()
    socketio.emit('lb_update', {'players': res.data})

@app.route('/')
def index(): return render_template('index.html')

@socketio.on('login_action')
def auth_logic(data):
    p, pwd, t = data['pseudo'].strip(), data['password'], data['type']
    res = supabase.table("users").select("*").eq("pseudo", p).execute()
    user = res.data[0] if res.data else None
    if t == 'register' and not user:
        hpw = bcrypt.generate_password_hash(pwd).decode('utf-8')
        supabase.table("users").insert({"pseudo": p, "password": hpw, "clicks": 0, "multiplier": 1}).execute()
        user = supabase.table("users").select("*").eq("pseudo", p).execute().data[0]
    if user and bcrypt.check_password_hash(user['password'], pwd):
        connected_users[request.sid] = {'pseudo': p, 'mult': user['multiplier'], 'guild': user.get('guild_name')}
        emit('login_ok', {'pseudo': p, 'clicks': user['clicks'], 'mult': user['multiplier'], 'guild': user.get('guild_name'), 'rank': get_rank(user['clicks'])})
        send_lb()

@socketio.on('add_click')
def add_click():
    u = connected_users.get(request.sid)
    if u:
        res = supabase.table("users").select("clicks").eq("pseudo", u['pseudo']).execute()
        nv = res.data[0]['clicks'] + u['mult']
        supabase.table("users").update({"clicks": nv}).eq("pseudo", u['pseudo']).execute()
        emit('update_score', {'clicks': nv, 'rank': get_rank(nv)})
        send_lb()

@socketio.on('buy_upgrade')
def buy_up():
    u = connected_users.get(request.sid)
    if u:
        res = supabase.table("users").select("clicks", "multiplier").eq("pseudo", u['pseudo']).execute()
        c, m = res.data[0]['clicks'], res.data[0]['multiplier']
        cost = m * 100
        if c >= cost:
            supabase.table("users").update({"clicks": c-cost, "multiplier": m+1}).eq("pseudo", u['pseudo']).execute()
            connected_users[request.sid]['mult'] = m + 1
            emit('update_full_state', {'clicks': c-cost, 'mult': m+1, 'rank': get_rank(c-cost)})
            emit('notif', "Booster acheté ! 🚀")
        else: emit('notif', "Pas assez de clics !")

@socketio.on('create_guild')
def create_g(data):
    p = connected_users[request.sid]['pseudo']
    n = data['name'].strip()
    supabase.table("guilds").insert({"name": n, "total_clicks": 0}).execute()
    supabase.table("users").update({"guild_name": n}).eq("pseudo", p).execute()
    connected_users[request.sid]['guild'] = n
    emit('update_full_state', {'guild': n})

@socketio.on('msg')
def handle_msg(d):
    u = connected_users.get(request.sid)
    if u: emit('new_msg', {'p': u['pseudo'], 'm': d['m']}, broadcast=True)

@socketio.on('spin_wheel')
def spin():
    p = connected_users[request.sid]['pseudo']
    c = supabase.table("users").select("clicks").eq("pseudo", p).execute().data[0]['clicks']
    if c < 500: return
    win = random.random() < 0.3
    gn = 2500 if win else -500
    supabase.table("users").update({"clicks": c+gn}).eq("pseudo", p).execute()
    emit('spin_result', {'outcome': 'gagné' if win else 'perdu', 'clicks': c+gn})

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000)



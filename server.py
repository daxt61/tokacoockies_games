import os
import logging
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room
from flask_bcrypt import Bcrypt
from supabase import create_client, Client
import eventlet

app = Flask(__name__)
app.config['SECRET_KEY'] = 'sorek_v9_ultra_secure'
bcrypt = Bcrypt(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# === CONFIG SUPABASE ===
SUPABASE_URL = "https://rzzhkdzjnjeeoqbtlles.supabase.co"
SUPABASE_KEY = "sb_secret_wjlaZm7VdO5VgO6UfqEn0g_FgbwC-ao"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

connected_users = {}  # {sid: {pseudo, mult, guild}}

# === UTILITAIRES ===
def get_rank(clicks):
    ranks = [(0, "Vagabond"), (1000, "Citoyen"), (5000, "Chevalier"), (20000, "Seigneur"), (100000, "Roi"), (500000, "Empereur")]
    for threshold, title in reversed(ranks):
        if clicks >= threshold: return title
    return "Vagabond"

def send_leaderboard(sid=None):
    try:
        if sid and sid in connected_users:
            pseudo = connected_users[sid]['pseudo']
            res = supabase.rpc('get_relative_leaderboard', {'player_pseudo': pseudo}).execute()
            socketio.emit('leaderboard_update', {'players': res.data}, room=sid)
        else:
            res = supabase.table("users").select("pseudo", "clicks", "guild_name").order("clicks", desc=True).limit(10).execute()
            players = [{"rank": i+1, **p} for i, p in enumerate(res.data)]
            socketio.emit('leaderboard_update', {'players': players})
    except Exception as e:
        logging.error(f"LB Error: {e}")

def update_social_data(pseudo):
    try:
        # Amis
        res_f = supabase.table("friendships").select("*").or_(f'user1.eq."{pseudo}",user2.eq."{pseudo}"').eq("status", "accepted").execute()
        friends = [f['user2'] if f['user1'] == pseudo else f['user1'] for f in res_f.data]
        # Requêtes d'amis
        res_r = supabase.table("friendships").select("user1").eq("user2", pseudo).eq("status", "pending").execute()
        requests = [r['user1'] for r in res_r.data]
        # Invitations Guilde
        res_g = supabase.table("guild_invites").select("guild_name").eq("target_user", pseudo).execute()
        guild_invs = [g['guild_name'] for g in res_g.data]
        
        socketio.emit('social_update', {
            'friends': friends,
            'friend_requests': requests,
            'guild_invites': guild_invs
        }, room=pseudo)
    except Exception as e:
        logging.error(f"Social update error: {e}")

@app.route('/')
def index():
    return render_template('index.html')

# === AUTHENTIFICATION ===
@socketio.on('login_action')
def auth(data):
    try:
        p, pwd, t = data['pseudo'].strip(), data['password'], data['type']
        res = supabase.table("users").select("*").eq("pseudo", p).execute()
        user = res.data[0] if res.data else None

        if t == 'register' and not user:
            hpw = bcrypt.generate_password_hash(pwd).decode('utf-8')
            supabase.table("users").insert({"pseudo": p, "password": hpw, "clicks": 0, "multiplier": 1}).execute()
            user = supabase.table("users").select("*").eq("pseudo", p).execute().data[0]

        if user and bcrypt.check_password_hash(user['password'], pwd):
            connected_users[request.sid] = {'pseudo': p, 'mult': user['multiplier'], 'guild': user.get('guild_name')}
            join_room(p)
            emit('login_ok', {
                'pseudo': p, 'clicks': user['clicks'], 'mult': user['multiplier'], 
                'guild': user.get('guild_name'), 'rank': get_rank(user['clicks'])
            })
            send_leaderboard(request.sid)
            update_social_data(p)
        else:
            emit('error', "Identifiants invalides")
    except Exception as e:
        emit('error', f"Erreur: {e}")

# === JEU ===
@socketio.on('add_click')
def click():
    u = connected_users.get(request.sid)
    if u:
        res = supabase.table("users").select("clicks").eq("pseudo", u['pseudo']).execute()
        nv = res.data[0]['clicks'] + u['mult']
        supabase.table("users").update({"clicks": nv}).eq("pseudo", u['pseudo']).execute()
        emit('update_score', {'clicks': nv, 'rank': get_rank(nv)})
        if nv % 10 == 0: send_leaderboard(request.sid)
        if u.get('guild'):
            supabase.rpc('increment_guild_clicks', {'guild_name': u['guild'], 'amount': u['mult']}).execute()

@socketio.on('buy_upgrade')
def upgrade():
    u = connected_users.get(request.sid)
    if u:
        res = supabase.table("users").select("clicks", "multiplier").eq("pseudo", u['pseudo']).execute()
        c, m = res.data[0]['clicks'], res.data[0]['multiplier']
        cost = m * 100
        if c >= cost:
            supabase.table("users").update({"clicks": c-cost, "multiplier": m+1}).eq("pseudo", u['pseudo']).execute()
            connected_users[request.sid]['mult'] = m+1
            emit('update_full_state', {'clicks': c-cost, 'mult': m+1})
            emit('success', "Booster acheté !")
        else: emit('error', "Pas assez de clics")

# === GUILDES ===
@socketio.on('create_guild')
def create_g(data):
    u = connected_users.get(request.sid)
    n = data['name'].strip()
    try:
        supabase.table("guilds").insert({"name": n, "founder": u['pseudo']}).execute()
        supabase.table("users").update({"guild_name": n}).eq("pseudo", u['pseudo']).execute()
        connected_users[request.sid]['guild'] = n
        emit('update_full_state', {'guild': n})
        emit('success', f"Guilde {n} créée !")
    except: emit('error', "Nom déjà pris")

@socketio.on('get_guilds')
def get_guilds():
    res = supabase.table("guilds").select("*").order("total_clicks", desc=True).execute()
    emit('guild_list', {'guilds': res.data})

@socketio.on('get_guild_data')
def get_guild_data():
    u = connected_users.get(request.sid)
    if u and u.get('guild'):
        g = supabase.table("guilds").select("*").eq("name", u['guild']).execute().data[0]
        m = supabase.table("users").select("pseudo", "clicks").eq("guild_name", u['guild']).execute().data
        g['members'] = m
        emit('guild_data', g)

# === SOCIAL & CHAT ===
@socketio.on('msg')
def handle_msg(data):
    u = connected_users.get(request.sid)
    if u: emit('new_msg', {'p': u['pseudo'], 'm': data['m'][:200]}, broadcast=True)

@socketio.on('friend_request')
def fr_req(data):
    u = connected_users.get(request.sid)
    target = data['target'].strip()
    if target != u['pseudo']:
        supabase.table("friendships").insert({"user1": u['pseudo'], "user2": target, "status": "pending"}).execute()
        update_social_data(target)
        emit('success', "Demande envoyée")

@socketio.on('respond_friend')
def resp_fr(data):
    u = connected_users.get(request.sid)
    if data['action'] == 'accept':
        supabase.table("friendships").update({"status": "accepted"}).match({"user1": data['target'], "user2": u['pseudo']}).execute()
    else:
        supabase.table("friendships").delete().match({"user1": data['target'], "user2": u['pseudo']}).execute()
    update_social_data(u['pseudo'])
    update_social_data(data['target'])

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000)

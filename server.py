import os
import logging
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room
from flask_bcrypt import Bcrypt
from supabase import create_client, Client
import eventlet

# Nécessaire pour Render
app = Flask(__name__)
app.config['SECRET_KEY'] = 'tokacookies_ultra_secret_2026'
bcrypt = Bcrypt(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# Configuration Supabase
SUPABASE_URL = "https://rzzhkdzjnjeeoqbtlles.supabase.co"
SUPABASE_KEY = "sb_secret_wjlaZm7VdO5VgO6UfqEn0g_FgbwC-ao"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

connected_users = {} # {sid: {pseudo, mult, guild}}

def get_rank(clicks):
    ranks = [(0, "Vagabond"), (1000, "Citoyen"), (5000, "Chevalier"), (20000, "Seigneur"), (100000, "Roi"), (500000, "Empereur")]
    for threshold, title in reversed(ranks):
        if clicks >= threshold: return title
    return "Vagabond"

def send_relative_leaderboard(sid):
    u = connected_users.get(sid)
    if u:
        try:
            res = supabase.rpc('get_relative_leaderboard', {'player_pseudo': u['pseudo']}).execute()
            socketio.emit('leaderboard_update', {'players': res.data}, room=sid)
        except: pass

def update_social_data(pseudo):
    try:
        # Amis acceptés
        res_f = supabase.table("friendships").select("*").or_(f'user1.eq."{pseudo}",user2.eq."{pseudo}"').eq("status", "accepted").execute()
        friends = [f['user2'] if f['user1'] == pseudo else f['user1'] for f in res_f.data]
        # Demandes d'amis en attente
        res_r = supabase.table("friendships").select("user1").eq("user2", pseudo).eq("status", "pending").execute()
        req_friends = [r['user1'] for r in res_r.data]
        # Demandes de guilde (si le joueur est fondateur)
        res_g = supabase.table("guilds").select("name").eq("founder", pseudo).execute()
        join_reqs = []
        is_leader = False
        if res_g.data:
            is_leader = True
            g_name = res_g.data[0]['name']
            res_jr = supabase.table("guild_join_requests").select("requester").eq("guild_name", g_name).execute()
            join_reqs = [r['requester'] for r in res_jr.data]

        socketio.emit('social_update', {
            'friends': friends,
            'friend_requests': req_friends,
            'guild_join_requests': join_reqs,
            'is_leader': is_leader
        }, room=pseudo)
    except: pass

@app.route('/')
def index(): return render_template('index.html')

@socketio.on('login_action')
def auth(data):
    p, pwd, t = data['pseudo'].strip(), data['password'], data['type']
    res = supabase.table("users").select("*").eq("pseudo", p).execute()
    user = res.data[0] if res.data else None

    if t == 'register' and not user:
        hpw = bcrypt.generate_password_hash(pwd).decode('utf-8')
        supabase.table("users").insert({"pseudo": p, "password": hpw}).execute()
        user = supabase.table("users").select("*").eq("pseudo", p).execute().data[0]

    if user and bcrypt.check_password_hash(user['password'], pwd):
        connected_users[request.sid] = {'pseudo': p, 'mult': user['multiplier'], 'guild': user.get('guild_name')}
        join_room(p)
        emit('login_ok', {'pseudo': p, 'clicks': user['clicks'], 'mult': user['multiplier'], 'guild': user.get('guild_name'), 'rank': get_rank(user['clicks'])})
        send_relative_leaderboard(request.sid)
        update_social_data(p)
    else: emit('error', "Identifiants incorrects")

@socketio.on('add_click')
def click():
    u = connected_users.get(request.sid)
    if u:
        nv = supabase.table("users").select("clicks").eq("pseudo", u['pseudo']).execute().data[0]['clicks'] + u['mult']
        supabase.table("users").update({"clicks": nv}).eq("pseudo", u['pseudo']).execute()
        emit('update_score', {'clicks': nv, 'rank': get_rank(nv)})
        if nv % 7 == 0: send_relative_leaderboard(request.sid) # Update leaderboard régulièrement
        if u.get('guild'):
            supabase.rpc('increment_guild_clicks', {'guild_name': u['guild'], 'amount': u['mult']}).execute()

@socketio.on('buy_upgrade')
def upgrade():
    u = connected_users.get(request.sid)
    if u:
        res = supabase.table("users").select("clicks", "multiplier").eq("pseudo", u['pseudo']).execute().data[0]
        cost = res['multiplier'] * 100
        if res['clicks'] >= cost:
            nm = res['multiplier'] + 1
            supabase.table("users").update({"clicks": res['clicks']-cost, "multiplier": nm}).eq("pseudo", u['pseudo']).execute()
            connected_users[request.sid]['mult'] = nm
            emit('update_full_state', {'clicks': res['clicks']-cost, 'mult': nm})
            emit('success', "Booster Activé !")
        else: emit('error', "Pas assez de clics")

# --- LOGIQUE GUILDES ---
@socketio.on('create_guild')
def create_g(data):
    u = connected_users.get(request.sid)
    if u.get('guild'): return emit('error', "Quitte ta guilde d'abord")
    name = data['name'].strip()
    try:
        supabase.table("guilds").insert({"name": name, "founder": u['pseudo']}).execute()
        supabase.table("users").update({"guild_name": name}).eq("pseudo", u['pseudo']).execute()
        connected_users[request.sid]['guild'] = name
        emit('update_full_state', {'guild': name})
        update_social_data(u['pseudo'])
    except: emit('error', "Nom déjà pris")

@socketio.on('ask_join_guild')
def ask_g(data):
    u = connected_users.get(request.sid)
    if u.get('guild'): return emit('error', "Tu as déjà une guilde")
    try:
        supabase.table("guild_join_requests").insert({"guild_name": data['name'], "requester": u['pseudo']}).execute()
        emit('success', "Demande envoyée au chef !")
        # Notifier le chef
        res = supabase.table("guilds").select("founder").eq("name", data['name']).execute()
        if res.data: 
            socketio.emit('notif_sound', room=res.data[0]['founder'])
            update_social_data(res.data[0]['founder'])
    except: emit('error', "Demande déjà en cours")

@socketio.on('respond_guild_join')
def resp_g(data):
    u = connected_users.get(request.sid)
    if data['action'] == 'accept':
        supabase.table("users").update({"guild_name": u['guild']}).eq("pseudo", data['requester']).execute()
        socketio.emit('notif_sound', room=data['requester'])
    supabase.table("guild_join_requests").delete().match({"guild_name": u['guild'], "requester": data['requester']}).execute()
    update_social_data(u['pseudo'])

@socketio.on('leave_guild')
def leave():
    u = connected_users.get(request.sid)
    supabase.table("users").update({"guild_name": None}).eq("pseudo", u['pseudo']).execute()
    connected_users[request.sid]['guild'] = None
    emit('update_full_state', {'guild': None})
    emit('success', "Tu es maintenant en solo.")

@socketio.on('get_guilds')
def list_g():
    res = supabase.table("guilds").select("*").order("total_clicks", desc=True).limit(20).execute()
    emit('guild_list', {'guilds': res.data})

# --- LOGIQUE SOCIAL ---
@socketio.on('send_friend_request')
def f_req(data):
    u = connected_users.get(request.sid)
    try:
        supabase.table("friendships").insert({"user1": u['pseudo'], "user2": data['target'], "status": "pending"}).execute()
        socketio.emit('notif_sound', room=data['target'])
        update_social_data(data['target'])
        emit('success', "Demande envoyée")
    except: emit('error', "Impossible d'ajouter")

@socketio.on('respond_friend_request')
def f_resp(data):
    u = connected_users.get(request.sid)
    if data['action'] == 'accept':
        supabase.table("friendships").update({"status": "accepted"}).match({"user1": data['target'], "user2": u['pseudo']}).execute()
    else:
        supabase.table("friendships").delete().match({"user1": data['target'], "user2": u['pseudo']}).execute()
    update_social_data(u['pseudo'])

if __name__ == '__main__':
    # Indispensable pour Render
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port)

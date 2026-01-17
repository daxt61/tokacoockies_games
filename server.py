import os
import eventlet
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room
from flask_bcrypt import Bcrypt
from supabase import create_client, Client

# --- INITIALISATION ---
app = Flask(__name__)
app.config['SECRET_KEY'] = 'tokacookies_v15_final'
bcrypt = Bcrypt(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# --- CONFIG SUPABASE ---
SUPABASE_URL = "https://rzzhkdzjnjeeoqbtlles.supabase.co"
SUPABASE_KEY = "sb_secret_wjlaZm7VdO5VgO6UfqEn0g_FgbwC-ao"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Stockage sessions { sid: { 'pseudo': '...', 'mult': 1, 'auto': 0, 'guild': '...' } }
connected_users = {}

# --- BOUCLE AUTO-CLICK (CPS) ---
def auto_click_loop():
    """Donne des clics automatiquement toutes les secondes"""
    while True:
        socketio.sleep(1)
        for sid, u in list(connected_users.items()):
            cps = u.get('auto', 0)
            if cps > 0:
                try:
                    # On récupère, incrémente et update
                    res = supabase.table("users").select("clicks").eq("pseudo", u['pseudo']).execute()
                    if res.data:
                        nv = res.data[0]['clicks'] + cps
                        supabase.table("users").update({"clicks": nv}).eq("pseudo", u['pseudo']).execute()
                        # Envoi au joueur
                        socketio.emit('update_score', {'clicks': nv, 'rank': get_rank_title(nv)}, room=sid)
                except: pass

# Lancement du thread en arrière-plan
eventlet.spawn(auto_click_loop)

# --- FONCTIONS SYNC ---
def get_rank_title(clicks):
    ranks = [(0, "Vagabond"), (1000, "Citoyen"), (5000, "Chevalier"), (20000, "Seigneur"), (100000, "Roi"), (500000, "Empereur")]
    for threshold, title in reversed(ranks):
        if clicks >= threshold: return title
    return "Vagabond"

def broadcast_leaderboard_to_all():
    for sid, data in connected_users.items():
        try:
            res = supabase.rpc('get_relative_leaderboard', {'player_pseudo': data['pseudo']}).execute()
            socketio.emit('leaderboard_update', {'players': res.data}, room=sid)
        except: pass

def update_social_data(pseudo):
    try:
        res_f = supabase.table("friendships").select("*").or_(f'user1.eq."{pseudo}",user2.eq."{pseudo}"').eq("status", "accepted").execute()
        friends_list = [f['user2'] if f['user1'] == pseudo else f['user1'] for f in res_f.data]
        res_r = supabase.table("friendships").select("user1").eq("user2", pseudo).eq("status", "pending").execute()
        pending_requests = [r['user1'] for r in res_r.data]
        res_g = supabase.table("guilds").select("name").eq("founder", pseudo).execute()
        is_leader = bool(res_g.data)
        guild_reqs = []
        if is_leader:
            g_name = res_g.data[0]['name']
            res_jr = supabase.table("guild_join_requests").select("requester").eq("guild_name", g_name).execute()
            guild_reqs = [r['requester'] for r in res_jr.data]
        socketio.emit('social_update', {'friends': friends_list, 'friend_requests': pending_requests, 'guild_join_requests': guild_reqs, 'is_leader': is_leader}, room=pseudo)
        return friends_list
    except: return []

# --- ROUTES & SOCKETS ---
@app.route('/')
def index(): return render_template('index.html')

@socketio.on('login_action')
def handle_auth(data):
    p, pwd, t = data['pseudo'].strip(), data['password'], data['type']
    res = supabase.table("users").select("*").eq("pseudo", p).execute()
    user = res.data[0] if res.data else None
    if t == 'register' and not user:
        hpw = bcrypt.generate_password_hash(pwd).decode('utf-8')
        supabase.table("users").insert({"pseudo": p, "password": hpw}).execute()
        user = supabase.table("users").select("*").eq("pseudo", p).execute().data[0]
    if user and bcrypt.check_password_hash(user['password'], pwd):
        join_room(p)
        friends = update_social_data(p)
        connected_users[request.sid] = {'pseudo': p, 'mult': user.get('multiplier', 1), 'auto': user.get('auto_clicks', 0), 'guild': user.get('guild_name')}
        emit('login_ok', {'pseudo': p, 'clicks': user['clicks'], 'mult': user.get('multiplier', 1), 'auto': user.get('auto_clicks', 0), 'guild': user.get('guild_name'), 'rank': get_rank_title(user['clicks'])})
        broadcast_leaderboard_to_all()
    else: emit('error', "Identifiants invalides")

@socketio.on('add_click')
def handle_click():
    u = connected_users.get(request.sid)
    if u:
        res = supabase.table("users").select("clicks").eq("pseudo", u['pseudo']).execute()
        nv = res.data[0]['clicks'] + u['mult']
        supabase.table("users").update({"clicks": nv}).eq("pseudo", u['pseudo']).execute()
        emit('update_score', {'clicks': nv, 'rank': get_rank_title(nv)})
        if nv % 20 == 0: broadcast_leaderboard_to_all()
        if u.get('guild'):
            try: supabase.rpc('increment_guild_clicks', {'guild_name': str(u['guild']), 'amount': int(u['mult'])}).execute()
            except: pass

@socketio.on('buy_upgrade')
def handle_up(data):
    u = connected_users.get(request.sid)
    if not u: return
    res = supabase.table("users").select("*").eq("pseudo", u['pseudo']).execute().data[0]
    
    if data['type'] == 'mult':
        cost = res['multiplier'] * 100
        if res['clicks'] >= cost:
            nm, nv = res['multiplier'] + 1, res['clicks'] - cost
            supabase.table("users").update({"clicks": nv, "multiplier": nm}).eq("pseudo", u['pseudo']).execute()
            connected_users[request.sid]['mult'] = nm
            emit('update_full_state', {'clicks': nv, 'mult': nm})
        else: emit('error', "Pas assez de clics")
    
    elif data['type'] == 'auto':
        current_auto = res.get('auto_clicks', 0)
        cost = (current_auto + 1) * 500
        if res['clicks'] >= cost:
            na, nv = current_auto + 1, res['clicks'] - cost
            supabase.table("users").update({"clicks": nv, "auto_clicks": na}).eq("pseudo", u['pseudo']).execute()
            connected_users[request.sid]['auto'] = na
            emit('update_full_state', {'clicks': nv, 'auto': na})
        else: emit('error', "Pas assez de clics")

@socketio.on('send_chat')
def handle_chat(data):
    u = connected_users.get(request.sid)
    if u and data.get('msg'):
        socketio.emit('new_chat', {'user': u['pseudo'], 'text': data['msg'][:150], 'guild': u['guild']}, broadcast=True)

@socketio.on('send_friend_request')
def handle_f_req(data):
    u = connected_users.get(request.sid)
    target = data['target'].strip()
    if not u or target == u['pseudo']: return
    try:
        supabase.table("friendships").insert({"user1": u['pseudo'], "user2": target, "status": "pending"}).execute()
        socketio.emit('notif_sound', room=target)
        update_social_data(target)
        emit('success', "Demande envoyée")
    except: emit('error', "Impossible d'envoyer")

@socketio.on('respond_friend_request')
def handle_f_resp(data):
    u = connected_users.get(request.sid)
    if data['action'] == 'accept':
        supabase.table("friendships").update({"status": "accepted"}).match({"user1": data['target'], "user2": u['pseudo']}).execute()
    else:
        supabase.table("friendships").delete().match({"user1": data['target'], "user2": u['pseudo']}).execute()
    update_social_data(u['pseudo']); update_social_data(data['target'])

@socketio.on('create_guild')
def handle_create_g(data):
    u = connected_users.get(request.sid)
    name = data['name'].strip()
    try:
        supabase.table("guilds").insert({"name": name, "founder": u['pseudo']}).execute()
        supabase.table("users").update({"guild_name": name}).eq("pseudo", u['pseudo']).execute()
        connected_users[request.sid]['guild'] = name
        emit('update_full_state', {'guild': name})
    except: emit('error', "Erreur Guilde")

@socketio.on('get_guilds')
def handle_get_g():
    res = supabase.table("guilds").select("*").order("total_clicks", desc=True).limit(20).execute()
    emit('guild_list', {'guilds': res.data})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port)

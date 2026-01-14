import os
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room
from flask_bcrypt import Bcrypt
from supabase import create_client, Client

app = Flask(__name__)
app.config['SECRET_KEY'] = 'sorek_v9_ultra_secure'
bcrypt = Bcrypt(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# === CONFIG SUPABASE ===
SUPABASE_URL = "https://rzzhkdzjnjeeoqbtlles.supabase.co"
SUPABASE_KEY = "sb_secret_wjlaZm7VdO5VgO6UfqEn0g_FgbwC-ao"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

connected_users = {}  # {sid: {pseudo, mult, guild}}

def get_rank(clicks):
    ranks = [(0, "Vagabond"), (1000, "Citoyen"), (5000, "Chevalier"), (20000, "Seigneur"), (100000, "Roi"), (500000, "Empereur")]
    for threshold, title in reversed(ranks):
        if clicks >= threshold: return title
    return "Vagabond"

def update_social_data(pseudo):
    """Envoie les listes d'amis, requêtes et invitations à un joueur précis"""
    try:
        # Amis acceptés
        res_f = supabase.table("friendships").select("*").eq("status", "accepted").or_(f'user1.eq."{pseudo}",user2.eq."{pseudo}"').execute()
        friends = [f['user2'] if f['user1'] == pseudo else f['user1'] for f in res_f.data]
        
        # Demandes d'amis reçues
        res_r = supabase.table("friendships").select("*").eq("status", "pending").eq("user2", pseudo).execute()
        requests = [r['user1'] for r in res_r.data]

        # Invitations de guilde
        res_g = supabase.table("guild_invites").select("*").eq("target_user", pseudo).execute()
        guild_invites = [g['guild_name'] for g in res_g.data]

        socketio.emit('social_update', {
            'friends': friends,
            'friend_requests': requests,
            'guild_invites': guild_invites
        }, room=pseudo)
    except: pass

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
            join_room(p)
            connected_users[request.sid] = {'pseudo': p, 'mult': user['multiplier'], 'guild': user.get('guild_name')}
            emit('login_ok', {'pseudo': p, 'clicks': user['clicks'], 'mult': user['multiplier'], 'guild': user.get('guild_name'), 'rank': get_rank(user['clicks'])})
            update_social_data(p)
        else: emit('error', "Échec de connexion")
    except: emit('error', "Erreur serveur")

@socketio.on('add_click')
def add_click():
    u = connected_users.get(request.sid)
    if u:
        res = supabase.table("users").select("clicks").eq("pseudo", u['pseudo']).execute()
        nv = res.data[0]['clicks'] + u['mult']
        supabase.table("users").update({"clicks": nv}).eq("pseudo", u['pseudo']).execute()
        emit('update_score', {'clicks': nv, 'rank': get_rank(nv)})

@socketio.on('send_friend_request')
def send_friend(data):
    u = connected_users.get(request.sid)
    target = data['target'].strip()
    if not u or target == u['pseudo']: return
    try:
        supabase.table("friendships").insert({"user1": u['pseudo'], "user2": target, "status": "pending"}).execute()
        update_social_data(target)
        emit('success', f"Demande envoyée à {target}")
    except: emit('error', "Joueur introuvable")

@socketio.on('respond_friend_request')
def resp_friend(data):
    u = connected_users.get(request.sid)
    target, action = data['target'], data['action']
    if action == 'accept':
        supabase.table("friendships").update({"status": "accepted"}).match({"user1": target, "user2": u['pseudo']}).execute()
    else:
        supabase.table("friendships").delete().match({"user1": target, "user2": u['pseudo']}).execute()
    update_social_data(u['pseudo'])
    update_social_data(target)

@socketio.on('invite_to_guild')
def inv_guild(data):
    u = connected_users.get(request.sid)
    if u.get('guild'):
        supabase.table("guild_invites").insert({"guild_name": u['guild'], "target_user": data['target']}).execute()
        update_social_data(data['target'])
        emit('success', "Invitation envoyée")

@socketio.on('respond_guild_invite')
def resp_guild(data):
    u = connected_users.get(request.sid)
    g_name, action = data['guild_name'], data['action']
    if action == 'accept':
        supabase.table("users").update({"guild_name": g_name}).eq("pseudo", u['pseudo']).execute()
        connected_users[request.sid]['guild'] = g_name
        emit('update_full_state', {'guild': g_name})
    supabase.table("guild_invites").delete().match({"guild_name": g_name, "target_user": u['pseudo']}).execute()
    update_social_data(u['pseudo'])

@socketio.on('msg')
def handle_msg(data):
    u = connected_users.get(request.sid)
    if u: emit('new_msg', {'p': u['pseudo'], 'm': data['m'][:200]}, broadcast=True)

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000)









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

logging.basicConfig(level=logging.INFO)

# === CONFIG SUPABASE ===
SUPABASE_URL = "https://rzzhkdzjnjeeoqbtlles.supabase.co"
SUPABASE_KEY = "sb_secret_wjlaZm7VdO5VgO6UfqEn0g_FgbwC-ao"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

connected_users = {}

def get_rank(clicks):
    ranks = [(0, "Vagabond"), (1000, "Citoyen"), (5000, "Chevalier"), (20000, "Seigneur"), (100000, "Roi"), (500000, "Empereur")]
    for threshold, title in reversed(ranks):
        if clicks >= threshold: return title
    return "Vagabond"

# Envoi du classement relatif (5 au-dessus, 4 en-dessous)
def send_relative_leaderboard(sid):
    try:
        u = connected_users.get(sid)
        if u:
            res = supabase.rpc('get_relative_leaderboard', {'player_pseudo': u['pseudo']}).execute()
            socketio.emit('leaderboard_update', {'players': res.data}, room=sid)
    except Exception as e:
        print(f"LB Error: {e}")

def update_social_data(pseudo):
    try:
        # Amis
        res_f = supabase.table("friendships").select("*").or_(f'user1.eq."{pseudo}",user2.eq."{pseudo}"').eq("status", "accepted").execute()
        friends = [f['user2'] if f['user1'] == pseudo else f['user1'] for f in res_f.data]
        
        # Demandes d'amis reçues
        res_r = supabase.table("friendships").select("user1").eq("user2", pseudo).eq("status", "pending").execute()
        requests = [r['user1'] for r in res_r.data]
        
        # Invitations Guilde (reçues)
        res_gi = supabase.table("guild_invites").select("guild_name").eq("target_user", pseudo).execute()
        guild_invites = [g['guild_name'] for g in res_gi.data]

        # Si le joueur est CHEF de guilde, récupérer les demandes d'adhésion
        guild_join_requests = []
        user_guild_info = supabase.table("guilds").select("name").eq("founder", pseudo).execute()
        if user_guild_info.data:
            my_guild_name = user_guild_info.data[0]['name']
            res_gjr = supabase.table("guild_join_requests").select("requester").eq("guild_name", my_guild_name).execute()
            guild_join_requests = [r['requester'] for r in res_gjr.data]

        socketio.emit('social_update', {
            'friends': friends,
            'friend_requests': requests,
            'guild_invites': guild_invites,
            'guild_join_requests': guild_join_requests,
            'is_leader': len(user_guild_info.data) > 0
        }, room=pseudo)
    except Exception as e:
        print(f"Social Update Error: {e}")

@app.route('/')
def index(): return render_template('index.html')

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
            emit('login_ok', {'pseudo': p, 'clicks': user['clicks'], 'mult': user['multiplier'], 'guild': user.get('guild_name'), 'rank': get_rank(user['clicks'])})
            send_relative_leaderboard(request.sid)
            update_social_data(p)
        else: emit('error', "Mauvais identifiants")
    except Exception as e: emit('error', str(e))

@socketio.on('add_click')
def click():
    u = connected_users.get(request.sid)
    if u:
        res = supabase.table("users").select("clicks").eq("pseudo", u['pseudo']).execute()
        nv = res.data[0]['clicks'] + u['mult']
        supabase.table("users").update({"clicks": nv}).eq("pseudo", u['pseudo']).execute()
        emit('update_score', {'clicks': nv, 'rank': get_rank(nv)})
        
        # Mise à jour du classement temps réel (fréquente)
        if nv % 5 == 0: send_relative_leaderboard(request.sid)
        
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
            emit('success', "Booster acheté !") # Pas de son ici, géré par le client
        else: emit('error', "Pas assez de clics")

# === GUILDES ===
@socketio.on('create_guild')
def create_g(data):
    u = connected_users.get(request.sid)
    if u.get('guild'): return emit('error', "Quitte ta guilde d'abord !")
    n = data['name'].strip()
    try:
        supabase.table("guilds").insert({"name": n, "founder": u['pseudo']}).execute()
        supabase.table("users").update({"guild_name": n}).eq("pseudo", u['pseudo']).execute()
        connected_users[request.sid]['guild'] = n
        emit('update_full_state', {'guild': n})
        emit('success', f"Guilde {n} fondée !")
        update_social_data(u['pseudo'])
    except: emit('error', "Nom déjà pris")

@socketio.on('ask_join_guild')
def ask_join(data):
    u = connected_users.get(request.sid)
    if u.get('guild'): return emit('error', "Tu as déjà une guilde !")
    target_guild = data['name']
    
    # Vérif si demande existe déjà
    check = supabase.table("guild_join_requests").select("*").match({"guild_name": target_guild, "requester": u['pseudo']}).execute()
    if check.data: return emit('error', "Demande déjà en attente")

    supabase.table("guild_join_requests").insert({"guild_name": target_guild, "requester": u['pseudo']}).execute()
    emit('success', f"Demande envoyée à {target_guild}")
    
    # Notifier le chef
    founder_res = supabase.table("guilds").select("founder").eq("name", target_guild).execute()
    if founder_res.data:
        founder = founder_res.data[0]['founder']
        socketio.emit('notif_sound', room=founder) # Son notif
        update_social_data(founder)

@socketio.on('respond_guild_join')
def resp_join(data):
    # Action du CHEF DE GUILDE
    u = connected_users.get(request.sid)
    requester = data['requester']
    action = data['action']
    my_guild = u.get('guild') # Supposons que le chef est dans sa guilde

    if action == 'accept':
        supabase.table("users").update({"guild_name": my_guild}).eq("pseudo", requester).execute()
        supabase.table("guild_join_requests").delete().match({"guild_name": my_guild, "requester": requester}).execute()
        # Notifier le nouveau membre (s'il est co)
        # On pourrait parcourir connected_users pour mettre à jour son état
        for sid, user in connected_users.items():
            if user['pseudo'] == requester:
                user['guild'] = my_guild
                emit('update_full_state', {'guild': my_guild}, room=sid)
                emit('success', f"Tu as rejoint {my_guild} !", room=sid)
                emit('notif_sound', room=sid)
    else:
        supabase.table("guild_join_requests").delete().match({"guild_name": my_guild, "requester": requester}).execute()
    
    update_social_data(u['pseudo'])

@socketio.on('leave_guild') # Trahir
def leave_g():
    u = connected_users.get(request.sid)
    if u.get('guild'):
        # Si c'est le chef, attention (ici on simplifie, il part juste)
        supabase.table("users").update({"guild_name": None}).eq("pseudo", u['pseudo']).execute()
        old_guild = u['guild']
        connected_users[request.sid]['guild'] = None
        emit('update_full_state', {'guild': None})
        emit('success', f"Tu as trahi {old_guild} !")

@socketio.on('get_guilds')
def get_guilds():
    res = supabase.table("guilds").select("*").order("total_clicks", desc=True).limit(50).execute()
    emit('guild_list', {'guilds': res.data})

# === SOCIAL ===
@socketio.on('send_friend_request')
def friend_req(data):
    u = connected_users.get(request.sid)
    target = data['target']
    supabase.table("friendships").insert({"user1": u['pseudo'], "user2": target, "status": "pending"}).execute()
    emit('success', "Demande envoyée")
    socketio.emit('notif_sound', room=target)
    update_social_data(target)

@socketio.on('respond_friend_request')
def friend_resp(data):
    u = connected_users.get(request.sid)
    if data['action'] == 'accept':
        supabase.table("friendships").update({"status": "accepted"}).match({"user1": data['target'], "user2": u['pseudo']}).execute()
    else:
        supabase.table("friendships").delete().match({"user1": data['target'], "user2": u['pseudo']}).execute()
    update_social_data(u['pseudo'])
    update_social_data(data['target'])

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000)

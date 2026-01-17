import os
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room
from flask_bcrypt import Bcrypt
from supabase import create_client, Client
import eventlet

# --- INITIALISATION ---
app = Flask(__name__)
app.config['SECRET_KEY'] = 'tokacookies_v13_ultra'
bcrypt = Bcrypt(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# --- CONFIG SUPABASE ---
SUPABASE_URL = "https://rzzhkdzjnjeeoqbtlles.supabase.co"
SUPABASE_KEY = "sb_secret_wjlaZm7VdO5VgO6UfqEn0g_FgbwC-ao"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Stockage sessions { sid: { 'pseudo': '...', 'mult': 1, 'guild': '...', 'friends': [] } }
connected_users = {}

# --- FONCTIONS SYNC ---

def get_rank_title(clicks):
    ranks = [(0, "Vagabond"), (1000, "Citoyen"), (5000, "Chevalier"), (20000, "Seigneur"), (100000, "Roi"), (500000, "Empereur")]
    for threshold, title in reversed(ranks):
        if clicks >= threshold: return title
    return "Vagabond"

def broadcast_leaderboard_to_all():
    """Sync le classement pour tous les joueurs en ligne"""
    for sid, data in connected_users.items():
        try:
            res = supabase.rpc('get_relative_leaderboard', {'player_pseudo': data['pseudo']}).execute()
            socketio.emit('leaderboard_update', {'players': res.data}, room=sid)
        except: pass

def update_social_data(pseudo):
    """Logique sociale corrigée : Filtre les amis et les demandes séparément"""
    try:
        # 1. Amis RÉELS (status = accepted)
        # On cherche où l'utilisateur est soit l'envoyeur (user1) soit le receveur (user2)
        res_f = supabase.table("friendships").select("*").or_(f'user1.eq."{pseudo}",user2.eq."{pseudo}"').eq("status", "accepted").execute()
        
        friends_list = []
        for f in res_f.data:
            # On récupère le nom de l'autre personne
            friend_name = f['user2'] if f['user1'] == pseudo else f['user1']
            if friend_name not in friends_list:
                friends_list.append(friend_name)
        
        # 2. Demandes en ATTENTE (status = pending ET on est le receveur user2)
        res_r = supabase.table("friendships").select("user1").eq("user2", pseudo).eq("status", "pending").execute()
        pending_requests = [r['user1'] for r in res_r.data]
        
        # 3. Stats Guilde (si fondateur)
        res_g = supabase.table("guilds").select("name").eq("founder", pseudo).execute()
        is_leader = False
        guild_reqs = []
        if res_g.data:
            is_leader = True
            g_name = res_g.data[0]['name']
            res_jr = supabase.table("guild_join_requests").select("requester").eq("guild_name", g_name).execute()
            guild_reqs = [r['requester'] for r in res_jr.data]

        # Envoi des données propres au joueur
        socketio.emit('social_update', {
            'friends': friends_list,
            'friend_requests': pending_requests,
            'guild_join_requests': guild_reqs,
            'is_leader': is_leader
        }, room=pseudo)
        
        return friends_list
    except Exception as e:
        print(f"Social Sync Error: {e}")
        return []

# --- ROUTES & SOCKETS ---

@app.route('/')
def index():
    return render_template('index.html')

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
        connected_users[request.sid] = {
            'pseudo': p, 
            'mult': user['multiplier'], 
            'guild': user.get('guild_name'),
            'friends': friends
        }
        emit('login_ok', {
            'pseudo': p, 'clicks': user['clicks'], 'mult': user['multiplier'], 
            'guild': user.get('guild_name'), 'rank': get_rank_title(user['clicks'])
        })
        broadcast_leaderboard_to_all()
    else:
        emit('error', "Identifiants incorrects")

@socketio.on('add_click')
def handle_click():
    u = connected_users.get(request.sid)
    if u:
        res = supabase.table("users").select("clicks").eq("pseudo", u['pseudo']).execute()
        nv = res.data[0]['clicks'] + u['mult']
        supabase.table("users").update({"clicks": nv}).eq("pseudo", u['pseudo']).execute()
        emit('update_score', {'clicks': nv, 'rank': get_rank_title(nv)})
        
        # Broadcast classement
        if nv % 5 == 0: broadcast_leaderboard_to_all()
        
        # Score de guilde (Correction PGRST203 intégrée)
        if u.get('guild'):
            try:
                supabase.rpc('increment_guild_clicks', {
                    'guild_name': str(u['guild']), 
                    'amount': int(u['mult'])
                }).execute()
            except: pass

@socketio.on('send_chat')
def handle_chat(data):
    u = connected_users.get(request.sid)
    if u and data.get('msg'):
        socketio.emit('new_chat', {
            'user': u['pseudo'], 'text': data['msg'][:150], 'guild': u['guild']
        }, broadcast=True)

# --- LOGIQUE SOCIALE CORRIGÉE ---

@socketio.on('send_friend_request')
def handle_f_req(data):
    u = connected_users.get(request.sid)
    target = data['target'].strip()
    if not u or target == u['pseudo']: return

    # Vérification anti-doublon (A->B ou B->A existe déjà ?)
    check = supabase.table("friendships").select("*").or_(
        f'and(user1.eq."{u["pseudo"]}",user2.eq."{target}"),and(user1.eq."{target}",user2.eq."{u["pseudo"]}")'
    ).execute()

    if check.data:
        return emit('error', "Déjà amis ou demande en attente")

    try:
        supabase.table("friendships").insert({
            "user1": u['pseudo'], "user2": target, "status": "pending"
        }).execute()
        socketio.emit('notif_sound', room=target)
        update_social_data(target)
        emit('success', "Demande envoyée !")
    except:
        emit('error', "Joueur introuvable")

@socketio.on('respond_friend_request')
def handle_f_resp(data):
    u = connected_users.get(request.sid)
    target = data['target']
    
    if data['action'] == 'accept':
        # On valide la ligne où on est user2 (receveur)
        supabase.table("friendships").update({"status": "accepted"}).match({
            "user1": target, "user2": u['pseudo']
        }).execute()
        emit('success', f"Tu es ami avec {target}")
    else:
        # On décline/supprime
        supabase.table("friendships").delete().match({
            "user1": target, "user2": u['pseudo']
        }).execute()

    # Refresh pour les deux
    update_social_data(u['pseudo'])
    update_social_data(target)
    # Sync de la liste d'amis en mémoire pour le chat
    if request.sid in connected_users:
        connected_users[request.sid]['friends'] = update_social_data(u['pseudo'])

# --- GUILDES (Inchangé) ---

@socketio.on('create_guild')
def handle_create_g(data):
    u = connected_users.get(request.sid)
    if u.get('guild'): return emit('error', "Quitte ta guilde d'abord")
    name = data['name'].strip()
    try:
        supabase.table("guilds").insert({"name": name, "founder": u['pseudo']}).execute()
        supabase.table("users").update({"guild_name": name}).eq("pseudo", u['pseudo']).execute()
        connected_users[request.sid]['guild'] = name
        emit('update_full_state', {'guild': name})
    except: emit('error', "Nom déjà pris")

@socketio.on('ask_join_guild')
def handle_ask_g(data):
    u = connected_users.get(request.sid)
    try:
        supabase.table("guild_join_requests").insert({"guild_name": data['name'], "requester": u['pseudo']}).execute()
        res = supabase.table("guilds").select("founder").eq("name", data['name']).execute()
        if res.data:
            socketio.emit('notif_sound', room=res.data[0]['founder'])
            update_social_data(res.data[0]['founder'])
        emit('success', "Demande envoyée")
    except: emit('error', "Déjà demandé")

@socketio.on('respond_guild_join')
def handle_g_resp(data):
    u = connected_users.get(request.sid)
    if data['action'] == 'accept':
        supabase.table("users").update({"guild_name": u['guild']}).eq("pseudo", data['requester']).execute()
    supabase.table("guild_join_requests").delete().match({"guild_name": u['guild'], "requester": data['requester']}).execute()
    update_social_data(u['pseudo'])

@socketio.on('leave_guild')
def handle_l_g():
    u = connected_users.get(request.sid)
    supabase.table("users").update({"guild_name": None}).eq("pseudo", u['pseudo']).execute()
    connected_users[request.sid]['guild'] = None
    emit('update_full_state', {'guild': None})

@socketio.on('get_guilds')
def handle_get_g():
    res = supabase.table("guilds").select("*").order("total_clicks", desc=True).limit(20).execute()
    emit('guild_list', {'guilds': res.data})

@socketio.on('buy_upgrade')
def handle_up():
    u = connected_users.get(request.sid)
    if u:
        res = supabase.table("users").select("clicks", "multiplier").eq("pseudo", u['pseudo']).execute().data[0]
        cost = res['multiplier'] * 100
        if res['clicks'] >= cost:
            nm, nv = res['multiplier'] + 1, res['clicks'] - cost
            supabase.table("users").update({"clicks": nv, "multiplier": nm}).eq("pseudo", u['pseudo']).execute()
            connected_users[request.sid]['mult'] = nm
            emit('update_full_state', {'clicks': nv, 'mult': nm})
        else: emit('error', "Pas assez de clics")

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port)

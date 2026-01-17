import os
import logging
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room
from flask_bcrypt import Bcrypt
from supabase import create_client, Client
import eventlet

# --- INITIALISATION ---
app = Flask(__name__)
app.config['SECRET_KEY'] = 'tokacookies_supreme_2026'
bcrypt = Bcrypt(app)
# Configuration spécifique pour Render et les WebSockets
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# --- CONFIG SUPABASE ---
SUPABASE_URL = "https://rzzhkdzjnjeeoqbtlles.supabase.co"
SUPABASE_KEY = "sb_secret_wjlaZm7VdO5VgO6UfqEn0g_FgbwC-ao"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Stockage des utilisateurs actifs
# Format: { sid: { 'pseudo': '...', 'mult': 1, 'guild': '...', 'friends': [] } }
connected_users = {}

# --- FONCTIONS UTILES ---

def get_rank_title(clicks):
    """Calcule le titre honorifique selon les clics"""
    ranks = [(0, "Vagabond"), (1000, "Citoyen"), (5000, "Chevalier"), (20000, "Seigneur"), (100000, "Roi"), (500000, "Empereur")]
    for threshold, title in reversed(ranks):
        if clicks >= threshold: return title
    return "Vagabond"

def broadcast_leaderboard_to_all():
    """Recalcule et envoie le classement relatif à TOUS les joueurs connectés"""
    for sid, data in connected_users.items():
        try:
            # Appel de la fonction SQL 'get_relative_leaderboard' pour chaque joueur
            res = supabase.rpc('get_relative_leaderboard', {'player_pseudo': data['pseudo']}).execute()
            socketio.emit('leaderboard_update', {'players': res.data}, room=sid)
        except Exception as e:
            print(f"Erreur Sync Leaderboard pour {data.get('pseudo')}: {e}")

def update_social_data(pseudo):
    """Récupère les amis, les demandes et le statut de guilde pour notifier un joueur"""
    try:
        # Amis acceptés
        res_f = supabase.table("friendships").select("*").or_(f'user1.eq."{pseudo}",user2.eq."{pseudo}"').eq("status", "accepted").execute()
        friends = [f['user2'] if f['user1'] == pseudo else f['user1'] for f in res_f.data]
        
        # Demandes d'amis reçues
        res_r = supabase.table("friendships").select("user1").eq("user2", pseudo).eq("status", "pending").execute()
        friend_reqs = [r['user1'] for r in res_r.data]
        
        # Gestion Guilde
        res_g = supabase.table("guilds").select("name").eq("founder", pseudo).execute()
        guild_reqs = []
        is_leader = False
        if res_g.data:
            is_leader = True
            g_name = res_g.data[0]['name']
            res_jr = supabase.table("guild_join_requests").select("requester").eq("guild_name", g_name).execute()
            guild_reqs = [r['requester'] for r in res_jr.data]

        socketio.emit('social_update', {
            'friends': friends,
            'friend_requests': friend_reqs,
            'guild_join_requests': guild_reqs,
            'is_leader': is_leader
        }, room=pseudo)
        return friends
    except: return []

# --- ROUTES & SOCKETS ---

@app.route('/')
def index():
    return render_template('index.html')

@socketio.on('login_action')
def handle_auth(data):
    p, pwd, t = data['pseudo'].strip(), data['password'], data['type']
    res = supabase.table("users").select("*").eq("pseudo", p).execute()
    user = res.data[0] if res.data else None

    # Inscription
    if t == 'register' and not user:
        hpw = bcrypt.generate_password_hash(pwd).decode('utf-8')
        supabase.table("users").insert({"pseudo": p, "password": hpw}).execute()
        user = supabase.table("users").select("*").eq("pseudo", p).execute().data[0]

    # Connexion
    if user and bcrypt.check_password_hash(user['password'], pwd):
        # Initialisation de la session
        friends = update_social_data(p)
        connected_users[request.sid] = {
            'pseudo': p, 
            'mult': user['multiplier'], 
            'guild': user.get('guild_name'),
            'friends': friends
        }
        join_room(p) # Le joueur rejoint sa propre room pour les notifs privées
        
        emit('login_ok', {
            'pseudo': p, 
            'clicks': user['clicks'], 
            'mult': user['multiplier'], 
            'guild': user.get('guild_name'), 
            'rank': get_rank_title(user['clicks'])
        })
        broadcast_leaderboard_to_all() # Update tout le monde quand un nouveau arrive
    else:
        emit('error', "Pseudo ou mot de passe incorrect.")

@socketio.on('add_click')
def handle_click():
    u = connected_users.get(request.sid)
    if u:
        # 1. Mise à jour score en BDD
        res = supabase.table("users").select("clicks").eq("pseudo", u['pseudo']).execute()
        new_val = res.data[0]['clicks'] + u['mult']
        supabase.table("users").update({"clicks": new_val}).eq("pseudo", u['pseudo']).execute()
        
        # 2. Update visuel direct pour le joueur qui a cliqué
        emit('update_score', {'clicks': new_val, 'rank': get_rank_title(new_val)})
        
        # 3. Synchronisation Temps Réel (Broadcast)
        # % 1 signifie que le classement s'actualise pour TOUT LE MONDE à chaque clic.
        if new_val % 1 == 0:
            broadcast_leaderboard_to_all()
            
        # 4. Si en guilde, on ajoute au score de guilde
        # BIEN ALIGNÉ ICI : cela doit être à l'intérieur du "if u:"
        if u.get('guild'):
            supabase.rpc('increment_guild_clicks', {
                'guild_name': str(u['guild']), 
                'amount': int(u['mult'])
            }).execute()

@socketio.on('buy_upgrade')
def handle_upgrade():
    u = connected_users.get(request.sid)
    if u:
        res = supabase.table("users").select("clicks", "multiplier").eq("pseudo", u['pseudo']).execute().data[0]
        cost = res['multiplier'] * 100
        if res['clicks'] >= cost:
            nm = res['multiplier'] + 1
            nv = res['clicks'] - cost
            supabase.table("users").update({"clicks": nv, "multiplier": nm}).eq("pseudo", u['pseudo']).execute()
            connected_users[request.sid]['mult'] = nm
            emit('update_full_state', {'clicks': nv, 'mult': nm})
            broadcast_leaderboard_to_all()
        else:
            emit('error', f"Il te manque {cost - res['clicks']} clics !")

# --- SYSTÈME DE CHAT ---
@socketio.on('send_chat')
def handle_chat(data):
    u = connected_users.get(request.sid)
    if u and data.get('msg'):
        msg_payload = {
            'user': u['pseudo'],
            'text': data['msg'][:150], # Limite à 150 caractères
            'guild': u['guild']
        }
        # Broadcast global à tout le monde
        socketio.emit('new_chat', msg_payload, broadcast=True)

# --- GESTION DES GUILDES ---
@socketio.on('create_guild')
def handle_create_g(data):
    u = connected_users.get(request.sid)
    if u.get('guild'): return emit('error', "Tu dois quitter ta guilde actuelle.")
    name = data['name'].strip()
    try:
        supabase.table("guilds").insert({"name": name, "founder": u['pseudo']}).execute()
        supabase.table("users").update({"guild_name": name}).eq("pseudo", u['pseudo']).execute()
        connected_users[request.sid]['guild'] = name
        emit('update_full_state', {'guild': name})
        emit('success', f"Guilde {name} créée !")
    except:
        emit('error', "Ce nom de guilde est déjà pris.")

@socketio.on('ask_join_guild')
def handle_join_request(data):
    u = connected_users.get(request.sid)
    if u.get('guild'): return emit('error', "Tu es déjà en guilde.")
    try:
        supabase.table("guild_join_requests").insert({"guild_name": data['name'], "requester": u['pseudo']}).execute()
        emit('success', "Demande de recrutement envoyée !")
        # Notifier le chef
        res = supabase.table("guilds").select("founder").eq("name", data['name']).execute()
        if res.data:
            socketio.emit('notif_sound', room=res.data[0]['founder'])
            update_social_data(res.data[0]['founder'])
    except:
        emit('error', "Demande déjà en cours.")

@socketio.on('respond_guild_join')
def handle_guild_resp(data):
    u = connected_users.get(request.sid) # Le chef
    if data['action'] == 'accept':
        supabase.table("users").update({"guild_name": u['guild']}).eq("pseudo", data['requester']).execute()
        socketio.emit('notif_sound', room=data['requester'])
    supabase.table("guild_join_requests").delete().match({"guild_name": u['guild'], "requester": data['requester']}).execute()
    update_social_data(u['pseudo'])

@socketio.on('leave_guild')
def handle_leave():
    u = connected_users.get(request.sid)
    supabase.table("users").update({"guild_name": None}).eq("pseudo", u['pseudo']).execute()
    connected_users[request.sid]['guild'] = None
    emit('update_full_state', {'guild': None})
    emit('success', "Tu as quitté ta guilde.")

@socketio.on('get_guilds')
def handle_get_g():
    res = supabase.table("guilds").select("*").order("total_clicks", desc=True).limit(20).execute()
    emit('guild_list', {'guilds': res.data})

# --- SOCIAL ---
@socketio.on('send_friend_request')
def handle_f_req(data):
    u = connected_users.get(request.sid)
    try:
        supabase.table("friendships").insert({"user1": u['pseudo'], "user2": data['target'], "status": "pending"}).execute()
        socketio.emit('notif_sound', room=data['target'])
        update_social_data(data['target'])
        emit('success', "Demande d'ami envoyée !")
    except:
        emit('error', "Utilisateur introuvable ou déjà ami.")

@socketio.on('respond_friend_request')
def handle_f_resp(data):
    u = connected_users.get(request.sid)
    if data['action'] == 'accept':
        supabase.table("friendships").update({"status": "accepted"}).match({"user1": data['target'], "user2": u['pseudo']}).execute()
        # On met à jour la liste locale d'amis pour le chat
        connected_users[request.sid]['friends'] = update_social_data(u['pseudo'])
    else:
        supabase.table("friendships").delete().match({"user1": data['target'], "user2": u['pseudo']}).execute()
    update_social_data(u['pseudo'])

# --- RUN ---
if __name__ == '__main__':
    # Indispensable pour que Render trouve le bon port
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port)

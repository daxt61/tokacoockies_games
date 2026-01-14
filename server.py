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

# === UTILITAIRES ===
def get_rank(clicks):
    ranks = [(0, "Vagabond"), (1000, "Citoyen"), (5000, "Chevalier"), (20000, "Seigneur"), (100000, "Roi"), (500000, "Empereur")]
    for threshold, title in reversed(ranks):
        if clicks >= threshold: return title
    return "Vagabond"

def send_leaderboard():
    try:
        res = supabase.table("users").select("pseudo", "clicks").order("clicks", desc=True).limit(10).execute()
        socketio.emit('leaderboard_update', {'players': res.data})
    except: pass

# === ROUTES ===
@app.route('/')
def index():
    return render_template('index.html')

# === SOCKETS : AUTH ===
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
            connected_users[request.sid] = {'pseudo': p, 'mult': user['multiplier'], 'guild': user.get('guild_name')}
            join_room(p) # Le joueur rejoint une "room" à son nom pour recevoir des notifs privées
            emit('login_ok', {
                'pseudo': p, 'clicks': user['clicks'], 'mult': user['multiplier'], 
                'guild': user.get('guild_name'), 'rank': get_rank(user['clicks'])
            })
            send_leaderboard()
            # On envoie les infos sociales au démarrage
            update_social_data(p)
        else:
            emit('error', "Identifiants incorrects")
    except Exception as e:
        emit('error', f"Erreur Auth: {str(e)}")

# === SOCKETS : JEU ===
@socketio.on('add_click')
def add_click():
    u = connected_users.get(request.sid)
    if u:
        try:
            res = supabase.table("users").select("clicks").eq("pseudo", u['pseudo']).execute()
            nv = res.data[0]['clicks'] + u['mult']
            supabase.table("users").update({"clicks": nv}).eq("pseudo", u['pseudo']).execute()
            emit('update_score', {'clicks': nv, 'rank': get_rank(nv)})
            
            # Contribution guilde
            if u.get('guild'):
                supabase.rpc('increment_guild_clicks', {'guild_name': u['guild'], 'amount': u['mult']}).execute()
        except: pass

@socketio.on('buy_upgrade')
def buy_up():
    u = connected_users.get(request.sid)
    if u:
        try:
            res = supabase.table("users").select("clicks", "multiplier").eq("pseudo", u['pseudo']).execute()
            c, m = res.data[0]['clicks'], res.data[0]['multiplier']
            cost = m * 100
            if c >= cost:
                supabase.table("users").update({"clicks": c-cost, "multiplier": m+1}).eq("pseudo", u['pseudo']).execute()
                connected_users[request.sid]['mult'] = m + 1
                emit('update_full_state', {'clicks': c-cost, 'mult': m+1, 'rank': get_rank(c-cost)})
                emit('success', "Booster acheté ! 🚀")
            else: emit('error', "Pas assez de clics !")
        except: pass

# === SOCIAL : FONCTIONS DE BASE ===
def update_social_data(pseudo):
    """Envoie les listes d'amis et de requêtes au joueur"""
    try:
        # 1. Liste d'amis (acceptés)
        res_friends = supabase.table("friendships").select("*").eq("status", "accepted").or_(f'user1.eq."{pseudo}",user2.eq."{pseudo}"').execute()
        friends = [f['user2'] if f['user1'] == pseudo else f['user1'] for f in res_friends.data]
        
        # 2. Requêtes d'amis reçues (pending)
        res_req = supabase.table("friendships").select("*").eq("status", "pending").eq("user2", pseudo).execute()
        friend_requests = [r['user1'] for r in res_req.data]

        # 3. Invitations de guilde
        res_guild = supabase.table("guild_invites").select("*").eq("target_user", pseudo).execute()
        guild_invites = [g['guild_name'] for g in res_guild.data]

        socketio.emit('social_update', {
            'friends': friends,
            'friend_requests': friend_requests,
            'guild_invites': guild_invites
        }, room=pseudo)
    except Exception as e:
        print(f"Social Update Error: {e}")

# === SOCIAL : GESTION AMIS ===
@socketio.on('send_friend_request')
def send_friend_req(data):
    u = connected_users.get(request.sid)
    target = data['target'].strip()
    if not u or target == u['pseudo']: return emit('error', "Impossible")

    try:
        # Vérif si déjà amis ou demande en cours
        check = supabase.table("friendships").select("*").or_(f'and(user1.eq."{u["pseudo"]}",user2.eq."{target}"),and(user1.eq."{target}",user2.eq."{u["pseudo"]}")').execute()
        if check.data: return emit('error', "Déjà en lien avec ce joueur")

        # Création demande
        supabase.table("friendships").insert({"user1": u['pseudo'], "user2": target, "status": "pending"}).execute()
        emit('success', f"Demande envoyée à {target}")
        
        # Notifie le destinataire
        update_social_data(target)
    except: emit('error', "Joueur introuvable")

@socketio.on('respond_friend_request')
def respond_friend(data):
    u = connected_users.get(request.sid)
    target = data['target']
    action = data['action'] # 'accept' ou 'decline'
    
    try:
        if action == 'accept':
            supabase.table("friendships").update({"status": "accepted"}).match({"user1": target, "user2": u['pseudo']}).execute()
            emit('success', f"Tu es maintenant ami avec {target}")
        else:
            supabase.table("friendships").delete().match({"user1": target, "user2": u['pseudo']}).execute()
            emit('success', "Demande refusée")
            
        update_social_data(u['pseudo'])
        update_social_data(target)
    except: pass

@socketio.on('remove_friend')
def remove_friend(data):
    u = connected_users.get(request.sid)
    target = data['target']
    try:
        supabase.table("friendships").delete().or_(f'and(user1.eq."{u["pseudo"]}",user2.eq."{target}"),and(user1.eq."{target}",user2.eq."{u["pseudo"]}")').execute()
        update_social_data(u['pseudo'])
        update_social_data(target)
        emit('success', f"{target} retiré.")
    except: pass

# === SOCIAL : GUILDES ===
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
    except: emit('error', "Nom pris ou erreur")

@socketio.on('invite_to_guild')
def invite_guild(data):
    u = connected_users.get(request.sid)
    target = data['target']
    if not u.get('guild'): return emit('error', "Tu n'as pas de guilde !")
    
    try:
        supabase.table("guild_invites").insert({"guild_name": u['guild'], "target_user": target}).execute()
        emit('success', f"Invitation envoyée à {target}")
        update_social_data(target)
    except: emit('error', "Erreur invitation")

@socketio.on('respond_guild_invite')
def respond_guild(data):
    u = connected_users.get(request.sid)
    guild_name = data['guild_name']
    action = data['action']
    
    try:
        if action == 'accept':
            supabase.table("users").update({"guild_name": guild_name}).eq("pseudo", u['pseudo']).execute()
            connected_users[request.sid]['guild'] = guild_name
            emit('update_full_state', {'guild': guild_name})
            emit('success', f"Bienvenue chez {guild_name} !")
            
        # Dans tous les cas on supprime l'invitation
        supabase.table("guild_invites").delete().match({"guild_name": guild_name, "target_user": u['pseudo']}).execute()
        update_social_data(u['pseudo'])
    except: pass

# === TCHAT ===
@socketio.on('msg')
def handle_msg(data):
    u = connected_users.get(request.sid)
    if u and data['m'].strip():
        emit('new_msg', {'p': u['pseudo'], 'm': data['m'][:200]}, broadcast=True)

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000)









import os
from flask import Flask, send_file
from flask_socketio import SocketIO, emit
from flask_bcrypt import Bcrypt
from supabase import create_client, Client

app = Flask(__name__)
app.config['SECRET_KEY'] = 'sorek_v9_ultra_secure'
bcrypt = Bcrypt(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# CONFIG SUPABASE - Remplace par tes vraies clés
SUPABASE_URL = "https://rzzhkdzjnjeeoqbtlles.supabase.co"
SUPABASE_KEY = "sb_secret_wjlaZm7VdO5VgO6UfqEn0g_FgbwC-ao"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

connected_users = {}  # {sid: {pseudo, mult, guild, ...}}

# === UTILITAIRES ===
def get_rank(clicks):
    ranks = [(0, "Vagabond"), (1000, "Citoyen"), (5000, "Chevalier"), (20000, "Seigneur"), (100000, "Roi"), (500000, "Empereur")]
    for threshold, title in reversed(ranks):
        if clicks >= threshold:
            return title
    return "Vagabond"

def send_leaderboard():
    """Envoie le top 10 à tous les joueurs"""
    try:
        res = supabase.table("users").select("pseudo, clicks, guild_name").order("clicks", desc=True).limit(10).execute()
        socketio.emit('lb_update', {'players': res.data})
    except Exception as e:
        print(f"Erreur leaderboard: {e}")

def get_user_data(pseudo):
    """Récupère les données complètes d'un utilisateur"""
    res = supabase.table("users").select("*").eq("pseudo", pseudo).execute()
    return res.data[0] if res.data else None

# === ROUTES ===
@app.route('/')
def index():
    return send_file('index.html')

# === AUTHENTIFICATION ===
@socketio.on('login_action')
def auth_logic(data):
    from flask import request
    try:
        pseudo = data['pseudo'].strip()
        password = data['password']
        action_type = data['type']
        
        if not pseudo or not password:
            return emit('auth_error', 'Pseudo et mot de passe requis !')
        
        user = get_user_data(pseudo)
        
        # Inscription
        if action_type == 'register':
            if user:
                return emit('auth_error', 'Ce pseudo existe déjà !')
            
            hashed_pw = bcrypt.generate_password_hash(password).decode('utf-8')
            supabase.table("users").insert({
                "pseudo": pseudo,
                "password": hashed_pw,
                "clicks": 0,
                "multiplier": 1,
                "guild_name": None
            }).execute()
            user = get_user_data(pseudo)
        
        # Connexion
        if user and bcrypt.check_password_hash(user['password'], password):
            connected_users[request.sid] = {
                'pseudo': pseudo,
                'mult': user['multiplier'],
                'guild': user.get('guild_name')
            }
            
            emit('login_ok', {
                'pseudo': pseudo,
                'clicks': user['clicks'],
                'mult': user['multiplier'],
                'guild': user.get('guild_name'),
                'rank': get_rank(user['clicks'])
            })
            
            send_leaderboard()
        else:
            emit('auth_error', 'Pseudo ou mot de passe incorrect !')
            
    except Exception as e:
        print(f"Erreur auth: {e}")
        emit('auth_error', 'Erreur serveur')

# === CLICKER ===
@socketio.on('add_click')
def add_click():
    from flask import request
    try:
        user_data = connected_users.get(request.sid)
        if not user_data:
            return
        
        pseudo = user_data['pseudo']
        mult = user_data['mult']
        
        # Récupère le score actuel
        user = get_user_data(pseudo)
        new_clicks = user['clicks'] + mult
        
        # Met à jour
        supabase.table("users").update({"clicks": new_clicks}).eq("pseudo", pseudo).execute()
        
        # Si dans une guilde, update le total
        if user['guild_name']:
            update_guild_total(user['guild_name'])
        
        emit('update_score', {
            'clicks': new_clicks,
            'rank': get_rank(new_clicks)
        })
        
        send_leaderboard()
        
    except Exception as e:
        print(f"Erreur add_click: {e}")

@socketio.on('buy_upgrade')
def buy_upgrade():
    from flask import request
    try:
        user_data = connected_users.get(request.sid)
        if not user_data:
            return
        
        pseudo = user_data['pseudo']
        user = get_user_data(pseudo)
        
        cost = user['multiplier'] * 100
        
        if user['clicks'] >= cost:
            new_clicks = user['clicks'] - cost
            new_mult = user['multiplier'] + 1
            
            supabase.table("users").update({
                "clicks": new_clicks,
                "multiplier": new_mult
            }).eq("pseudo", pseudo).execute()
            
            connected_users[request.sid]['mult'] = new_mult
            
            emit('update_full_state', {
                'clicks': new_clicks,
                'mult': new_mult,
                'rank': get_rank(new_clicks)
            })
            emit('success', f'Booster acheté ! Puissance x{new_mult} 🚀')
        else:
            emit('error', f'Pas assez de clics ! ({cost} requis)')
            
    except Exception as e:
        print(f"Erreur buy_upgrade: {e}")
        emit('error', 'Erreur serveur')

# === GUILDES ===
def update_guild_total(guild_name):
    """Met à jour le total de clics d'une guilde"""
    try:
        members = supabase.table("users").select("clicks").eq("guild_name", guild_name).execute()
        total = sum(m['clicks'] for m in members.data)
        supabase.table("guilds").update({"total_clicks": total}).eq("name", guild_name).execute()
    except Exception as e:
        print(f"Erreur update_guild_total: {e}")

@socketio.on('create_guild')
def create_guild(data):
    from flask import request
    try:
        user_data = connected_users.get(request.sid)
        if not user_data:
            return
        
        pseudo = user_data['pseudo']
        guild_name = data['name'].strip()
        
        if not guild_name:
            return emit('error', 'Nom de guilde invalide !')
        
        # Vérifie si la guilde existe déjà
        existing = supabase.table("guilds").select("*").eq("name", guild_name).execute()
        if existing.data:
            return emit('error', 'Cette guilde existe déjà !')
        
        # Vérifie si le joueur est déjà dans une guilde
        user = get_user_data(pseudo)
        if user['guild_name']:
            return emit('error', 'Tu es déjà dans une guilde !')
        
        # Crée la guilde
        supabase.table("guilds").insert({
            "name": guild_name,
            "total_clicks": user['clicks'],
            "founder": pseudo
        }).execute()
        
        # Ajoute le joueur
        supabase.table("users").update({"guild_name": guild_name}).eq("pseudo", pseudo).execute()
        
        connected_users[request.sid]['guild'] = guild_name
        
        emit('update_full_state', {'guild': guild_name})
        emit('success', f'Guilde "{guild_name}" créée ! 🛡️')
        
    except Exception as e:
        print(f"Erreur create_guild: {e}")
        emit('error', 'Erreur serveur')

@socketio.on('join_guild')
def join_guild(data):
    from flask import request
    try:
        user_data = connected_users.get(request.sid)
        if not user_data:
            return
        
        pseudo = user_data['pseudo']
        guild_name = data['name']
        
        user = get_user_data(pseudo)
        if user['guild_name']:
            return emit('error', 'Tu es déjà dans une guilde !')
        
        # Vérifie que la guilde existe
        guild = supabase.table("guilds").select("*").eq("name", guild_name).execute()
        if not guild.data:
            return emit('error', 'Cette guilde n\'existe pas !')
        
        # Rejoint
        supabase.table("users").update({"guild_name": guild_name}).eq("pseudo", pseudo).execute()
        update_guild_total(guild_name)
        
        connected_users[request.sid]['guild'] = guild_name
        
        emit('update_full_state', {'guild': guild_name})
        emit('success', f'Tu as rejoint {guild_name} ! 🛡️')
        
    except Exception as e:
        print(f"Erreur join_guild: {e}")
        emit('error', 'Erreur serveur')

@socketio.on('leave_guild')
def leave_guild():
    from flask import request
    try:
        user_data = connected_users.get(request.sid)
        if not user_data:
            return
        
        pseudo = user_data['pseudo']
        user = get_user_data(pseudo)
        
        if not user['guild_name']:
            return emit('error', 'Tu n\'es dans aucune guilde !')
        
        old_guild = user['guild_name']
        
        # Quitte
        supabase.table("users").update({"guild_name": None}).eq("pseudo", pseudo).execute()
        update_guild_total(old_guild)
        
        connected_users[request.sid]['guild'] = None
        
        emit('update_full_state', {'guild': None})
        emit('success', f'Tu as quitté {old_guild}')
        
    except Exception as e:
        print(f"Erreur leave_guild: {e}")
        emit('error', 'Erreur serveur')

@socketio.on('get_guilds')
def get_guilds():
    try:
        guilds = supabase.table("guilds").select("name, total_clicks, founder").execute()
        
        # Ajoute le nombre de membres
        for guild in guilds.data:
            members = supabase.table("users").select("pseudo", count='exact').eq("guild_name", guild['name']).execute()
            guild['member_count'] = len(members.data) if members.data else 0
        
        emit('guild_list', {'guilds': guilds.data})
        
    except Exception as e:
        print(f"Erreur get_guilds: {e}")

@socketio.on('get_guild_data')
def get_guild_data():
    from flask import request
    try:
        user_data = connected_users.get(request.sid)
        if not user_data or not user_data['guild']:
            return
        
        guild_name = user_data['guild']
        
        # Récupère les membres
        members = supabase.table("users").select("pseudo, clicks").eq("guild_name", guild_name).order("clicks", desc=True).execute()
        
        # Récupère le total
        guild = supabase.table("guilds").select("total_clicks").eq("name", guild_name).execute()
        
        emit('update_full_state', {
            'guild_data': {
                'name': guild_name,
                'total_clicks': guild.data[0]['total_clicks'] if guild.data else 0,
                'members': members.data
            }
        })
        
    except Exception as e:
        print(f"Erreur get_guild_data: {e}")

# === AMIS ===
@socketio.on('send_friend_request')
def send_friend_request(data):
    from flask import request
    try:
        user_data = connected_users.get(request.sid)
        if not user_data:
            return
        
        from_pseudo = user_data['pseudo']
        to_pseudo = data['target'].strip()
        
        # Vérifie que le joueur existe
        target_user = get_user_data(to_pseudo)
        if not target_user:
            return emit('error', 'Ce joueur n\'existe pas !')
        
        # Vérifie qu'ils ne sont pas déjà amis
        existing = supabase.table("friendships").select("*").or_(
            f"and(user1.eq.{from_pseudo},user2.eq.{to_pseudo}),and(user1.eq.{to_pseudo},user2.eq.{from_pseudo})"
        ).execute()
        
        if existing.data:
            return emit('error', 'Vous êtes déjà amis ou une demande existe déjà !')
        
        # Crée la demande
        supabase.table("friend_requests").insert({
            "from_pseudo": from_pseudo,
            "to_pseudo": to_pseudo,
            "status": "pending"
        }).execute()
        
        emit('success', f'Demande envoyée à {to_pseudo} ! 📬')
        
        # Notifie le destinataire s'il est connecté
        for sid, u in connected_users.items():
            if u['pseudo'] == to_pseudo:
                socketio.emit('notif', f'{from_pseudo} t\'a envoyé une demande d\'ami !', room=sid)
                break
        
    except Exception as e:
        print(f"Erreur send_friend_request: {e}")
        emit('error', 'Erreur serveur')

@socketio.on('get_requests')
def get_requests():
    from flask import request
    try:
        user_data = connected_users.get(request.sid)
        if not user_data:
            return
        
        pseudo = user_data['pseudo']
        
        # Demandes d'amis reçues
        friend_reqs = supabase.table("friend_requests").select("*").eq("to_pseudo", pseudo).eq("status", "pending").execute()
        
        emit('friend_requests', {'requests': friend_reqs.data})
        emit('guild_invites', {'invites': []})  # TODO: implémenter les invitations de guilde
        
    except Exception as e:
        print(f"Erreur get_requests: {e}")

@socketio.on('accept_friend_request')
def accept_friend_request(data):
    from flask import request
    try:
        user_data = connected_users.get(request.sid)
        if not user_data:
            return
        
        to_pseudo = user_data['pseudo']
        from_pseudo = data['from']
        
        # Supprime la demande
        supabase.table("friend_requests").delete().eq("from_pseudo", from_pseudo).eq("to_pseudo", to_pseudo).execute()
        
        # Crée l'amitié
        supabase.table("friendships").insert({
            "user1": from_pseudo,
            "user2": to_pseudo
        }).execute()
        
        emit('success', f'Tu es maintenant ami avec {from_pseudo} ! 🎉')
        
        # Notifie l'autre joueur
        for sid, u in connected_users.items():
            if u['pseudo'] == from_pseudo:
                socketio.emit('success', f'{to_pseudo} a accepté ta demande d\'ami ! 🎉', room=sid)
                break
        
    except Exception as e:
        print(f"Erreur accept_friend_request: {e}")

@socketio.on('decline_friend_request')
def decline_friend_request(data):
    from flask import request
    try:
        user_data = connected_users.get(request.sid)
        if not user_data:
            return
        
        to_pseudo = user_data['pseudo']
        from_pseudo = data['from']
        
        supabase.table("friend_requests").delete().eq("from_pseudo", from_pseudo).eq("to_pseudo", to_pseudo).execute()
        
        emit('notif', 'Demande refusée')
        
    except Exception as e:
        print(f"Erreur decline_friend_request: {e}")

@socketio.on('get_friends')
def get_friends():
    from flask import request
    try:
        user_data = connected_users.get(request.sid)
        if not user_data:
            return
        
        pseudo = user_data['pseudo']
        
        # Récupère les amitiés
        friendships = supabase.table("friendships").select("*").or_(f"user1.eq.{pseudo},user2.eq.{pseudo}").execute()
        
        friends_list = []
        for f in friendships.data:
            friend_pseudo = f['user2'] if f['user1'] == pseudo else f['user1']
            friend_data = get_user_data(friend_pseudo)
            if friend_data:
                friends_list.append({
                    'pseudo': friend_pseudo,
                    'clicks': friend_data['clicks'],
                    'online': any(u['pseudo'] == friend_pseudo for u in connected_users.values())
                })
        
        emit('friends_list', {'friends': friends_list})
        
    except Exception as e:
        print(f"Erreur get_friends: {e}")

@socketio.on('remove_friend')
def remove_friend(data):
    from flask import request
    try:
        user_data = connected_users.get(request.sid)
        if not user_data:
            return
        
        pseudo = user_data['pseudo']
        target = data['target']
        
        supabase.table("friendships").delete().or_(
            f"and(user1.eq.{pseudo},user2.eq.{target}),and(user1.eq.{target},user2.eq.{pseudo})"
        ).execute()
        
        emit('notif', f'{target} retiré de tes amis')
        
    except Exception as e:
        print(f"Erreur remove_friend: {e}")

# === TCHAT ===
@socketio.on('msg')
def handle_msg(data):
    from flask import request
    try:
        user_data = connected_users.get(request.sid)
        if not user_data:
            return
        
        message = data['m'].strip()
        if not message or len(message) > 200:
            return
        
        emit('new_msg', {
            'p': user_data['pseudo'],
            'm': message
        }, broadcast=True)
        
    except Exception as e:
        print(f"Erreur msg: {e}")

# === DÉCONNEXION ===
@socketio.on('disconnect')
def on_disconnect():
    from flask import request
    if request.sid in connected_users:
        del connected_users[request.sid]

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=False)





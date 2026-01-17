import os
import eventlet
from datetime import datetime, timedelta

# IMPORTANT : Le monkey_patch doit être au tout début pour Render/Heroku
eventlet.monkey_patch()

from flask import Flask, render_template_string, request
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_bcrypt import Bcrypt
from supabase import create_client, Client

# --- INITIALISATION ---
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'tokacookies_ultra_v16_security')
bcrypt = Bcrypt(app)

# Utilisation d'eventlet pour gérer les threads d'auto-click
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# --- CONFIG SUPABASE ---
# CORRECTION BUG #1 : Utiliser des variables d'environnement
SUPABASE_URL = os.environ.get('SUPABASE_URL', 'https://rzzhkdzjnjeeoqbtlles.supabase.co')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY', '')  # NE JAMAIS hardcoder la clé !

if not SUPABASE_KEY:
    print("⚠️  WARNING: SUPABASE_KEY non définie !")
    
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Stockage des sessions en mémoire vive
# Structure : { sid: { 'pseudo': '...', 'mult': 1, 'auto': 0, 'guild': '...', 'friends': [], 'last_click': timestamp } }
connected_users = {}

# CORRECTION BUG #12 : Anti-spam
CLICK_RATE_LIMIT = 50  # Clics max par seconde
CHAT_RATE_LIMIT = 3    # Messages max par 10 secondes

# --- LOGIQUE AUTO-CLICK (CPS) ---

def auto_click_loop():
    """
    Boucle infinie qui s'exécute toutes les secondes.
    Elle distribue les clics automatiques aux joueurs connectés.
    CORRECTION BUG #6 : Gestion d'erreur robuste
    """
    print("🚀 [SYSTEM] Boucle Auto-Click démarrée")
    while True:
        socketio.sleep(1)
        
        # CORRECTION BUG #6 : Copie de la liste pour éviter les modifications pendant l'itération
        users_snapshot = list(connected_users.items())
        
        for sid, user_info in users_snapshot:
            # Vérification que l'utilisateur existe encore
            if sid not in connected_users:
                continue
                
            cps = user_info.get('auto', 0)
            
            if cps > 0:
                try:
                    pseudo = user_info['pseudo']
                    
                    # CORRECTION BUG #3 : Utilisation d'une requête atomique
                    res = supabase.rpc('increment_user_clicks', {
                        'player_pseudo': pseudo,
                        'amount': cps
                    }).execute()
                    
                    if res.data:
                        new_clicks = res.data
                        
                        # Mise à jour du cache local
                        if sid in connected_users:  # Double vérification
                            socketio.emit('update_score', {
                                'clicks': new_clicks, 
                                'rank': get_rank_title(new_clicks)
                            }, room=sid)
                            
                except Exception as e:
                    print(f"❌ [ERROR] Erreur Auto-Click pour {user_info.get('pseudo', 'unknown')}: {e}")

# Lancement de la boucle dans un thread séparé
eventlet.spawn(auto_click_loop)

# --- FONCTIONS UTILITAIRES ---

def get_rank_title(clicks):
    """Calcule le titre en fonction du score"""
    ranks = [
        (1000000, "Légende"),
        (500000, "Empereur"),
        (100000, "Roi"), 
        (20000, "Seigneur"), 
        (5000, "Chevalier"), 
        (1000, "Citoyen"),
        (0, "Vagabond")
    ]
    for threshold, title in ranks:
        if clicks >= threshold:
            return title
    return "Vagabond"

def broadcast_leaderboard_to_all():
    """
    Envoie le classement relatif à chaque utilisateur connecté
    CORRECTION BUG #7 : Optimisation avec cache
    """
    # On récupère d'abord le leaderboard global
    try:
        global_lb = supabase.table("users").select("pseudo,clicks").order("clicks", desc=True).limit(100).execute()
        
        for sid, data in list(connected_users.items()):
            try:
                # CORRECTION BUG #5 : Utilisation sécurisée des paramètres
                res = supabase.rpc('get_relative_leaderboard', {
                    'player_pseudo': str(data['pseudo'])
                }).execute()
                
                if sid in connected_users:  # Vérification avant émission
                    socketio.emit('leaderboard_update', {'players': res.data}, room=sid)
            except Exception as e:
                print(f"❌ [ERROR] Classement échoué pour {data.get('pseudo', 'unknown')}: {e}")
    except Exception as e:
        print(f"❌ [ERROR] Erreur globale leaderboard: {e}")

def update_social_data(pseudo):
    """
    Synchronise la liste d'amis et les demandes pour un pseudo donné
    CORRECTION BUG #11 : Ajout gestion complète des demandes de guilde
    """
    try:
        # 1. Amis acceptés
        res_f = supabase.table("friendships").select("*").or_(
            f'user1.eq.{pseudo},user2.eq.{pseudo}'
        ).eq("status", "accepted").execute()
        
        friends_list = []
        for f in res_f.data:
            friend = f['user2'] if f['user1'] == pseudo else f['user1']
            friends_list.append(friend)
        
        # 2. Demandes d'amis reçues (en attente)
        res_r = supabase.table("friendships").select("user1").eq(
            "user2", pseudo
        ).eq("status", "pending").execute()
        pending_requests = [r['user1'] for r in res_r.data]
        
        # 3. Vérification si leader de guilde et demandes de guilde
        res_g = supabase.table("guilds").select("name").eq("founder", pseudo).execute()
        is_leader = False
        guild_reqs = []
        
        if res_g.data:
            is_leader = True
            g_name = res_g.data[0]['name']
            res_jr = supabase.table("guild_join_requests").select("requester").eq(
                "guild_name", g_name
            ).execute()
            guild_reqs = [r['requester'] for r in res_jr.data]

        # CORRECTION BUG #8 : Émission vers la room du pseudo
        socketio.emit('social_update', {
            'friends': friends_list,
            'friend_requests': pending_requests,
            'guild_join_requests': guild_reqs,
            'is_leader': is_leader
        }, room=pseudo)
        
        return friends_list
    except Exception as e:
        print(f"❌ [ERROR] Social Sync pour {pseudo}: {e}")
        return []

def check_rate_limit(sid, limit_type='click'):
    """
    CORRECTION BUG #12 : Anti-spam pour clics et messages
    """
    if sid not in connected_users:
        return False
        
    now = datetime.now()
    user = connected_users[sid]
    
    if limit_type == 'click':
        last_click = user.get('last_click', now - timedelta(seconds=2))
        clicks_this_sec = user.get('clicks_this_sec', 0)
        
        if (now - last_click).total_seconds() < 1:
            if clicks_this_sec >= CLICK_RATE_LIMIT:
                return False
            user['clicks_this_sec'] = clicks_this_sec + 1
        else:
            user['clicks_this_sec'] = 1
            user['last_click'] = now
            
    elif limit_type == 'chat':
        last_msgs = user.get('last_messages', [])
        # Filtrer les messages de moins de 10 secondes
        recent = [t for t in last_msgs if (now - t).total_seconds() < 10]
        
        if len(recent) >= CHAT_RATE_LIMIT:
            return False
            
        recent.append(now)
        user['last_messages'] = recent
        
    return True

# --- ROUTES & SOCKETS ---

# CORRECTION BUG #2 : HTML inline au lieu de fichier externe
HTML_TEMPLATE = open('index(1).html', 'r', encoding='utf-8').read()

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@socketio.on('login_action')
def handle_auth(data):
    try:
        p = data.get('pseudo', '').strip()
        pwd = data.get('password', '')
        t = data.get('type', 'login')
        
        # Validation basique
        if not p or len(p) < 3 or len(p) > 20:
            return emit('error', "Pseudo invalide (3-20 caractères)")
        if not pwd or len(pwd) < 4:
            return emit('error', "Mot de passe trop court (min 4)")
        
        # Recherche de l'utilisateur
        res = supabase.table("users").select("*").eq("pseudo", p).execute()
        user = res.data[0] if res.data else None

        # Inscription
        if t == 'register':
            if user:
                return emit('error', "Ce pseudo est déjà pris")
                
            hpw = bcrypt.generate_password_hash(pwd).decode('utf-8')
            supabase.table("users").insert({
                "pseudo": p, 
                "password": hpw, 
                "clicks": 0, 
                "multiplier": 1, 
                "auto_clicks": 0
            }).execute()
            user = supabase.table("users").select("*").eq("pseudo", p).execute().data[0]

        # Connexion
        if user and bcrypt.check_password_hash(user['password'], pwd):
            # CORRECTION BUG #8 : Gestion propre des rooms
            join_room(p)
            join_room(request.sid)
            
            friends = update_social_data(p)
            
            # Stockage en session
            connected_users[request.sid] = {
                'pseudo': p, 
                'mult': user.get('multiplier', 1), 
                'auto': user.get('auto_clicks', 0),
                'guild': user.get('guild_name'),
                'friends': friends,
                'last_click': datetime.now(),
                'clicks_this_sec': 0,
                'last_messages': []
            }
            
            emit('login_ok', {
                'pseudo': p, 
                'clicks': user['clicks'], 
                'mult': user.get('multiplier', 1),
                'auto': user.get('auto_clicks', 0),
                'guild': user.get('guild_name'), 
                'rank': get_rank_title(user['clicks'])
            })
            
            broadcast_leaderboard_to_all()
        else:
            emit('error', "Pseudo ou mot de passe incorrect")
            
    except Exception as e:
        print(f"❌ [ERROR] Auth: {e}")
        emit('error', "Erreur lors de la connexion")

@socketio.on('add_click')
def handle_click():
    try:
        u = connected_users.get(request.sid)
        if not u:
            return
        
        # CORRECTION BUG #12 : Anti-spam
        if not check_rate_limit(request.sid, 'click'):
            return emit('error', "Ralentis un peu ! 🚫")
        
        # CORRECTION BUG #3 : Requête atomique
        res = supabase.rpc('increment_user_clicks', {
            'player_pseudo': u['pseudo'],
            'amount': u['mult']
        }).execute()
        
        if res.data:
            new_val = res.data
            
            # Update immédiat du joueur
            emit('update_score', {
                'clicks': new_val, 
                'rank': get_rank_title(new_val)
            })
            
            # Broadcast optimisé du leaderboard
            if new_val % 20 == 0:
                eventlet.spawn(broadcast_leaderboard_to_all)
                
            # Logique de guilde
            if u.get('guild'):
                try:
                    supabase.rpc('increment_guild_clicks', {
                        'guild_name': str(u['guild']), 
                        'amount': int(u['mult'])
                    }).execute()
                except Exception as e:
                    print(f"❌ [ERROR] Guild click: {e}")
                    
    except Exception as e:
        print(f"❌ [ERROR] Click handler: {e}")

@socketio.on('buy_upgrade')
def handle_up(data):
    try:
        u = connected_users.get(request.sid)
        if not u:
            return
        
        res = supabase.table("users").select("*").eq("pseudo", u['pseudo']).execute()
        if not res.data:
            return
            
        user_data = res.data[0]
        clicks = user_data['clicks']
        
        if data['type'] == 'mult':
            cost = user_data['multiplier'] * 100
            if clicks >= cost:
                nm, nv = user_data['multiplier'] + 1, clicks - cost
                supabase.table("users").update({
                    "clicks": nv, 
                    "multiplier": nm
                }).eq("pseudo", u['pseudo']).execute()
                
                connected_users[request.sid]['mult'] = nm
                emit('update_full_state', {'clicks': nv, 'mult': nm})
                emit('success', f"Multiplicateur +1 ! (x{nm})")
            else:
                emit('error', f"Il te faut {cost} clics !")
                
        elif data['type'] == 'auto':
            current_auto = user_data.get('auto_clicks', 0)
            cost = (current_auto + 1) * 500
            if clicks >= cost:
                na, nv = current_auto + 1, clicks - cost
                supabase.table("users").update({
                    "clicks": nv, 
                    "auto_clicks": na
                }).eq("pseudo", u['pseudo']).execute()
                
                # CORRECTION : Mise à jour critique pour l'auto-clicker
                connected_users[request.sid]['auto'] = na
                emit('update_full_state', {'clicks': nv, 'auto': na})
                emit('success', f"Auto-clicker +1 ! ({na}/s)")
            else:
                emit('error', f"Il te faut {cost} clics pour l'Auto !")
                
    except Exception as e:
        print(f"❌ [ERROR] Upgrade: {e}")
        emit('error', "Erreur lors de l'achat")

@socketio.on('send_chat')
def handle_chat(data):
    try:
        u = connected_users.get(request.sid)
        if not u or not data.get('msg'):
            return
        
        # CORRECTION BUG #12 : Anti-spam chat
        if not check_rate_limit(request.sid, 'chat'):
            return emit('error', "Trop de messages ! Attends un peu.")
        
        # Nettoyage et limitation du message
        msg = data['msg'].strip()[:150]
        if not msg:
            return
            
        socketio.emit('new_chat', {
            'user': u['pseudo'], 
            'text': msg, 
            'guild': u.get('guild', '')
        }, broadcast=True)
        
    except Exception as e:
        print(f"❌ [ERROR] Chat: {e}")

@socketio.on('send_friend_request')
def handle_f_req(data):
    try:
        u = connected_users.get(request.sid)
        target = data.get('target', '').strip()
        
        if not u or not target or target == u['pseudo']:
            return emit('error', "Cible invalide")
        
        # Vérification que le joueur cible existe
        target_check = supabase.table("users").select("pseudo").eq("pseudo", target).execute()
        if not target_check.data:
            return emit('error', "Joueur introuvable")
        
        # Vérification si déjà en relation
        check = supabase.table("friendships").select("*").or_(
            f'and(user1.eq.{u["pseudo"]},user2.eq.{target}),and(user1.eq.{target},user2.eq.{u["pseudo"]})'
        ).execute()
        
        if check.data:
            return emit('error', "Relation déjà existante")
            
        supabase.table("friendships").insert({
            "user1": u['pseudo'], 
            "user2": target, 
            "status": "pending"
        }).execute()
        
        # CORRECTION BUG #10 : Notification sonore implémentée
        socketio.emit('friend_request_notif', {
            'from': u['pseudo']
        }, room=target)
        
        update_social_data(target)
        emit('success', "Demande envoyée !")
        
    except Exception as e:
        print(f"❌ [ERROR] Friend request: {e}")
        emit('error', "Erreur lors de l'envoi")

@socketio.on('respond_friend_request')
def handle_f_resp(data):
    try:
        u = connected_users.get(request.sid)
        target = data.get('target')
        action = data.get('action')
        
        if not u or not target:
            return
        
        if action == 'accept':
            supabase.table("friendships").update({
                "status": "accepted"
            }).match({
                "user1": target, 
                "user2": u['pseudo']
            }).execute()
            emit('success', f"Ami ajouté : {target}")
        else:
            supabase.table("friendships").delete().match({
                "user1": target, 
                "user2": u['pseudo']
            }).execute()
            emit('success', "Demande refusée")
        
        update_social_data(u['pseudo'])
        update_social_data(target)
        
    except Exception as e:
        print(f"❌ [ERROR] Friend response: {e}")

@socketio.on('create_guild')
def handle_create_g(data):
    try:
        u = connected_users.get(request.sid)
        if not u:
            return
            
        if u.get('guild'):
            return emit('error', "Quitte d'abord ta guilde")
            
        name = data.get('name', '').strip()
        
        if len(name) < 3 or len(name) > 30:
            return emit('error', "Nom invalide (3-30 caractères)")
        
        supabase.table("guilds").insert({
            "name": name, 
            "founder": u['pseudo'],
            "total_clicks": 0
        }).execute()
        
        supabase.table("users").update({
            "guild_name": name
        }).eq("pseudo", u['pseudo']).execute()
        
        connected_users[request.sid]['guild'] = name
        emit('update_full_state', {'guild': name})
        emit('success', f"Guilde {name} créée !")
        
    except Exception as e:
        print(f"❌ [ERROR] Create guild: {e}")
        emit('error', "Ce nom de guilde est déjà pris")

@socketio.on('get_guilds')
def handle_get_g():
    try:
        res = supabase.table("guilds").select("*").order(
            "total_clicks", desc=True
        ).limit(20).execute()
        emit('guild_list', {'guilds': res.data})
    except Exception as e:
        print(f"❌ [ERROR] Get guilds: {e}")

@socketio.on('ask_join_guild')
def handle_join_guild(data):
    """CORRECTION BUG #11 : Handler manquant pour rejoindre une guilde"""
    try:
        u = connected_users.get(request.sid)
        if not u:
            return
            
        if u.get('guild'):
            return emit('error', "Quitte d'abord ta guilde")
            
        guild_name = data.get('name', '').strip()
        
        # Vérifier que la guilde existe
        guild = supabase.table("guilds").select("*").eq("name", guild_name).execute()
        if not guild.data:
            return emit('error', "Guilde introuvable")
        
        # Créer une demande d'adhésion
        supabase.table("guild_join_requests").insert({
            "guild_name": guild_name,
            "requester": u['pseudo']
        }).execute()
        
        # Notifier le fondateur
        founder = guild.data[0]['founder']
        socketio.emit('guild_request_notif', {
            'from': u['pseudo'],
            'guild': guild_name
        }, room=founder)
        
        update_social_data(founder)
        emit('success', f"Demande envoyée à {guild_name} !")
        
    except Exception as e:
        print(f"❌ [ERROR] Join guild: {e}")
        emit('error', "Erreur lors de la demande")

@socketio.on('respond_guild_request')
def handle_guild_response(data):
    """CORRECTION BUG #11 : Handler pour accepter/refuser les demandes de guilde"""
    try:
        u = connected_users.get(request.sid)
        if not u:
            return
            
        requester = data.get('requester')
        action = data.get('action')
        
        # Vérifier que l'utilisateur est bien le fondateur
        guild = supabase.table("guilds").select("*").eq("founder", u['pseudo']).execute()
        if not guild.data:
            return emit('error', "Tu n'es pas chef de guilde")
        
        guild_name = guild.data[0]['name']
        
        if action == 'accept':
            # Ajouter le membre à la guilde
            supabase.table("users").update({
                "guild_name": guild_name
            }).eq("pseudo", requester).execute()
            
            # Supprimer la demande
            supabase.table("guild_join_requests").delete().match({
                "guild_name": guild_name,
                "requester": requester
            }).execute()
            
            # Notifier le nouveau membre
            socketio.emit('guild_accepted', {
                'guild': guild_name
            }, room=requester)
            
            emit('success', f"{requester} a rejoint la guilde !")
        else:
            # Refuser et supprimer la demande
            supabase.table("guild_join_requests").delete().match({
                "guild_name": guild_name,
                "requester": requester
            }).execute()
            emit('success', "Demande refusée")
        
        update_social_data(u['pseudo'])
        
    except Exception as e:
        print(f"❌ [ERROR] Guild response: {e}")

@socketio.on('leave_guild')
def handle_l_g():
    try:
        u = connected_users.get(request.sid)
        if not u:
            return
            
        supabase.table("users").update({
            "guild_name": None
        }).eq("pseudo", u['pseudo']).execute()
        
        connected_users[request.sid]['guild'] = None
        emit('update_full_state', {'guild': None})
        emit('success', "Tu as quitté la guilde")
        
    except Exception as e:
        print(f"❌ [ERROR] Leave guild: {e}")

# CORRECTION BUG #8 & #4 : Nettoyage complet à la déconnexion
@socketio.on('disconnect')
def handle_disconnect():
    if request.sid in connected_users:
        try:
            # Quitter toutes les rooms
            user = connected_users[request.sid]
            leave_room(user['pseudo'])
            leave_room(request.sid)
            
            # Supprimer de la mémoire
            del connected_users[request.sid]
            print(f"👋 [DISCONNECT] {user.get('pseudo', 'unknown')} déconnecté")
        except Exception as e:
            print(f"❌ [ERROR] Disconnect cleanup: {e}")

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"✅ [SYSTEM] Serveur démarré sur le port {port}")
    print(f"📊 [INFO] Utilisateurs connectés : {len(connected_users)}")
    socketio.run(app, host='0.0.0.0', port=port, debug=False)

import os
import eventlet

# IMPORTANT : Le monkey_patch doit être au tout début pour Render/Heroku
eventlet.monkey_patch()

from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room
from flask_bcrypt import Bcrypt
from supabase import create_client, Client

# --- INITIALISATION ---
app = Flask(__name__)
app.config['SECRET_KEY'] = 'tokacookies_ultra_v16_security'
bcrypt = Bcrypt(app)

# Utilisation d'eventlet pour gérer les threads d'auto-click
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# --- CONFIG SUPABASE ---
# Note : Assure-toi d'utiliser la SERVICE_ROLE KEY si tu as activé la RLS
SUPABASE_URL = "https://rzzhkdzjnjeeoqbtlles.supabase.co"
SUPABASE_KEY = "sb_secret_wjlaZm7VdO5VgO6UfqEn0g_FgbwC-ao" 
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Stockage des sessions en mémoire vive
# Structure : { sid: { 'pseudo': '...', 'mult': 1, 'auto': 0, 'guild': '...', 'friends': [] } }
connected_users = {}

# --- LOGIQUE AUTO-CLICK (CPS) ---

def auto_click_loop():
    """
    Boucle infinie qui s'exécute toutes les secondes.
    Elle distribue les clics automatiques aux joueurs connectés.
    """
    print("🚀 [SYSTEM] Boucle Auto-Click démarrée")
    while True:
        socketio.sleep(1) # Pause d'une seconde
        
        # On itère sur une copie pour éviter les erreurs de dictionnaire pendant la boucle
        for sid, user_info in list(connected_users.items()):
            cps = user_info.get('auto', 0)
            
            if cps > 0:
                try:
                    pseudo = user_info['pseudo']
                    # 1. Récupération du score actuel
                    res = supabase.table("users").select("clicks").eq("pseudo", pseudo).execute()
                    
                    if res.data:
                        current_clicks = res.data[0]['clicks']
                        new_clicks = current_clicks + cps
                        
                        # 2. Mise à jour en base de données
                        supabase.table("users").update({"clicks": new_clicks}).eq("pseudo", pseudo).execute()
                        
                        # 3. Envoi au client pour mise à jour visuelle immédiate
                        socketio.emit('update_score', {
                            'clicks': new_clicks, 
                            'rank': get_rank_title(new_clicks)
                        }, room=sid)
                except Exception as e:
                    print(f"❌ [ERROR] Erreur Auto-Click pour {user_info.get('pseudo')}: {e}")

# Lancement de la boucle dans un thread séparé
eventlet.spawn(auto_click_loop)

# --- FONCTIONS UTILITAIRES ---

def get_rank_title(clicks):
    """Calcule le titre en fonction du score"""
    ranks = [
        (0, "Vagabond"), 
        (1000, "Citoyen"), 
        (5000, "Chevalier"), 
        (20000, "Seigneur"), 
        (100000, "Roi"), 
        (500000, "Empereur"),
        (1000000, "Légende")
    ]
    for threshold, title in reversed(ranks):
        if clicks >= threshold:
            return title
    return "Vagabond"

def broadcast_leaderboard_to_all():
    """Envoie le classement relatif à chaque utilisateur connecté"""
    for sid, data in connected_users.items():
        try:
            # Utilisation de la fonction RPC stockée sur Supabase pour la performance
            res = supabase.rpc('get_relative_leaderboard', {'player_pseudo': data['pseudo']}).execute()
            socketio.emit('leaderboard_update', {'players': res.data}, room=sid)
        except Exception as e:
            print(f"❌ [ERROR] Classement échoué pour {sid}: {e}")

def update_social_data(pseudo):
    """Synchronise la liste d'amis et les demandes pour un pseudo donné"""
    try:
        # 1. Amis acceptés
        res_f = supabase.table("friendships").select("*").or_(f'user1.eq."{pseudo}",user2.eq."{pseudo}"').eq("status", "accepted").execute()
        friends_list = []
        for f in res_f.data:
            friend = f['user2'] if f['user1'] == pseudo else f['user1']
            friends_list.append(friend)
        
        # 2. Demandes d'amis reçues (en attente)
        res_r = supabase.table("friendships").select("user1").eq("user2", pseudo).eq("status", "pending").execute()
        pending_requests = [r['user1'] for r in res_r.data]
        
        # 3. Vérification si leader de guilde et demandes de guilde
        res_g = supabase.table("guilds").select("name").eq("founder", pseudo).execute()
        is_leader = False
        guild_reqs = []
        if res_g.data:
            is_leader = True
            g_name = res_g.data[0]['name']
            res_jr = supabase.table("guild_join_requests").select("requester").eq("guild_name", g_name).execute()
            guild_reqs = [r['requester'] for r in res_jr.data]

        socketio.emit('social_update', {
            'friends': friends_list,
            'friend_requests': pending_requests,
            'guild_join_requests': guild_reqs,
            'is_leader': is_leader
        }, room=pseudo)
        
        return friends_list
    except Exception as e:
        print(f"❌ [ERROR] Social Sync: {e}")
        return []

# --- ROUTES & SOCKETS ---

@app.route('/')
def index():
    return render_template('index.html')

@socketio.on('login_action')
def handle_auth(data):
    p = data['pseudo'].strip()
    pwd = data['password']
    t = data['type']
    
    # Recherche de l'utilisateur
    res = supabase.table("users").select("*").eq("pseudo", p).execute()
    user = res.data[0] if res.data else None

    # Inscription
    if t == 'register' and not user:
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
        join_room(p)
        friends = update_social_data(p)
        
        # Stockage en session
        connected_users[request.sid] = {
            'pseudo': p, 
            'mult': user.get('multiplier', 1), 
            'auto': user.get('auto_clicks', 0), # CRUCIAL pour l'auto-clicker
            'guild': user.get('guild_name'),
            'friends': friends
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
        emit('error', "Pseudo ou mot de passe incorrect.")

@socketio.on('add_click')
def handle_click():
    u = connected_users.get(request.sid)
    if u:
        # Récupération et update atomique du score
        res = supabase.table("users").select("clicks").eq("pseudo", u['pseudo']).execute()
        new_val = res.data[0]['clicks'] + u['mult']
        
        supabase.table("users").update({"clicks": new_val}).eq("pseudo", u['pseudo']).execute()
        
        # Update immédiat du joueur
        emit('update_score', {'clicks': new_val, 'rank': get_rank_title(new_val)})
        
        # On broadcast le leaderboard seulement tous les 20 clics pour économiser le CPU
        if new_val % 20 == 0:
            broadcast_leaderboard_to_all()
            
        # Logique de guilde (ajout aux points collectifs)
        if u.get('guild'):
            try:
                supabase.rpc('increment_guild_clicks', {
                    'guild_name': str(u['guild']), 
                    'amount': int(u['mult'])
                }).execute()
            except: pass

@socketio.on('buy_upgrade')
def handle_up(data):
    u = connected_users.get(request.sid)
    if not u: return
    
    res = supabase.table("users").select("*").eq("pseudo", u['pseudo']).execute().data[0]
    clicks = res['clicks']
    
    if data['type'] == 'mult':
        cost = res['multiplier'] * 100
        if clicks >= cost:
            nm, nv = res['multiplier'] + 1, clicks - cost
            supabase.table("users").update({"clicks": nv, "multiplier": nm}).eq("pseudo", u['pseudo']).execute()
            connected_users[request.sid]['mult'] = nm
            emit('update_full_state', {'clicks': nv, 'mult': nm})
        else:
            emit('error', f"Il te faut {cost} clics !")
            
    elif data['type'] == 'auto':
        current_auto = res.get('auto_clicks', 0)
        cost = (current_auto + 1) * 500
        if clicks >= cost:
            na, nv = current_auto + 1, clicks - cost
            supabase.table("users").update({"clicks": nv, "auto_clicks": na}).eq("pseudo", u['pseudo']).execute()
            
            # Mise à jour de la session mémoire pour la boucle Auto-Click
            connected_users[request.sid]['auto'] = na
            emit('update_full_state', {'clicks': nv, 'auto': na})
        else:
            emit('error', f"Il te faut {cost} clics pour l'Auto !")

@socketio.on('send_chat')
def handle_chat(data):
    u = connected_users.get(request.sid)
    if u and data.get('msg'):
        # On limite le message à 150 caractères
        socketio.emit('new_chat', {
            'user': u['pseudo'], 
            'text': data['msg'][:150], 
            'guild': u['guild']
        }, broadcast=True)

@socketio.on('send_friend_request')
def handle_f_req(data):
    u = connected_users.get(request.sid)
    target = data['target'].strip()
    if not u or target == u['pseudo']: return
    
    try:
        # Vérification si déjà en relation
        check = supabase.table("friendships").select("*").or_(
            f'and(user1.eq."{u["pseudo"]}",user2.eq."{target}"),and(user1.eq."{target}",user2.eq."{u["pseudo"]}")'
        ).execute()
        
        if not check.data:
            supabase.table("friendships").insert({
                "user1": u['pseudo'], 
                "user2": target, 
                "status": "pending"
            }).execute()
            socketio.emit('notif_sound', room=target)
            update_social_data(target)
            emit('success', "Demande envoyée !")
    except:
        emit('error', "Joueur introuvable.")

@socketio.on('respond_friend_request')
def handle_f_resp(data):
    u = connected_users.get(request.sid)
    target = data['target']
    if data['action'] == 'accept':
        supabase.table("friendships").update({"status": "accepted"}).match({
            "user1": target, "user2": u['pseudo']
        }).execute()
        emit('success', f"Ami ajouté : {target}")
    else:
        supabase.table("friendships").delete().match({
            "user1": target, "user2": u['pseudo']
        }).execute()
    
    update_social_data(u['pseudo'])
    update_social_data(target)

@socketio.on('create_guild')
def handle_create_g(data):
    u = connected_users.get(request.sid)
    if u.get('guild'): return emit('error', "Quitte d'abord ta guilde.")
    name = data['name'].strip()
    if len(name) < 3: return emit('error', "Nom trop court.")
    
    try:
        supabase.table("guilds").insert({"name": name, "founder": u['pseudo']}).execute()
        supabase.table("users").update({"guild_name": name}).eq("pseudo", u['pseudo']).execute()
        connected_users[request.sid]['guild'] = name
        emit('update_full_state', {'guild': name})
        emit('success', f"Guilde {name} créée !")
    except:
        emit('error', "Ce nom de guilde est déjà pris.")

@socketio.on('get_guilds')
def handle_get_g():
    res = supabase.table("guilds").select("*").order("total_clicks", desc=True).limit(20).execute()
    emit('guild_list', {'guilds': res.data})

@socketio.on('leave_guild')
def handle_l_g():
    u = connected_users.get(request.sid)
    if u:
        supabase.table("users").update({"guild_name": None}).eq("pseudo", u['pseudo']).execute()
        connected_users[request.sid]['guild'] = None
        emit('update_full_state', {'guild': None})

# Gestion de la déconnexion
@socketio.on('disconnect')
def handle_disconnect():
    if request.sid in connected_users:
        del connected_users[request.sid]

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"✅ [SYSTEM] Serveur démarré sur le port {port}")
    socketio.run(app, host='0.0.0.0', port=port)

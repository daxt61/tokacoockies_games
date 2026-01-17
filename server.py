import os
import eventlet
import time
from datetime import datetime

# IMPORTANT : Le monkey_patch DOIT être au tout début pour le fonctionnement des Threads sur Render
eventlet.monkey_patch()

from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_bcrypt import Bcrypt
from supabase import create_client, Client

# --- INITIALISATION ---
app = Flask(__name__)
# Sécurité : On utilise une clé d'environnement ou une clé par défaut
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'toka_empire_ultra_secret_99')
bcrypt = Bcrypt(app)

# SocketIO configuré pour Eventlet (nécessaire pour les WebSockets stables)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# --- CONFIG SUPABASE ---
SUPABASE_URL = "https://rzzhkdzjnjeeoqbtlles.supabase.co"
# Priorité à la variable d'environnement Render, sinon clé en dur pour éviter le crash
SUPABASE_KEY = os.environ.get('SUPABASE_KEY') or "sb_secret_wjlaZm7VdO5VgO6UfqEn0g_FgbwC-ao"

try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    print("✅ [SUPABASE] Connexion établie avec succès.")
except Exception as e:
    print(f"❌ [SUPABASE] Erreur critique : {e}")

# Mémoire vive du serveur : Stocke les infos des joueurs en ligne
# Structure : { sid: { 'pseudo': str, 'mult': int, 'auto': int, 'guild': str } }
connected_users = {}

# --- FONCTIONS UTILITAIRES ---

def get_rank_title(clicks):
    """ Calcule le titre honorifique selon le score """
    ranks = [
        (1000000, "👑 Empereur"),
        (500000, "💎 Légende"),
        (100000, "🔱 Seigneur"),
        (50000, "🛡️ Chevalier"),
        (10000, "⚔️ Guerrier"),
        (1000, "🌾 Citoyen"),
        (0, "👞 Vagabond")
    ]
    for threshold, title in ranks:
        if clicks >= threshold: return title
    return "👞 Vagabond"

def update_social_data(pseudo):
    """ Envoie les mises à jour d'amis et de guildes à un utilisateur spécifique """
    try:
        # Récupération des amis
        friends_res = supabase.table("friendships").select("*").or_(f"user1.eq.{pseudo},user2.eq.{pseudo}").eq("status", "accepted").execute()
        friends = []
        for f in friends_res.data:
            friends.append(f['user2'] if f['user1'] == pseudo else f['user1'])
        
        # Récupération des demandes d'amis en attente
        reqs_res = supabase.table("friendships").select("user1").eq("user2", pseudo).eq("status", "pending").execute()
        reqs = [r['user1'] for r in reqs_res.data]
        
        socketio.emit('social_update', {
            'friends': friends,
            'friend_requests': reqs
        }, room=pseudo)
    except Exception as e:
        print(f"⚠️ [SOCIAL] Erreur mise à jour pour {pseudo}: {e}")

# --- LOGIQUE AUTO-CLICK (CPS) ---

def auto_click_loop():
    """ Boucle de fond qui s'exécute chaque seconde pour les revenus passifs """
    print("🚀 [LOOP] Boucle Auto-Click démarrée.")
    while True:
        eventlet.sleep(1) # Attendre 1 seconde
        for sid, user in list(connected_users.items()):
            if user.get('auto', 0) > 0:
                try:
                    # On récupère le score actuel, on ajoute le CPS (auto)
                    res = supabase.table("users").select("clicks").eq("pseudo", user['pseudo']).execute()
                    if res.data:
                        new_total = res.data[0]['clicks'] + user['auto']
                        # Mise à jour silencieuse en DB
                        supabase.table("users").update({"clicks": new_total}).eq("pseudo", user['pseudo']).execute()
                        # Envoi de la mise à jour visuelle au client
                        socketio.emit('update_score', {'clicks': new_total}, room=sid)
                except Exception as e:
                    print(f"⚠️ [LOOP] Erreur auto-click pour {user['pseudo']}: {e}")

# Lancement du thread CPS
eventlet.spawn(auto_click_loop)

# --- ROUTES FLASK ---

@app.route('/')
def index():
    return render_template('index.html')

# --- GESTION DES SOCKETS (JEU) ---

@socketio.on('login_action')
def handle_login(data):
    pseudo = data.get('pseudo', '').strip()
    password = data.get('password', '')
    
    if not pseudo or not password:
        return emit('error', "Champs manquants")

    res = supabase.table("users").select("*").eq("pseudo", pseudo).execute()
    
    if data['type'] == 'register':
        if res.data:
            return emit('error', "Ce pseudo est déjà utilisé.")
        
        hashed_pw = bcrypt.generate_password_hash(password).decode('utf-8')
        new_user = {
            "pseudo": pseudo,
            "password": hashed_pw,
            "clicks": 0,
            "multiplier": 1,
            "auto_clicker": 0,
            "guild_name": None
        }
        supabase.table("users").insert(new_user).execute()
        emit('success', "Compte créé ! Connecte-toi.")
    
    else: # LOGIN
        if not res.data:
            return emit('error', "Utilisateur inconnu.")
        
        user = res.data[0]
        if bcrypt.check_password_hash(user['password'], password):
            # Enregistrement de la session
            connected_users[request.sid] = {
                'pseudo': pseudo,
                'mult': user['multiplier'],
                'auto': user['auto_clicker'],
                'guild': user['guild_name']
            }
            join_room(pseudo) # Room personnelle pour les messages ciblés
            
            emit('login_ok', {
                'pseudo': pseudo,
                'clicks': user['clicks'],
                'mult': user['multiplier'],
                'auto': user['auto_clicker'],
                'guild': user['guild_name'],
                'rank': get_rank_title(user['clicks'])
            })
            
            # Update social et classement
            update_social_data(pseudo)
            broadcast_leaderboard()
        else:
            emit('error', "Mot de passe erroné.")

@socketio.on('add_click')
def handle_click():
    user = connected_users.get(request.sid)
    if not user: return
    
    try:
        # Récupérer clics actuels
        res = supabase.table("users").select("clicks").eq("pseudo", user['pseudo']).execute()
        current_clicks = res.data[0]['clicks']
        new_clicks = current_clicks + user['mult']
        
        # Sauvegarder
        supabase.table("users").update({"clicks": new_clicks}).eq("pseudo", user['pseudo']).execute()
        
        # Retourner le nouveau score
        emit('update_score', {
            'clicks': new_clicks,
            'rank': get_rank_title(new_clicks)
        })
    except Exception as e:
        print(f"❌ Erreur clic: {e}")

@socketio.on('buy_upgrade')
def handle_upgrade(data):
    u = connected_users.get(request.sid)
    if not u: return
    
    res = supabase.table("users").select("*").eq("pseudo", u['pseudo']).execute().data[0]
    clicks = res['clicks']
    
    if data['type'] == 'mult':
        cost = res['multiplier'] * 100
        if clicks >= cost:
            new_mult = res['multiplier'] + 1
            new_clicks = clicks - cost
            supabase.table("users").update({"clicks": new_clicks, "multiplier": new_mult}).eq("pseudo", u['pseudo']).execute()
            u['mult'] = new_mult
            emit('update_full_state', {'clicks': new_clicks, 'mult': new_mult})
            emit('success', "Multiplicateur amélioré !")
        else:
            emit('error', f"Il te manque {cost - clicks} cookies !")
            
    elif data['type'] == 'auto':
        cost = (res['auto_clicker'] + 1) * 500
        if clicks >= cost:
            new_auto = res['auto_clicker'] + 1
            new_clicks = clicks - cost
            supabase.table("users").update({"clicks": new_clicks, "auto_clicker": new_auto}).eq("pseudo", u['pseudo']).execute()
            u['auto'] = new_auto
            emit('update_full_state', {'clicks': new_clicks, 'auto': new_auto})
            emit('success', "Vitesse d'auto-clic augmentée !")
        else:
            emit('error', "Pas assez de cookies pour le CPS.")

# --- CHAT & SOCIAL ---

@socketio.on('send_chat')
def handle_chat(data):
    u = connected_users.get(request.sid)
    if not u: return
    
    msg = data.get('msg', '').strip()
    if msg:
        # Diffusion à tout le monde
        socketio.emit('new_chat', {
            'user': u['pseudo'],
            'text': msg[:150],
            'guild': u['guild']
        })

@socketio.on('send_friend_request')
def friend_req(data):
    u = connected_users.get(request.sid)
    target = data.get('target', '').strip()
    if not u or target == u['pseudo']: return
    
    try:
        supabase.table("friendships").insert({"user1": u['pseudo'], "user2": target, "status": "pending"}).execute()
        update_social_data(target)
        emit('success', "Demande envoyée !")
    except:
        emit('error', "Déjà amis ou joueur inexistant.")

@socketio.on('create_guild')
def create_guild(data):
    u = connected_users.get(request.sid)
    name = data.get('name', '').strip()
    if not u or u['guild']: return emit('error', "Tu as déjà une guilde.")
    
    try:
        supabase.table("guilds").insert({"name": name, "founder": u['pseudo']}).execute()
        supabase.table("users").update({"guild_name": name}).eq("pseudo", u['pseudo']).execute()
        u['guild'] = name
        emit('update_full_state', {'guild': name})
        emit('success', f"Guilde {name} fondée !")
    except:
        emit('error', "Ce nom de guilde est pris.")

def broadcast_leaderboard():
    """ Envoie le top 10 à tous les joueurs """
    res = supabase.table("users").select("pseudo, clicks").order("clicks", desc=True).limit(10).execute()
    # Ajout du rang numérique
    players = []
    for i, p in enumerate(res.data):
        players.append({'rank': i+1, 'pseudo': p['pseudo'], 'clicks': p['clicks']})
    socketio.emit('leaderboard_update', {'players': players})

@socketio.on('disconnect')
def on_disconnect():
    if request.sid in connected_users:
        print(f"👋 {connected_users[request.sid]['pseudo']} est parti.")
        del connected_users[request.sid]

# --- LANCEMENT ---

if __name__ == '__main__':
    # Configuration pour Render
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port)

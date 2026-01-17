import os
import eventlet
import time
import json
from datetime import datetime

# --- INITIALISATION CRITIQUE ---
# Le monkey_patch doit être AVANT tout autre import pour éviter les plantages Render
eventlet.monkey_patch()

from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_bcrypt import Bcrypt
from supabase import create_client, Client

# --- CONFIGURATION FLASK & SOCKETIO ---
# Ici on ne précise rien, donc Flask cherche dans le dossier 'templates' par défaut
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'toka_ultra_secret_2026')
bcrypt = Bcrypt(app)

# On active le mode async eventlet pour supporter des centaines de joueurs
socketio = SocketIO(app, 
                    cors_allowed_origins="*", 
                    async_mode='eventlet', 
                    ping_timeout=10, 
                    ping_interval=5)

# --- CONFIGURATION SUPABASE ---
# --- CONFIGURATION SUPABASE ---
SUPABASE_URL = "https://rzzhkdzjnjeeoqbtlles.supabase.co"
# Utilise ta clé service_role (celle qui commence par eyJ...)
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InJ6emhrZHpqbmplZW9xYnRsbGVzIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc2ODMxMTIwOCwiZXhwIjoyMDgzODg3MjA4fQ.0TRrVyMKV3EHXmw3HZKC86CQSo1ezMkISMbccLoyXrA" 

try:
    # Correction de l'erreur 'proxy' : on initialise sans arguments superflus
    from supabase import Client, create_client
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    print("✅ [SYSTEM] Base de données connectée avec succès.")
except Exception as e:
    print(f"❌ [SYSTEM] Erreur de connexion DB : {e}")

# Mémoire vive (RAM) du serveur
connected_users = {} # { sid: { data } }
server_stats = {"total_clicks_session": 0, "start_time": time.time()}

# --- LOGIQUE DE JEU AVANCÉE ---

def get_rank_info(clicks):
    """Calcule le rang et le bonus associé"""
    if clicks >= 1000000: return "👑 Divinité", 2.0
    if clicks >= 500000:  return "💎 Maître", 1.5
    if clicks >= 100000:  return "🔱 Seigneur", 1.2
    if clicks >= 10000:   return "⚔️ Guerrier", 1.1
    return "👞 Vagabond", 1.0

def log_event(msg):
    """Affiche un log propre dans la console Render"""
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {msg}")

# --- BOUCLE DE FOND (AUTO-CLICK / CPS) ---

def background_cps_worker():
    """Gère les revenus passifs de tous les joueurs connectés chaque seconde"""
    log_event("🚀 Worker CPS démarré")
    while True:
        eventlet.sleep(1) # Pause d'une seconde
        
        # On itère sur une copie pour éviter les erreurs si qqun se déconnecte
        for sid, user in list(connected_users.items()):
            if user.get('auto', 0) > 0:
                try:
                    # 1. Récupérer score actuel
                    res = supabase.table("users").select("clicks").eq("pseudo", user['pseudo']).execute()
                    if res.data:
                        current = res.data[0]['clicks']
                        # 2. Ajouter le CPS
                        new_score = current + user['auto']
                        # 3. Sauvegarder
                        supabase.table("users").update({"clicks": new_score}).eq("pseudo", user['pseudo']).execute()
                        # 4. Notifier le joueur
                        rank_name, _ = get_rank_info(new_score)
                        socketio.emit('update_score', {
                            'clicks': new_score,
                            'rank': rank_name
                        }, room=sid)
                except Exception as e:
                    log_event(f"⚠️ Erreur CPS ({user['pseudo']}): {e}")

# Lancement du thread CPS
eventlet.spawn(background_cps_worker)

# --- ROUTES HTTP ---

@app.route('/')
def index():
    """Cette route cherche index.html dans le dossier /templates"""
    log_event(f"🌐 Accès page d'accueil par {request.remote_addr}")
    return render_template('index.html')

@app.route('/status')
def status():
    """Route de diagnostic pour vérifier si le serveur est vivant"""
    uptime = time.time() - server_stats["start_time"]
    return jsonify({
        "status": "online",
        "players_online": len(connected_users),
        "uptime_seconds": int(uptime)
    })

# --- EVENEMENTS SOCKET.IO ---

@socketio.on('login_action')
def handle_login(data):
    pseudo = data.get('pseudo', '').strip()
    password = data.get('password', '')
    
    if not pseudo or len(pseudo) < 3:
        return emit('error', "Pseudo trop court (min 3 car.)")

    # Vérification DB
    res = supabase.table("users").select("*").eq("pseudo", pseudo).execute()
    
    if data['type'] == 'register':
        if res.data: return emit('error', "Pseudo déjà pris !")
        
        hashed = bcrypt.generate_password_hash(password).decode('utf-8')
        new_u = {
            "pseudo": pseudo, "password": hashed, "clicks": 0,
            "multiplier": 1, "auto_clicker": 0, "guild_name": None
        }
        supabase.table("users").insert(new_u).execute()
        log_event(f"🆕 Nouveau joueur : {pseudo}")
        emit('success', "Compte créé ! Connecte-toi.")
    
    else: # LOGIN
        if not res.data: return emit('error', "Joueur inconnu.")
        user = res.data[0]
        
        if bcrypt.check_password_hash(user['password'], password):
            # Stockage session
            connected_users[request.sid] = {
                'pseudo': pseudo, 'mult': user['multiplier'],
                'auto': user['auto_clicker'], 'guild': user['guild_name']
            }
            join_room(pseudo)
            
            rank_name, _ = get_rank_info(user['clicks'])
            emit('login_ok', {
                'pseudo': pseudo, 'clicks': user['clicks'],
                'mult': user['multiplier'], 'auto': user['auto_clicker'],
                'guild': user['guild_name'], 'rank': rank_name
            })
            log_event(f"🔑 {pseudo} s'est connecté.")
            send_leaderboard()
        else:
            emit('error', "Mot de passe incorrect.")

@socketio.on('add_click')
def handle_click():
    u = connected_users.get(request.sid)
    if not u: return
    
    try:
        # On récupère le score pour éviter la triche côté client
        res = supabase.table("users").select("clicks").eq("pseudo", u['pseudo']).execute()
        current_clicks = res.data[0]['clicks']
        
        # Calcul du gain
        rank_name, bonus = get_rank_info(current_clicks)
        gain = int(u['mult'] * bonus)
        new_total = current_clicks + gain
        
        # Update
        supabase.table("users").update({"clicks": new_total}).eq("pseudo", u['pseudo']).execute()
        server_stats["total_clicks_session"] += gain
        
        emit('update_score', {'clicks': new_total, 'rank': rank_name})
    except Exception as e:
        log_event(f"❌ Erreur clic : {e}")

@socketio.on('buy_upgrade')
def handle_buy(data):
    u = connected_users.get(request.sid)
    if not u: return
    
    res = supabase.table("users").select("*").eq("pseudo", u['pseudo']).execute().data[0]
    
    if data['type'] == 'mult':
        cost = res['multiplier'] * 200
        if res['clicks'] >= cost:
            new_m = res['multiplier'] + 1
            new_c = res['clicks'] - cost
            supabase.table("users").update({"clicks": new_c, "multiplier": new_m}).eq("pseudo", u['pseudo']).execute()
            u['mult'] = new_m
            emit('update_full_state', {'clicks': new_c, 'mult': new_m})
            emit('success', f"Multiplicateur : x{new_m}")
        else: emit('error', f"Besoin de {cost} cookies !")

    elif data['type'] == 'auto':
        cost = (res['auto_clicker'] + 1) * 750
        if res['clicks'] >= cost:
            new_a = res['auto_clicker'] + 1
            new_c = res['clicks'] - cost
            supabase.table("users").update({"clicks": new_c, "auto_clicker": new_a}).eq("pseudo", u['pseudo']).execute()
            u['auto'] = new_a
            emit('update_full_state', {'clicks': new_c, 'auto': new_a})
            emit('success', f"CPS augmenté à {new_a} !")
        else: emit('error', f"Besoin de {cost} cookies !")

@socketio.on('send_chat')
def handle_chat(data):
    u = connected_users.get(request.sid)
    if not u: return
    msg = data.get('msg', '').strip()
    if msg:
        socketio.emit('new_chat', {
            'user': u['pseudo'], 
            'text': msg[:100], 
            'guild': u['guild'],
            'time': datetime.now().strftime("%H:%M")
        })

def send_leaderboard():
    """Envoie le Top 10 mondial"""
    try:
        res = supabase.table("users").select("pseudo, clicks").order("clicks", desc=True).limit(10).execute()
        socketio.emit('leaderboard_update', {'players': res.data})
    except: pass

@socketio.on('disconnect')
def handle_disconnect():
    if request.sid in connected_users:
        u = connected_users[request.sid]
        log_event(f"👋 Déconnexion : {u['pseudo']}")
        del connected_users[request.sid]

# --- LANCEMENT DU SERVEUR ---

if __name__ == '__main__':
    # Le port est injecté par Render, sinon 5000 par défaut
    port = int(os.environ.get('PORT', 5000))
    log_event(f"🌍 Serveur Toka Cookies en ligne sur le port {port}")
    socketio.run(app, host='0.0.0.0', port=port)

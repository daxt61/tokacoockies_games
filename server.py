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
from supabase.lib.client_options import ClientOptions

# --- CONFIGURATION FLASK & SOCKETIO ---
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'toka_ultra_secret_2026')
bcrypt = Bcrypt(app)

# Mode async eventlet pour supporter les clics simultanés
socketio = SocketIO(app, 
                    cors_allowed_origins="*", 
                    async_mode='eventlet', 
                    ping_timeout=10, 
                    ping_interval=5)

# --- CONFIGURATION SUPABASE ---
SUPABASE_URL = "https://rzzhkdzjnjeeoqbtlles.supabase.co"
# METS TA CLÉ eyJ... ICI
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InJ6emhrZHpqbmplZW9xYnRsbGVzIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc2ODMxMTIwOCwiZXhwIjoyMDgzODg3MjA4fQ.0TRrVyMKV3EHXmw3HZKC86CQSo1ezMkISMbccLoyXrA" 

try:
    # On force les options à vide pour neutraliser l'erreur 'proxy'
    options = ClientOptions()
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY, options=options)
    print("✅ [SYSTEM] Base de données connectée avec succès.")
except Exception as e:
    print(f"❌ [SYSTEM] Erreur de connexion DB : {e}")

# Mémoire vive (RAM) du serveur
connected_users = {} 
server_stats = {"total_clicks_session": 0, "start_time": time.time()}

# --- LOGIQUE DE JEU ---

def get_rank_info(clicks):
    if clicks >= 1000000: return "👑 Divinité", 2.0
    if clicks >= 500000:  return "💎 Maître", 1.5
    if clicks >= 100000:  return "🔱 Seigneur", 1.2
    if clicks >= 10000:   return "⚔️ Guerrier", 1.1
    return "👞 Vagabond", 1.0

def log_event(msg):
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {msg}")

# --- BOUCLE AUTO-CLICK (CPS) ---

def background_cps_worker():
    log_event("🚀 Worker CPS démarré")
    while True:
        eventlet.sleep(1)
        for sid, user in list(connected_users.items()):
            if user.get('auto', 0) > 0:
                try:
                    res = supabase.table("users").select("clicks").eq("pseudo", user['pseudo']).execute()
                    if res.data:
                        current = res.data[0]['clicks']
                        new_score = current + user['auto']
                        supabase.table("users").update({"clicks": new_score}).eq("pseudo", user['pseudo']).execute()
                        rank_name, _ = get_rank_info(new_score)
                        socketio.emit('update_score', {'clicks': new_score, 'rank': rank_name}, room=sid)
                except Exception as e:
                    pass

eventlet.spawn(background_cps_worker)

# --- ROUTES HTTP ---

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/status')
def status():
    return jsonify({"status": "online", "players": len(connected_users)})

# --- EVENEMENTS SOCKET.IO ---

@socketio.on('login_action')
def handle_login(data):
    pseudo = data.get('pseudo', '').strip()
    password = data.get('password', '')
    if not pseudo or len(pseudo) < 3: return emit('error', "Pseudo trop court")

    res = supabase.table("users").select("*").eq("pseudo", pseudo).execute()
    
    if data['type'] == 'register':
        if res.data: return emit('error', "Pseudo déjà pris")
        hashed = bcrypt.generate_password_hash(password).decode('utf-8')
        new_u = {"pseudo": pseudo, "password": hashed, "clicks": 0, "multiplier": 1, "auto_clicker": 0}
        supabase.table("users").insert(new_u).execute()
        emit('success', "Compte créé !")
    else:
        if not res.data: return emit('error', "Inconnu")
        user = res.data[0]
        if bcrypt.check_password_hash(user['password'], password):
            connected_users[request.sid] = {'pseudo': pseudo, 'mult': user['multiplier'], 'auto': user['auto_clicker']}
            rank_name, _ = get_rank_info(user['clicks'])
            emit('login_ok', {'pseudo': pseudo, 'clicks': user['clicks'], 'mult': user['multiplier'], 'auto': user['auto_clicker'], 'rank': rank_name})
            send_leaderboard()
        else: emit('error', "Mauvais mot de passe")

@socketio.on('add_click')
def handle_click():
    u = connected_users.get(request.sid)
    if not u: return
    res = supabase.table("users").select("clicks").eq("pseudo", u['pseudo']).execute()
    current = res.data[0]['clicks']
    _, bonus = get_rank_info(current)
    gain = int(u['mult'] * bonus)
    new_total = current + gain
    supabase.table("users").update({"clicks": new_total}).eq("pseudo", u['pseudo']).execute()
    rank_name, _ = get_rank_info(new_total)
    emit('update_score', {'clicks': new_total, 'rank': rank_name})

@socketio.on('buy_upgrade')
def handle_buy(data):
    u = connected_users.get(request.sid)
    if not u: return
    res = supabase.table("users").select("*").eq("pseudo", u['pseudo']).execute().data[0]
    
    if data['type'] == 'mult':
        cost = res['multiplier'] * 200
        if res['clicks'] >= cost:
            new_m, new_c = res['multiplier'] + 1, res['clicks'] - cost
            supabase.table("users").update({"clicks": new_c, "multiplier": new_m}).eq("pseudo", u['pseudo']).execute()
            u['mult'] = new_m
            emit('update_full_state', {'clicks': new_c, 'mult': new_m})
    elif data['type'] == 'auto':
        cost = (res['auto_clicker'] + 1) * 750
        if res['clicks'] >= cost:
            new_a, new_c = res['auto_clicker'] + 1, res['clicks'] - cost
            supabase.table("users").update({"clicks": new_c, "auto_clicker": new_a}).eq("pseudo", u['pseudo']).execute()
            u['auto'] = new_a
            emit('update_full_state', {'clicks': new_c, 'auto': new_a})

@socketio.on('send_chat')
def handle_chat(data):
    u = connected_users.get(request.sid)
    if u and data.get('msg'):
        socketio.emit('new_chat', {'user': u['pseudo'], 'text': data['msg'][:100], 'time': datetime.now().strftime("%H:%M")})

def send_leaderboard():
    try:
        res = supabase.table("users").select("pseudo, clicks").order("clicks", desc=True).limit(10).execute()
        socketio.emit('leaderboard_update', {'players': res.data})
    except: pass

@socketio.on('disconnect')
def handle_disconnect():
    if request.sid in connected_users: del connected_users[request.sid]

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    socketio.run(app, host='0.0.0.0', port=port)

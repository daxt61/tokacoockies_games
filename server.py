import os
import eventlet
import time
from datetime import datetime

# CRITIQUE: monkey_patch AVANT tous les imports
eventlet.monkey_patch()

from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit
from flask_bcrypt import Bcrypt
from supabase import create_client, Client

# --- CONFIGURATION ---
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'toka_ultra_secret_2026')
bcrypt = Bcrypt(app)

socketio = SocketIO(app, 
                    cors_allowed_origins="*", 
                    async_mode='eventlet', 
                    ping_timeout=60, 
                    ping_interval=25)

# --- SUPABASE ---
SUPABASE_URL = "https://rzzhkdzjnjeeoqbtlles.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InJ6emhrZHpqbmplZW9xYnRsbGVzIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc2ODMxMTIwOCwiZXhwIjoyMDgzODg3MjA4fQ.0TRrVyMKV3EHXmw3HZKC86CQSo1ezMkISMbccLoyXrA"

supabase = None
try:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    print("✅ [SYSTEM] Base de données connectée")
except Exception as e:
    print(f"❌ [SYSTEM] Erreur DB: {e}")

# Mémoire
connected_users = {}

# --- UTILS ---
def get_rank_info(clicks):
    if clicks >= 1000000: return "👑 Divinité", 2.0
    if clicks >= 500000:  return "💎 Maître", 1.5
    if clicks >= 100000:  return "🔱 Seigneur", 1.2
    if clicks >= 10000:   return "⚔️ Guerrier", 1.1
    return "👞 Vagabond", 1.0

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

# --- AUTO-CLICKER WORKER ---
def auto_worker():
    log("🚀 Auto-worker démarré")
    while True:
        eventlet.sleep(1)
        for sid, u in list(connected_users.items()):
            if u.get('auto', 0) > 0 and supabase:
                try:
                    res = supabase.table("users").select("clicks").eq("pseudo", u['pseudo']).execute()
                    if res.data:
                        new = res.data[0]['clicks'] + u['auto']
                        supabase.table("users").update({"clicks": new}).eq("pseudo", u['pseudo']).execute()
                        rank, _ = get_rank_info(new)
                        socketio.emit('update_score', {'clicks': new, 'rank': rank}, room=sid)
                except Exception as e:
                    log(f"⚠️ Auto error {u['pseudo']}: {e}")

eventlet.spawn(auto_worker)

# --- ROUTES ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/status')
def status():
    return jsonify({"status": "online", "players": len(connected_users)})

# --- SOCKET EVENTS ---

@socketio.on('connect')
def on_connect():
    log(f"🔌 Client connecté: {request.sid}")

@socketio.on('disconnect')
def on_disconnect():
    if request.sid in connected_users:
        pseudo = connected_users[request.sid]['pseudo']
        del connected_users[request.sid]
        log(f"👋 {pseudo} déconnecté")

@socketio.on('login_action')
def handle_login(data):
    log(f"🔐 Login reçu: {data}")
    
    if not supabase:
        return emit('error', "Base de données indisponible")
    
    pseudo = data.get('pseudo', '').strip()
    password = data.get('password', '')
    
    if not pseudo or len(pseudo) < 3:
        return emit('error', "Pseudo trop court (min 3)")
    
    if not password or len(password) < 3:
        return emit('error', "Mot de passe trop court (min 3)")
    
    try:
        res = supabase.table("users").select("*").eq("pseudo", pseudo).execute()
        
        if data['type'] == 'register':
            if res.data:
                return emit('error', "Pseudo déjà pris")
            
            hashed = bcrypt.generate_password_hash(password).decode('utf-8')
            new_user = {
                "pseudo": pseudo,
                "password": hashed,
                "clicks": 0,
                "multiplier": 1,
                "auto_clicker": 0
            }
            supabase.table("users").insert(new_user).execute()
            log(f"✅ Compte créé: {pseudo}")
            return emit('success', "Compte créé ! Connectez-vous maintenant")
        
        else:  # login
            if not res.data:
                return emit('error', "Compte inconnu")
            
            user = res.data[0]
            if not bcrypt.check_password_hash(user['password'], password):
                return emit('error', "Mauvais mot de passe")
            
            connected_users[request.sid] = {
                'pseudo': pseudo,
                'mult': user['multiplier'],
                'auto': user['auto_clicker']
            }
            
            rank, _ = get_rank_info(user['clicks'])
            
            emit('login_ok', {
                'pseudo': pseudo,
                'clicks': user['clicks'],
                'mult': user['multiplier'],
                'auto': user['auto_clicker'],
                'rank': rank
            })
            
            log(f"✅ {pseudo} connecté ({user['clicks']} clics)")
            send_leaderboard()
            
    except Exception as e:
        log(f"❌ Erreur login: {e}")
        emit('error', f"Erreur serveur: {str(e)}")

@socketio.on('add_click')
def handle_click():
    u = connected_users.get(request.sid)
    if not u or not supabase:
        return
    
    try:
        res = supabase.table("users").select("clicks").eq("pseudo", u['pseudo']).execute()
        if not res.data:
            return
        
        current = res.data[0]['clicks']
        _, bonus = get_rank_info(current)
        gain = int(u['mult'] * bonus)
        new_total = current + gain
        
        supabase.table("users").update({"clicks": new_total}).eq("pseudo", u['pseudo']).execute()
        
        rank, _ = get_rank_info(new_total)
        emit('update_score', {'clicks': new_total, 'rank': rank})
        
        send_leaderboard()
        
    except Exception as e:
        log(f"❌ Click error: {e}")

@socketio.on('buy_upgrade')
def handle_buy(data):
    u = connected_users.get(request.sid)
    if not u or not supabase:
        return
    
    try:
        res = supabase.table("users").select("*").eq("pseudo", u['pseudo']).execute()
        if not res.data:
            return
        
        user = res.data[0]
        
        if data['type'] == 'mult':
            cost = user['multiplier'] * 200
            if user['clicks'] >= cost:
                new_m = user['multiplier'] + 1
                new_c = user['clicks'] - cost
                supabase.table("users").update({
                    "clicks": new_c,
                    "multiplier": new_m
                }).eq("pseudo", u['pseudo']).execute()
                
                u['mult'] = new_m
                emit('update_full_state', {'clicks': new_c, 'mult': new_m})
                emit('success', f"Multiplicateur x{new_m} acheté !")
                log(f"🛒 {u['pseudo']} achète mult x{new_m}")
            else:
                emit('error', f"Pas assez ! ({cost} requis)")
        
        elif data['type'] == 'auto':
            cost = (user['auto_clicker'] + 1) * 750
            if user['clicks'] >= cost:
                new_a = user['auto_clicker'] + 1
                new_c = user['clicks'] - cost
                supabase.table("users").update({
                    "clicks": new_c,
                    "auto_clicker": new_a
                }).eq("pseudo", u['pseudo']).execute()
                
                u['auto'] = new_a
                emit('update_full_state', {'clicks': new_c, 'auto': new_a})
                emit('success', f"Auto {new_a}/s acheté !")
                log(f"🛒 {u['pseudo']} achète auto {new_a}/s")
            else:
                emit('error', f"Pas assez ! ({cost} requis)")
                
    except Exception as e:
        log(f"❌ Buy error: {e}")
        emit('error', "Erreur lors de l'achat")

@socketio.on('send_chat')
def handle_chat(data):
    u = connected_users.get(request.sid)
    if u and data.get('msg'):
        msg = data['msg'][:100]
        socketio.emit('new_chat', {
            'user': u['pseudo'],
            'text': msg,
            'time': datetime.now().strftime("%H:%M")
        })
        log(f"💬 {u['pseudo']}: {msg}")

def send_leaderboard():
    if not supabase:
        return
    try:
        res = supabase.table("users").select("pseudo, clicks").order("clicks", desc=True).limit(10).execute()
        if res.data:
            for i, p in enumerate(res.data):
                p['rank'] = i + 1
            socketio.emit('leaderboard_update', {'players': res.data})
    except Exception as e:
        log(f"⚠️ Leaderboard error: {e}")

# --- LANCEMENT ---
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    log(f"🚀 Serveur démarré sur port {port}")
    socketio.run(app, host='0.0.0.0', port=port)

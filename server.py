import os
import eventlet
import time
from datetime import datetime

# --- PATCH DE COMPATIBILITÉ ---
# Doit être AVANT Flask et SocketIO
eventlet.monkey_patch()

from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_bcrypt import Bcrypt
from supabase import create_client, Client

# --- CONFIGURATION DE L'APPLICATION ---
# On définit explicitement les dossiers pour Render
app = Flask(__name__, 
            template_folder='.',     # Dit à Flask que l'index.html est à la racine
            static_folder='static')  # Si tu as un dossier static

app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'tokacookies_v17_mega_final')
bcrypt = Bcrypt(app)

# SocketIO avec eventlet pour le temps réel
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# --- CONFIGURATION BASE DE DONNÉES (SUPABASE) ---
SUPABASE_URL = "https://rzzhkdzjnjeeoqbtlles.supabase.co"
# On force la clé ici pour éviter l'erreur de déploiement Render
SUPABASE_KEY = "sb_secret_wjlaZm7VdO5VgO6UfqEn0g_FgbwC-ao"

try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    print("✅ [DATABASE] Connecté à Supabase")
except Exception as e:
    print(f"❌ [DATABASE] Erreur de connexion : {e}")

# Mémoire vive : joueurs connectés
connected_users = {}

# --- SYSTÈME DE TITRES (PROGRESSION) ---
def get_title(score):
    if score >= 1000000: return "👑 Divinité du Cookie"
    if score >= 500000: return "💎 Maître Suprême"
    if score >= 100000: return "🔱 Grand Seigneur"
    if score >= 50000: return "🛡️ Chevalier"
    if score >= 10000: return "⚔️ Guerrier"
    if score >= 1000: return "🌾 Boulanger"
    return "👞 Vagabond"

# --- BOUCLE AUTOMATIQUE (CPS / AUTO-CLICK) ---
def auto_click_process():
    """Ajoute les clics passifs toutes les secondes en base de données"""
    print("🚀 [WORKER] Boucle de clics automatiques activée")
    while True:
        eventlet.sleep(1)
        for sid, data in list(connected_users.items()):
            if data.get('auto', 0) > 0:
                try:
                    # Lecture du score actuel
                    user_res = supabase.table("users").select("clicks").eq("pseudo", data['pseudo']).execute()
                    if user_res.data:
                        current = user_res.data[0]['clicks']
                        new_score = current + data['auto']
                        # Mise à jour
                        supabase.table("users").update({"clicks": new_score}).eq("pseudo", data['pseudo']).execute()
                        # Envoi au joueur
                        socketio.emit('update_score', {
                            'clicks': new_score, 
                            'rank': get_title(new_score)
                        }, room=sid)
                except Exception as e:
                    print(f"⚠️ [CPS] Erreur pour {data['pseudo']}: {e}")

# Lancement du processus en arrière-plan
eventlet.spawn(auto_click_process)

# --- ROUTES RENDU HTML ---

@app.route('/')
def home():
    """Sert le fichier index.html situé à la racine du projet"""
    try:
        # On essaie de servir index(3).html ou index.html selon ton renommage
        return render_template('index.html') 
    except:
        return "⚠️ Erreur : Fichier index.html introuvable à la racine du projet."

@app.route('/health')
def health():
    return jsonify({"status": "online"}), 200

# --- GESTION DES SOCKETS (LOGIQUE JEU) ---

@socketio.on('login_action')
def on_login(data):
    pseudo = data.get('pseudo', '').strip()
    password = data.get('password', '')
    
    if not pseudo: return emit('error', "Pseudo vide !")

    # Recherche utilisateur
    res = supabase.table("users").select("*").eq("pseudo", pseudo).execute()
    
    if data['type'] == 'register':
        if res.data: return emit('error', "Ce pseudo est déjà pris.")
        
        hashed = bcrypt.generate_password_hash(password).decode('utf-8')
        new_u = {
            "pseudo": pseudo, "password": hashed, "clicks": 0,
            "multiplier": 1, "auto_clicker": 0, "guild_name": None
        }
        supabase.table("users").insert(new_u).execute()
        emit('success', "Compte créé ! Connecte-toi.")
    
    else: # LOGIN
        if not res.data: return emit('error', "Utilisateur inexistant.")
        user = res.data[0]
        
        if bcrypt.check_password_hash(user['password'], password):
            # Session active
            connected_users[request.sid] = {
                'pseudo': pseudo, 'mult': user['multiplier'], 
                'auto': user['auto_clicker'], 'guild': user['guild_name']
            }
            join_room(pseudo)
            
            emit('login_ok', {
                'pseudo': pseudo, 'clicks': user['clicks'],
                'mult': user['multiplier'], 'auto': user['auto_clicker'],
                'guild': user['guild_name'], 'rank': get_title(user['clicks'])
            })
            update_leaderboard()
        else:
            emit('error', "Mauvais mot de passe.")

@socketio.on('add_click')
def on_click():
    u = connected_users.get(request.sid)
    if not u: return
    
    res = supabase.table("users").select("clicks").eq("pseudo", u['pseudo']).execute()
    new_val = res.data[0]['clicks'] + u['mult']
    
    supabase.table("users").update({"clicks": new_val}).eq("pseudo", u['pseudo']).execute()
    emit('update_score', {'clicks': new_val, 'rank': get_title(new_val)})

@socketio.on('buy_upgrade')
def on_buy(data):
    u = connected_users.get(request.sid)
    if not u: return
    
    res = supabase.table("users").select("*").eq("pseudo", u['pseudo']).execute().data[0]
    
    if data['type'] == 'mult':
        cost = res['multiplier'] * 150
        if res['clicks'] >= cost:
            new_m = res['multiplier'] + 1
            new_c = res['clicks'] - cost
            supabase.table("users").update({"clicks": new_c, "multiplier": new_m}).eq("pseudo", u['pseudo']).execute()
            u['mult'] = new_m
            emit('update_full_state', {'clicks': new_c, 'mult': new_m})
        else: emit('error', "Pas assez de cookies !")

    elif data['type'] == 'auto':
        cost = (res['auto_clicker'] + 1) * 600
        if res['clicks'] >= cost:
            new_a = res['auto_clicker'] + 1
            new_c = res['clicks'] - cost
            supabase.table("users").update({"clicks": new_c, "auto_clicker": new_a}).eq("pseudo", u['pseudo']).execute()
            u['auto'] = new_a
            emit('update_full_state', {'clicks': new_c, 'auto': new_a})
        else: emit('error', "Pas assez de cookies !")

# --- SYSTÈME SOCIAL ---

@socketio.on('send_chat')
def on_chat(data):
    u = connected_users.get(request.sid)
    if not u or not data.get('msg'): return
    
    socketio.emit('new_chat', {
        'user': u['pseudo'], 
        'text': data['msg'][:120], 
        'guild': u['guild']
    })

def update_leaderboard():
    res = supabase.table("users").select("pseudo, clicks").order("clicks", desc=True).limit(10).execute()
    socketio.emit('leaderboard_update', {'players': res.data})

@socketio.on('disconnect')
def on_disconnect():
    if request.sid in connected_users:
        del connected_users[request.sid]

# --- DÉMARRAGE ---
if __name__ == '__main__':
    # PORT est géré automatiquement par Render
    port = int(os.environ.get('PORT', 5000))
    print(f"🌍 Serveur démarré sur le port {port}")
    socketio.run(app, host='0.0.0.0', port=port)

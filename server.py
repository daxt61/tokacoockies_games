import os
import eventlet
import time
from datetime import datetime

# IMPORTANT : Le monkey_patch doit être au tout début pour Render/Heroku
eventlet.monkey_patch()

from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_bcrypt import Bcrypt
from supabase import create_client, Client

# --- INITIALISATION ---
app = Flask(__name__)
# Utilisation de variable d'environnement pour la sécurité
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'tokacookies_v17_mega_final')
bcrypt = Bcrypt(app)

# SocketIO avec eventlet pour gérer les threads d'auto-click
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# --- CONFIG SUPABASE ---
SUPABASE_URL = "https://rzzhkdzjnjeeoqbtlles.supabase.co"
# Note: Idéalement, utilise os.environ.get('SUPABASE_KEY')
# Cherche d'abord dans les variables d'environnement, sinon utilise la clé en dur
SUPABASE_KEY = os.environ.get('SUPABASE_KEY') or "sb_secret_wjlaZm7VdO5VgO6UfqEn0g_FgbwC-ao"

if not SUPABASE_KEY:
    raise ValueError("ERREUR : La SUPABASE_KEY est manquante !")
# Stockage des sessions en mémoire vive
connected_users = {}

# --- LOGIQUE AUTO-CLICK (CPS) ---

def auto_click_loop():
    """
    Boucle infinie qui distribue les clics automatiques chaque seconde.
    """
    print("🚀 [SYSTEM] Boucle Auto-Click démarrée")
    while True:
        socketio.sleep(1) 
        
        for sid, user_info in list(connected_users.items()):
            cps = user_info.get('auto', 0)
            
            if cps > 0:
                try:
                    pseudo = user_info['pseudo']
                    # Mise à jour silencieuse en BDD
                    res = supabase.table("users").select("clicks").eq("pseudo", pseudo).execute()
                    
                    if res.data:
                        new_clicks = res.data[0]['clicks'] + cps
                        supabase.table("users").update({"clicks": new_clicks}).eq("pseudo", pseudo).execute()
                        
                        # Envoi au client pour mise à jour visuelle
                        socketio.emit('update_score', {
                            'clicks': new_clicks, 
                            'rank': get_rank_title(new_clicks)
                        }, room=sid)
                except Exception as e:
                    print(f"❌ [ERROR CPS] {pseudo}: {e}")

# Lancement de la boucle dans un thread séparé
eventlet.spawn(auto_click_loop)

# --- FONCTIONS UTILITAIRES ---

def get_rank_title(clicks):
    """Calcule le titre en fonction du score"""
    ranks = [
        (0, "Vagabond"), (1000, "Citoyen"), (5000, "Chevalier"), 
        (20000, "Seigneur"), (100000, "Roi"), (500000, "Empereur"),
        (1000000, "Légende")
    ]
    for threshold, title in reversed(ranks):
        if clicks >= threshold: return title
    return "Vagabond"

def broadcast_leaderboard():
    """Mise à jour du classement pour tout le monde"""
    for sid, data in connected_users.items():
        try:
            res = supabase.rpc('get_relative_leaderboard', {'player_pseudo': data['pseudo']}).execute()
            socketio.emit('leaderboard_update', {'players': res.data}, room=sid)
        except: pass

def update_social_data(pseudo):
    """Synchronise amis et guildes pour un joueur"""
    try:
        # Amis acceptés
        res_f = supabase.table("friendships").select("*").or_(f'user1.eq."{pseudo}",user2.eq."{pseudo}"').eq("status", "accepted").execute()
        friends = [f['user2'] if f['user1'] == pseudo else f['user1'] for f in res_f.data]
        
        # Demandes reçues
        res_r = supabase.table("friendships").select("user1").eq("user2", pseudo).eq("status", "pending").execute()
        pending = [r['user1'] for r in res_r.data]
        
        # État Guilde
        res_g = supabase.table("guilds").select("name").eq("founder", pseudo).execute()
        is_leader = bool(res_g.data)
        guild_reqs = []
        if is_leader:
            g_name = res_g.data[0]['name']
            res_jr = supabase.table("guild_join_requests").select("requester").eq("guild_name", g_name).execute()
            guild_reqs = [r['requester'] for r in res_jr.data]

        socketio.emit('social_update', {
            'friends': friends, 'friend_requests': pending,
            'guild_join_requests': guild_reqs, 'is_leader': is_leader
        }, room=pseudo)
        return friends
    except: return []

# --- ROUTES & SOCKETS ---

@app.route('/')
def index():
    return render_template('index.html')

@socketio.on('login_action')
def handle_auth(data):
    p = data['pseudo'].strip()
    pwd = data['password']
    t = data['type']
    
    res = supabase.table("users").select("*").eq("pseudo", p).execute()
    user = res.data[0] if res.data else None

    if t == 'register' and not user:
        hpw = bcrypt.generate_password_hash(pwd).decode('utf-8')
        supabase.table("users").insert({
            "pseudo": p, "password": hpw, "clicks": 0, 
            "multiplier": 1, "auto_clicks": 0
        }).execute()
        user = supabase.table("users").select("*").eq("pseudo", p).execute().data[0]

    if user and bcrypt.check_password_hash(user['password'], pwd):
        join_room(p)
        friends = update_social_data(p)
        
        # Stockage session avec anti-cheat
        connected_users[request.sid] = {
            'pseudo': p, 'mult': user.get('multiplier', 1), 
            'auto': user.get('auto_clicks', 0), 'guild': user.get('guild_name'),
            'friends': friends, 'last_click': 0
        }
        
        emit('login_ok', {
            'pseudo': p, 'clicks': user['clicks'], 
            'mult': user.get('multiplier', 1), 'auto': user.get('auto_clicks', 0),
            'guild': user.get('guild_name'), 'rank': get_rank_title(user['clicks'])
        })
        broadcast_leaderboard()
    else:
        emit('error', "Échec de connexion.")

@socketio.on('add_click')
def handle_click():
    u = connected_users.get(request.sid)
    if not u: return

    # ANTI-CHEAT : Max 20 clics par seconde
    now = time.time()
    if now - u['last_click'] < 0.05:
        return
    u['last_click'] = now

    res = supabase.table("users").select("clicks").eq("pseudo", u['pseudo']).execute()
    new_val = res.data[0]['clicks'] + u['mult']
    supabase.table("users").update({"clicks": new_val}).eq("pseudo", u['pseudo']).execute()
    
    emit('update_score', {'clicks': new_val, 'rank': get_rank_title(new_val)})
    
    if new_val % 20 == 0: broadcast_leaderboard()
    
    if u.get('guild'):
        try:
            supabase.rpc('increment_guild_clicks', {'guild_name': u['guild'], 'amount': u['mult']}).execute()
        except: pass

@socketio.on('buy_upgrade')
def handle_up(data):
    u = connected_users.get(request.sid)
    if not u: return
    
    res = supabase.table("users").select("*").eq("pseudo", u['pseudo']).execute().data[0]
    
    if data.get('type') == 'mult':
        cost = res['multiplier'] * 100
        if res['clicks'] >= cost:
            nm, nv = res['multiplier'] + 1, res['clicks'] - cost
            supabase.table("users").update({"clicks": nv, "multiplier": nm}).eq("pseudo", u['pseudo']).execute()
            u['mult'] = nm
            emit('update_full_state', {'clicks': nv, 'mult': nm})
        else: emit('error', f"Besoin de {cost} clics")
            
    elif data.get('type') == 'auto':
        cur_auto = res.get('auto_clicks', 0)
        cost = (cur_auto + 1) * 500
        if res['clicks'] >= cost:
            na, nv = cur_auto + 1, res['clicks'] - cost
            supabase.table("users").update({"clicks": nv, "auto_clicks": na}).eq("pseudo", u['pseudo']).execute()
            u['auto'] = na # Mise à jour immédiate pour la boucle CPS
            emit('update_full_state', {'clicks': nv, 'auto': na})
        else: emit('error', f"Besoin de {cost} clics")

@socketio.on('send_chat')
def handle_chat(data):
    u = connected_users.get(request.sid)
    if u and data.get('msg'):
        socketio.emit('new_chat', {
            'user': u['pseudo'], 'text': data['msg'][:150], 'guild': u['guild']
        }, broadcast=True)

@socketio.on('send_friend_request')
def handle_f_req(data):
    u = connected_users.get(request.sid)
    target = data['target'].strip()
    if not u or target == u['pseudo']: return
    try:
        supabase.table("friendships").insert({"user1": u['pseudo'], "user2": target, "status": "pending"}).execute()
        socketio.emit('notif_sound', room=target)
        update_social_data(target)
        emit('success', "Demande envoyée !")
    except: emit('error', "Joueur introuvable ou déjà ajouté.")

@socketio.on('create_guild')
def handle_create_g(data):
    u = connected_users.get(request.sid)
    if u.get('guild'): return emit('error', "Quitte ta guilde d'abord.")
    name = data['name'].strip()
    try:
        supabase.table("guilds").insert({"name": name, "founder": u['pseudo']}).execute()
        supabase.table("users").update({"guild_name": name}).eq("pseudo", u['pseudo']).execute()
        u['guild'] = name
        emit('update_full_state', {'guild': name})
    except: emit('error', "Nom déjà pris.")

@socketio.on('disconnect')
def handle_disconnect():
    if request.sid in connected_users:
        user = connected_users[request.sid]
        leave_room(user['pseudo'])
        del connected_users[request.sid]

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port)

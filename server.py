import os
import logging
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room
from flask_bcrypt import Bcrypt
from supabase import create_client, Client

app = Flask(__name__)
app.config['SECRET_KEY'] = 'sorek_v9_ultra_secure'
bcrypt = Bcrypt(app)
# On utilise eventlet pour le support du temps réel asynchrone
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

logging.basicConfig(level=logging.INFO)

# === CONFIG SUPABASE ===
SUPABASE_URL = "https://rzzhkdzjnjeeoqbtlles.supabase.co"
SUPABASE_KEY = "sb_secret_wjlaZm7VdO5VgO6UfqEn0g_FgbwC-ao"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

connected_users = {}  # Stocke {sid: {pseudo, mult, guild}}

def get_rank(clicks):
    ranks = [(0, "Vagabond"), (1000, "Citoyen"), (5000, "Chevalier"), (20000, "Seigneur"), (100000, "Roi"), (500000, "Empereur")]
    for threshold, title in reversed(ranks):
        if clicks >= threshold: return title
    return "Vagabond"

def send_leaderboard(sid=None):
    """Envoie le classement. Si sid est fourni, envoie le classement relatif au joueur."""
    try:
        if sid and sid in connected_users:
            pseudo = connected_users[sid]['pseudo']
            # Appel à la fonction RPC de Supabase pour le classement relatif
            res = supabase.rpc('get_relative_leaderboard', {'player_pseudo': pseudo}).execute()
            socketio.emit('leaderboard_update', {'players': res.data}, room=sid)
        else:
            # Top 10 global pour tout le monde
            res = supabase.table("users").select("pseudo, clicks, guild_name").order("clicks", desc=True).limit(10).execute()
            players = [{"rank": i+1, **p} for i, p in enumerate(res.data)]
            socketio.emit('leaderboard_update', {'players': players})
    except Exception as e:
        logging.error(f"Erreur Leaderboard: {e}")

@app.route('/')
def index():
    return render_template('index.html')

@socketio.on('login_action')
def auth(data):
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
            join_room(p)
            emit('login_ok', {
                'pseudo': p, 
                'clicks': user['clicks'], 
                'mult': user['multiplier'], 
                'guild': user.get('guild_name'), 
                'rank': get_rank(user['clicks'])
            })
            send_leaderboard(request.sid)
        else:
            emit('error', "Identifiants invalides")
    except Exception as e:
        emit('error', f"Erreur d'authentification: {e}")

@socketio.on('add_click')
def click():
    u = connected_users.get(request.sid)
    if u:
        try:
            res = supabase.table("users").select("clicks").eq("pseudo", u['pseudo']).execute()
            nv = res.data[0]['clicks'] + u['mult']
            supabase.table("users").update({"clicks": nv}).eq("pseudo", u['pseudo']).execute()
            
            emit('update_score', {'clicks': nv, 'rank': get_rank(nv)})
            
            # Mise à jour du classement tous les 10 clics pour économiser les ressources
            if nv % 10 == 0:
                send_leaderboard(request.sid)
            
            # Mise à jour de la guilde si applicable
            if u.get('guild'):
                supabase.rpc('increment_guild_clicks', {'guild_name': u['guild'], 'amount': u['mult']}).execute()
        except:
            pass

@socketio.on('get_leaderboard')
def handle_get_lb():
    send_leaderboard(request.sid)

@socketio.on('msg')
def msg(data):
    u = connected_users.get(request.sid)
    if u:
        emit('new_msg', {'p': u['pseudo'], 'm': data['m'][:200]}, broadcast=True)

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000)

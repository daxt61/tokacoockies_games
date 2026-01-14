import os, uuid, random, time
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit
from flask_bcrypt import Bcrypt
from supabase import create_client, Client

app = Flask(__name__)
app.config['SECRET_KEY'] = 'sorek_vegas_2026'
bcrypt = Bcrypt(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# --- TES CLÉS SUPABASE ICI ---
SUPABASE_URL = "https://TON_ID.supabase.co"
SUPABASE_KEY = "TA_CLE_SERVICE_ROLE"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

connected_users = {} # sid: {pseudo, mult, guild, rank}

RANKS = [
    (0, "Vagabond 🛖"), (1000, "Citoyen 🏠"), (5000, "Chevalier ⚔️"),
    (20000, "Seigneur 🏰"), (100000, "Roi 👑"), (1000000, "Empereur 🪐"),
    (10000000, "DIEU VIVANT ⚡")
]

def get_rank(clicks):
    for score, title in reversed(RANKS):
        if clicks >= score: return title
    return RANKS[0][1]

@app.route('/')
def index(): return render_template('index.html')

@socketio.on('login_action')
def auth_logic(data):
    try:
        p, pwd, t = data['pseudo'].strip(), data['password'], data['type']
        res = supabase.table("users").select("*").eq("pseudo", p).execute()
        user = res.data[0] if res.data else None

        if t == 'register':
            if user: return emit('auth_error', "Pseudo déjà pris !")
            hpw = bcrypt.generate_password_hash(pwd).decode('utf-8')
            supabase.table("users").insert({"pseudo": p, "password": hpw}).execute()
            user = supabase.table("users").select("*").eq("pseudo", p).execute().data[0]

        if user and bcrypt.check_password_hash(user['password'], pwd):
            rank = get_rank(user['clicks'])
            connected_users[request.sid] = {'pseudo': p, 'mult': user['multiplier'], 'guild': user.get('guild_name'), 'rank': rank}
            emit('login_ok', {
                'pseudo': p, 'clicks': user['clicks'], 'mult': user['multiplier'], 
                'guild': user.get('guild_name'), 'rank': rank
            })
            send_friends_list(p, request.sid)
        else: emit('auth_error', "Identifiants invalides.")
    except Exception as e: emit('auth_error', f"Erreur: {e}")

@socketio.on('add_click')
def add_click():
    if request.sid in connected_users:
        u = connected_users[request.sid]
        update_score(u['pseudo'], u['mult'], u['guild'], request.sid)

@socketio.on('buy_upgrade')
def buy_up():
    sid = request.sid
    if sid in connected_users:
        p = connected_users[sid]['pseudo']
        # Vérif serveur stricte
        res = supabase.table("users").select("clicks", "multiplier").eq("pseudo", p).execute()
        data = res.data[0]
        current_clicks = data['clicks']
        current_mult = data['multiplier']
        cost = current_mult * 100

        if current_clicks >= cost:
            new_clicks = current_clicks - cost
            new_mult = current_mult + 1
            
            supabase.table("users").update({"clicks": new_clicks, "multiplier": new_mult}).eq("pseudo", p).execute()
            
            connected_users[sid]['mult'] = new_mult
            rank = get_rank(new_clicks)
            connected_users[sid]['rank'] = rank
            
            emit('update_full_state', {
                'clicks': new_clicks, 'mult': new_mult, 'rank': rank
            }, room=sid)
            emit('notif_success', "Amélioration achetée !", room=sid)
        else:
            emit('notif_error', "Pas assez d'argent !", room=sid)

@socketio.on('spin_wheel')
def spin():
    sid = request.sid
    if sid not in connected_users: return
    p = connected_users[sid]['pseudo']
    
    res = supabase.table("users").select("clicks").eq("pseudo", p).execute()
    clicks = res.data[0]['clicks']
    cost = 500 
    
    if clicks < cost: return emit('notif_error', "Il faut 500 clics !", room=sid)
    
    # RNG
    outcome = random.choices(['perdu', 'x2', 'x5', 'jackpot'], weights=[55, 30, 10, 5])[0]
    gain = 0
    if outcome == 'x2': gain = cost * 2
    elif outcome == 'x5': gain = cost * 5
    elif outcome == 'jackpot': gain = cost * 50
    
    net_change = gain - cost
    update_score(p, net_change, connected_users[sid]['guild'], sid)
    
    # On envoie le résultat mais le client jouera l'animation
    emit('spin_result', {'outcome': outcome, 'gain': gain, 'net': net_change}, room=sid)

# --- FONCTIONS UTILES ---
def update_score(pseudo, amount, guild_name, sid):
    res = supabase.table("users").select("clicks").eq("pseudo", pseudo).execute()
    new_val = res.data[0]['clicks'] + amount
    supabase.table("users").update({"clicks": new_val}).eq("pseudo", pseudo).execute()
    
    if guild_name:
        g_res = supabase.table("guilds").select("total_clicks").eq("name", guild_name).execute()
        if g_res.data:
            supabase.table("guilds").update({"total_clicks": g_res.data[0]['total_clicks'] + amount}).eq("name", guild_name).execute()
            
    rank = get_rank(new_val)
    if sid in connected_users: connected_users[sid]['rank'] = rank
    emit('update_score', {'clicks': new_val, 'rank': rank}, room=sid)
    send_relative_lb(pseudo, sid)

def send_relative_lb(pseudo, sid):
    res = supabase.table("users").select("pseudo", "clicks").order("clicks", desc=True).execute()
    all_u = res.data
    idx = next((i for i, x in enumerate(all_u) if x['pseudo'] == pseudo), 0)
    s, e = max(0, idx - 5), min(len(all_u), idx + 6)
    emit('relative_lb', {'lb': all_u[s:e], 'pos': idx + 1}, room=sid)

def send_friends_list(pseudo, sid):
    pass # (Garde ta fonction d'amis précédente ici si tu veux, j'ai allégé pour la lecture)

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))


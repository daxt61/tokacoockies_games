import os, uuid, random, datetime
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_bcrypt import Bcrypt
from supabase import create_client, Client

app = Flask(__name__)
app.config['SECRET_KEY'] = 'sorek_ultimate_v6'
bcrypt = Bcrypt(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# --- CONFIG SUPABASE ---
SUPABASE_URL = "https://rzzhkdzjnjeeoqbtlles.supabase.co"
SUPABASE_KEY = "sb_secret_wjlaZm7VdO5VgO6UfqEn0g_FgbwC-ao"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

connected_users = {} # sid: {pseudo, mult, guild, rank}

# RANGS (Seuils de clics)
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
            if user: return emit('auth_error', "Pseudo pris !")
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
            broadcast_global()
            send_friends_list(p, request.sid)
        else: emit('auth_error', "Identifiants invalides.")
    except Exception as e: 
        print(e)
        emit('auth_error', "Erreur serveur.")

# --- CLICKER & SCORE ---
@socketio.on('add_click')
def add_click():
    sid = request.sid
    if sid in connected_users:
        u = connected_users[sid]
        update_score(u['pseudo'], u['mult'], u['guild'])

def update_score(pseudo, amount, guild_name):
    # Update User
    res = supabase.table("users").select("clicks").eq("pseudo", pseudo).execute()
    curr_clicks = res.data[0]['clicks']
    new_val = curr_clicks + amount
    supabase.table("users").update({"clicks": new_val}).eq("pseudo", pseudo).execute()
    
    # Update Guild
    if guild_name:
        g_res = supabase.table("guilds").select("total_clicks").eq("name", guild_name).execute()
        if g_res.data:
            supabase.table("guilds").update({"total_clicks": g_res.data[0]['total_clicks'] + amount}).eq("name", guild_name).execute()
    
    # Notifie le user s'il est co
    sid = get_sid(pseudo)
    if sid:
        rank = get_rank(new_val)
        connected_users[sid]['rank'] = rank
        emit('update_score', {'clicks': new_val, 'rank': rank}, room=sid)
        send_relative_lb(pseudo, sid)

# --- CASINO (LA SURPRISE) ---
@socketio.on('spin_wheel')
def spin():
    sid = request.sid
    if sid not in connected_users: return
    p = connected_users[sid]['pseudo']
    
    res = supabase.table("users").select("clicks").eq("pseudo", p).execute()
    clicks = res.data[0]['clicks']
    cost = 500 # Coût du spin
    
    if clicks < cost:
        return emit('notif', "Pas assez de clics ! (500 requis)", room=sid)
    
    # Logique de gain (RNG)
    outcome = random.choices(['perdu', 'x2', 'x5', 'jackpot'], weights=[50, 30, 15, 5])[0]
    gain = 0
    if outcome == 'perdu': gain = -cost
    elif outcome == 'x2': gain = cost * 2
    elif outcome == 'x5': gain = cost * 5
    elif outcome == 'jackpot': gain = cost * 50
    
    final_gain = gain if outcome != 'perdu' else -cost # Correction calcul
    if outcome != 'perdu': final_gain = gain - cost # On déduit le coût du gain net

    update_score(p, final_gain, connected_users[sid]['guild'])
    emit('spin_result', {'outcome': outcome, 'gain': gain, 'net': final_gain}, room=sid)

# --- AMIS ---
@socketio.on('add_friend')
def add_friend(data):
    me = connected_users[request.sid]['pseudo']
    target = data['target'].strip()
    if me == target: return emit('notif', "Tu ne peux pas t'ajouter toi-même.")
    
    # Vérifie si target existe
    res = supabase.table("users").select("id").eq("pseudo", target).execute()
    if not res.data: return emit('notif', "Ce joueur n'existe pas.")

    # Vérifie si déjà amis
    check = supabase.table("friendships").select("*").or_(f"and(user_a.eq.{me},user_b.eq.{target}),and(user_a.eq.{target},user_b.eq.{me})").execute()
    if check.data: return emit('notif', "Demande déjà envoyée ou amis.")

    supabase.table("friendships").insert({"user_a": me, "user_b": target, "status": "pending"}).execute()
    emit('notif', f"Demande envoyée à {target} !")
    
    # Si target connecté, on refresh sa liste
    tsid = get_sid(target)
    if tsid: send_friends_list(target, tsid)

@socketio.on('accept_friend')
def accept_friend(data):
    me = connected_users[request.sid]['pseudo']
    target = data['target']
    supabase.table("friendships").update({"status": "accepted"}).match({"user_a": target, "user_b": me}).execute()
    send_friends_list(me, request.sid)
    tsid = get_sid(target)
    if tsid: 
        send_friends_list(target, tsid)
        emit('notif', f"{me} a accepté ta demande !", room=tsid)

def send_friends_list(pseudo, sid):
    # Récupérer amis
    res = supabase.table("friendships").select("*").or_(f"user_a.eq.{pseudo},user_b.eq.{pseudo}").execute()
    friends = []
    requests = []
    
    for row in res.data:
        other = row['user_b'] if row['user_a'] == pseudo else row['user_a']
        if row['status'] == 'accepted':
            is_online = get_sid(other) is not None
            friends.append({'name': other, 'online': is_online})
        elif row['user_b'] == pseudo: # Demande reçue
            requests.append(other)
            
    emit('update_friends', {'friends': friends, 'requests': requests}, room=sid)

# --- UTILS ---
def get_sid(pseudo):
    for s, u in connected_users.items():
        if u['pseudo'] == pseudo: return s
    return None

def send_relative_lb(pseudo, sid):
    res = supabase.table("users").select("pseudo", "clicks").order("clicks", desc=True).execute()
    all_u = res.data
    idx = next((i for i, x in enumerate(all_u) if x['pseudo'] == pseudo), 0)
    s, e = max(0, idx - 5), min(len(all_u), idx + 6)
    emit('relative_lb', {'lb': all_u[s:e], 'pos': idx + 1}, room=sid)

def broadcast_global():
    # Simplifié pour économiser la bande passante, on envoie juste le nombre de co
    emit('online_count', {'count': len(connected_users)}, broadcast=True)

@socketio.on('disconnect')
def disc():
    if request.sid in connected_users:
        del connected_users[request.sid]
        broadcast_global()

@socketio.on('get_full_lb')
def get_lb():
    u_res = supabase.table("users").select("pseudo", "clicks").order("clicks", desc=True).limit(20).execute()
    g_res = supabase.table("guilds").select("name", "total_clicks").order("total_clicks", desc=True).limit(20).execute()
    emit('full_lb_data', {'users': u_res.data, 'guilds': g_res.data})

@socketio.on('msg')
def chat(d):
    if request.sid in connected_users:
        u = connected_users[request.sid]
        emit('new_msg', {'p': u['pseudo'], 'm': d['m'], 'r': u['rank']}, broadcast=True)

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))



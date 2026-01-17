import os
import eventlet
import time
from datetime import datetime

# --- INITIALISATION CRITIQUE ---
eventlet.monkey_patch()

from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room
from flask_bcrypt import Bcrypt
from supabase import create_client, Client
from supabase.lib.client_options import ClientOptions

app = Flask(__name__)
app.config['SECRET_KEY'] = 'toka_secret_2026'
bcrypt = Bcrypt(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# --- CONFIG SUPABASE ---
SUPABASE_URL = "https://rzzhkdzjnjeeoqbtlles.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InJ6emhrZHpqbmplZW9xYnRsbGVzIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc2ODMxMTIwOCwiZXhwIjoyMDgzODg3MjA4fQ.0TRrVyMKV3EHXmw3HZKC86CQSo1ezMkISMbccLoyXrA"

options = ClientOptions()
supabase = create_client(SUPABASE_URL, SUPABASE_KEY, options=options)

connected_users = {} # { sid: {data} }

# --- MOTEUR CPS (SCORE AUTO) ---
def background_cps_worker():
    while True:
        eventlet.sleep(1)
        for sid, user in list(connected_users.items()):
            if user.get('auto', 0) > 0:
                try:
                    res = supabase.table("users").select("clicks").eq("pseudo", user['pseudo']).execute()
                    if res.data:
                        new_score = res.data[0]['clicks'] + user['auto']
                        supabase.table("users").update({"clicks": new_score}).eq("pseudo", user['pseudo']).execute()
                        socketio.emit('update_score', {'clicks': new_score, 'auto': user['auto']}, room=sid)
                except: continue

eventlet.spawn(background_cps_worker)

# --- ROUTES & SOCKETS ---
@app.route('/')
def index(): return render_template('index.html')

@socketio.on('login_action')
def handle_login(data):
    pseudo, password = data['pseudo'].strip(), data['password']
    res = supabase.table("users").select("*").eq("pseudo", pseudo).execute()
    
    if data['type'] == 'register':
        if res.data: return emit('error', "Pseudo déjà pris")
        h = bcrypt.generate_password_hash(password).decode('utf-8')
        supabase.table("users").insert({"pseudo":pseudo,"password":h,"clicks":0,"multiplier":1,"auto_clicker":0}).execute()
        emit('success', "Compte créé !")
    else:
        if not res.data or not bcrypt.check_password_hash(res.data[0]['password'], password):
            return emit('error', "Identifiants invalides")
        u = res.data[0]
        connected_users[request.sid] = {'pseudo':pseudo, 'mult':u['multiplier'], 'auto':u['auto_clicker']}
        join_room(pseudo)
        emit('login_ok', {'pseudo':pseudo, 'clicks':u['clicks'], 'mult':u['multiplier'], 'auto':u['auto_clicker'], 'guild':u['guild_name']})
        send_friends_list(pseudo, request.sid)

@socketio.on('add_click')
def handle_click():
    u = connected_users.get(request.sid)
    if not u: return
    res = supabase.table("users").select("clicks").eq("pseudo", u['pseudo']).execute()
    new_total = res.data[0]['clicks'] + u['mult']
    supabase.table("users").update({"clicks": new_total}).eq("pseudo", u['pseudo']).execute()
    emit('update_score', {'clicks': new_total})

@socketio.on('buy_upgrade')
def handle_buy(data):
    u = connected_users.get(request.sid)
    res = supabase.table("users").select("*").eq("pseudo", u['pseudo']).execute().data[0]
    if data['type'] == 'mult':
        cost = res['multiplier'] * 200
        if res['clicks'] >= cost:
            new_m = res['multiplier'] + 1
            supabase.table("users").update({"clicks":res['clicks']-cost, "multiplier":new_m}).eq("pseudo",u['pseudo']).execute()
            u['mult'] = new_m
            emit('update_full_state', {'clicks':res['clicks']-cost, 'mult':new_m})
    elif data['type'] == 'auto':
        cost = (res['auto_clicker']+1) * 750
        if res['clicks'] >= cost:
            new_a = res['auto_clicker'] + 1
            supabase.table("users").update({"clicks":res['clicks']-cost, "auto_clicker":new_a}).eq("pseudo",u['pseudo']).execute()
            u['auto'] = new_a
            emit('update_full_state', {'clicks':res['clicks']-cost, 'auto':new_a})

@socketio.on('add_friend')
def handle_friend(data):
    u = connected_users.get(request.sid)
    target = data.get('pseudo').strip()
    try:
        supabase.table("friends").insert({"user_pseudo":u['pseudo'], "friend_pseudo":target}).execute()
        emit('success', f"{target} ajouté !")
        send_friends_list(u['pseudo'], request.sid)
    except: emit('error', "Impossible d'ajouter cet ami")

def send_friends_list(pseudo, sid):
    res = supabase.table("friends").select("friend_pseudo").eq("user_pseudo", pseudo).execute()
    friends = [f['friend_pseudo'] for f in res.data]
    emit('update_friends', {'friends': friends}, room=sid)

@socketio.on('send_chat')
def handle_chat(data):
    u = connected_users.get(request.sid)
    if u: socketio.emit('new_chat', {'user': u['pseudo'], 'text': data['msg']})

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))

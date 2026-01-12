import os
import uuid
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room, leave_room

app = Flask(__name__)
app.config['SECRET_KEY'] = 'sorek_secret_key'

# Configuration Socket.io pour Render (mode eventlet)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet', max_http_buffer_size=1e7)

# Stockage des données en mémoire
users = {} 
shifumi_data = {} 

@app.route('/')
def index():
    # Cherche automatiquement le fichier index.html dans le dossier /templates
    return render_template('index.html')

@socketio.on('join')
def on_join(data):
    # request.sid est l'identifiant unique de la connexion du joueur
    users[request.sid] = {
        'pseudo': data.get('pseudo', 'Anonyme'), 
        'activity': '🏠 Accueil', 
        'room': None
    }
    emit('update_users', users, broadcast=True)

@socketio.on('disconnect')
def on_disc():
    if request.sid in users:
        user_room = users[request.sid]['room']
        if user_room:
            emit('fin_duel', room=user_room)
        del users[request.sid]
        emit('update_users', users, broadcast=True)

@socketio.on('envoyer_defi')
def send_defi(data):
    target = data.get('target_id')
    if target in users:
        emit('reception_defi', {
            'from_id': request.sid, 
            'from_name': users[request.sid]['pseudo'], 
            'game': data['game_type']
        }, room=target)

@socketio.on('accepter_defi')
def accept(data):
    p1, p2 = data['challenger_id'], request.sid
    gtype = data['game']
    
    if p1 in users and p2 in users:
        # Création d'une salle unique pour le duel
        rid = f"room_{uuid.uuid4().hex[:6]}"
        join_room(rid, sid=p1)
        join_room(rid, sid=p2)
        
        users[p1].update({'room': rid, 'activity': f'⚔️ {gtype}'})
        users[p2].update({'room': rid, 'activity': f'⚔️ {gtype}'})
        
        if gtype == "Shifumi": 
            shifumi_data[rid] = {}
            
        emit('start_duel', {'room': rid, 'game': gtype, 'opp': users[p2]['pseudo'], 'turn': True, 'sym': 'X'}, room=p1)
        emit('start_duel', {'room': rid, 'game': gtype, 'opp': users[p1]['pseudo'], 'turn': False, 'sym': 'O'}, room=p2)
        emit('update_users', users, broadcast=True)

@socketio.on('msg')
def msg(data):
    if request.sid in users:
        emit('new_msg', {'p': users[request.sid]['pseudo'], 'm': data['m']}, broadcast=True)

@socketio.on('draw_data')
def draw(data):
    # Envoie les coordonnées du dessin à tous les autres joueurs
    emit('draw_remote', data, broadcast=True, include_self=False)

@socketio.on('clear_canvas')
def clear_canvas():
    emit('canvas_cleared', broadcast=True, include_self=False)

@socketio.on('coup_morpion')
def coup_m(data):
    emit('receive_move', data, room=data['room'], include_self=False)

@socketio.on('quitter_duel')
def quit_d(data):
    rid = data.get('room')
    if rid:
        emit('fin_duel', room=rid)
        for sid in list(users.keys()):
            if users[sid]['room'] == rid:
                users[sid]['room'] = None
                users[sid]['activity'] = '🏠 Accueil'
        emit('update_users', users, broadcast=True)

if __name__ == '__main__':
    # Utilisation du port fourni par Render
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port)

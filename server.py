"""
============================================================================
COOKIE EMPIRE - ULTIMATE BACKEND SERVER
Production-ready Flask + Socket.IO backend with all features
============================================================================
"""

import eventlet
eventlet.monkey_patch()  # DOIT être la toute première instruction du fichier

import os
import logging
import time
import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps
from flask import Flask, render_template, request, session, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_bcrypt import Bcrypt
from supabase import create_client, Client

# Configurer les logs pour voir ce qui se passe sur Render
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================================================
# APP INITIALIZATION
# ============================================================================

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', secrets.token_hex(32))

bcrypt = Bcrypt(app)

# Initialisation de SocketIO avec les paramètres optimisés pour Render
socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode='eventlet',
    ping_timeout=60,
    ping_interval=25,
    logger=True,          # Mis à True temporairement pour débugger
    engineio_logger=True  # Mis à True temporairement pour débugger
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================================================
# DATABASE CONFIGURATION
# ============================================================================

SUPABASE_URL = os.environ.get('SUPABASE_URL', 'https://rzzhkdzjnjeeoqbtlles.supabase.co')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY', 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InJ6emhrZHpqbmplZW9xYnRsbGVzIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc2ODMxMTIwOCwiZXhwIjoyMDgzODg3MjA4fQ.0TRrVyMKV3EHXmw3HZKC86CQSo1ezMkISMbccLoyXrA')

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ============================================================================
# GAME CONFIGURATION
# ============================================================================

ACHIEVEMENTS = {
    'first_click': {'name': 'First Steps', 'desc': 'Make your first click', 'requirement': 1, 'reward': 100, 'icon': '👶'},
    'century': {'name': 'Century', 'desc': 'Reach 100 clicks', 'requirement': 100, 'reward': 500, 'icon': '💯'},
    'millennium': {'name': 'Millennium', 'desc': 'Reach 1,000 clicks', 'requirement': 1000, 'reward': 2000, 'icon': '🎯'},
    'mega_clicker': {'name': 'Mega Clicker', 'desc': 'Reach 10,000 clicks', 'requirement': 10000, 'reward': 10000, 'icon': '⚡'},
    'legendary': {'name': 'Legendary', 'desc': 'Reach 100,000 clicks', 'requirement': 100000, 'reward': 50000, 'icon': '👑'},
    'social_butterfly': {'name': 'Social Butterfly', 'desc': 'Add 5 friends', 'requirement': 5, 'reward': 1000, 'icon': '🦋'},
    'guild_founder': {'name': 'Guild Founder', 'desc': 'Create a guild', 'requirement': 1, 'reward': 5000, 'icon': '🏰'},
    'power_player': {'name': 'Power Player', 'desc': 'Reach x10 multiplier', 'requirement': 10, 'reward': 3000, 'icon': '💪'},
    'prestige_master': {'name': 'Prestige Master', 'desc': 'Prestige for the first time', 'requirement': 1, 'reward': 10000, 'icon': '🌟'},
    'streak_warrior': {'name': 'Streak Warrior', 'desc': 'Maintain 7-day login streak', 'requirement': 7, 'reward': 5000, 'icon': '🔥'},
}

RANKS = [
    (0, "Novice", "#6b7280"),
    (500, "Apprentice", "#10b981"),
    (2000, "Adept", "#3b82f6"),
    (5000, "Expert", "#8b5cf6"),
    (10000, "Master", "#f59e0b"),
    (25000, "Grandmaster", "#ef4444"),
    (50000, "Champion", "#ec4899"),
    (100000, "Legend", "#fbbf24"),
    (250000, "Mythic", "#06b6d4"),
    (500000, "Divine", "#a855f7"),
    (1000000, "Immortal", "#ffffff"),
]

# ============================================================================
# STATE MANAGEMENT
# ============================================================================

connected_users = {}  # {sid: {pseudo, mult, guild, auto, powerup_mult, powerup_end}}
rate_limits = {}  # {sid: {action: [timestamps]}}
active_powerups = {}  # {sid: {type, end_time}}

# ============================================================================
# UTILITY DECORATORS
# ============================================================================

def rate_limit(action, max_calls=10, period=60):
    """Rate limiting decorator"""
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            sid = request.sid
            now = time.time()
            
            if sid not in rate_limits:
                rate_limits[sid] = {}
            if action not in rate_limits[sid]:
                rate_limits[sid][action] = []
            
            rate_limits[sid][action] = [t for t in rate_limits[sid][action] if now - t < period]
            
            if len(rate_limits[sid][action]) >= max_calls:
                emit('error', f'Rate limit exceeded. Please wait.')
                return
            
            rate_limits[sid][action].append(now)
            return f(*args, **kwargs)
        return wrapped
    return decorator

def authenticated(f):
    """Require authentication"""
    @wraps(f)
    def wrapped(*args, **kwargs):
        if request.sid not in connected_users:
            emit('error', 'Not authenticated')
            return
        return f(*args, **kwargs)
    return wrapped

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def get_rank(clicks):
    """Get rank based on clicks"""
    for threshold, title, color in reversed(RANKS):
        if clicks >= threshold:
            return {'title': title, 'color': color, 'threshold': threshold}
    return {'title': 'Novice', 'color': '#6b7280', 'threshold': 0}

def check_achievements(pseudo, clicks=0, friends_count=0, mult=1, prestige=0, streak=0):
    """Check and unlock achievements"""
    try:
        res = supabase.table("user_achievements").select("achievement_id").eq("user_pseudo", pseudo).execute()
        unlocked = [a['achievement_id'] for a in res.data]
        newly_unlocked = []
        
        for aid, ach in ACHIEVEMENTS.items():
            if aid in unlocked:
                continue
                
            unlocked_now = False
            
            if aid == 'first_click' and clicks >= 1:
                unlocked_now = True
            elif aid == 'century' and clicks >= 100:
                unlocked_now = True
            elif aid == 'millennium' and clicks >= 1000:
                unlocked_now = True
            elif aid == 'mega_clicker' and clicks >= 10000:
                unlocked_now = True
            elif aid == 'legendary' and clicks >= 100000:
                unlocked_now = True
            elif aid == 'social_butterfly' and friends_count >= 5:
                unlocked_now = True
            elif aid == 'power_player' and mult >= 10:
                unlocked_now = True
            elif aid == 'prestige_master' and prestige >= 1:
                unlocked_now = True
            elif aid == 'streak_warrior' and streak >= 7:
                unlocked_now = True
                
            if unlocked_now:
                supabase.table("user_achievements").insert({
                    "user_pseudo": pseudo,
                    "achievement_id": aid
                }).execute()
                
                user_res = supabase.table("users").select("clicks").eq("pseudo", pseudo).execute()
                if user_res.data:
                    new_clicks = user_res.data[0]['clicks'] + ach['reward']
                    supabase.table("users").update({"clicks": new_clicks}).eq("pseudo", pseudo).execute()
                
                newly_unlocked.append({
                    'id': aid,
                    'name': ach['name'],
                    'desc': ach['desc'],
                    'reward': ach['reward'],
                    'icon': ach['icon']
                })
        
        return newly_unlocked
    except Exception as e:
        logger.error(f"Achievement check error: {e}")
        return []

def send_leaderboard(sid=None):
    try:
        if sid and sid in connected_users:
            pseudo = connected_users[sid]['pseudo']
            # Appel de la nouvelle fonction RPC
            res = supabase.rpc('get_relative_leaderboard', {'p_player_pseudo': pseudo}).execute()
            
            if not res.data:
                # Backup si le joueur n'a pas encore de score
                res = supabase.table("users").select("pseudo, clicks, guild_name, prestige_level").order("clicks", desc=True).limit(10).execute()
            
            socketio.emit('leaderboard_update', {'players': res.data, 'type': 'relative'}, room=sid)
        else:
            # Top 10 classique pour le broadcast global
            res = supabase.table("users").select("pseudo, clicks, guild_name, prestige_level").order("clicks", desc=True).limit(10).execute()
            socketio.emit('leaderboard_update', {'players': res.data, 'type': 'global'})
    except Exception as e:
        logger.error(f"Leaderboard error: {e}")

def update_social_data(pseudo):
    """Update all social data for a user"""
    try:
        # Friends
        res_friends = supabase.table("friendships").select("*").eq("status", "accepted").or_(
            f'user1.eq."{pseudo}",user2.eq."{pseudo}"'
        ).execute()
        friends = []
        for f in res_friends.data:
            friend_name = f['user2'] if f['user1'] == pseudo else f['user1']
            friends.append(friend_name)
        
        # Friend requests
        res_req = supabase.table("friendships").select("*").eq("status", "pending").eq("user2", pseudo).execute()
        friend_requests = [r['user1'] for r in res_req.data]

        # Guild invites
        res_guild = supabase.table("guild_invites").select("*").eq("target_user", pseudo).execute()
        guild_invites = [{'guild': g['guild_name'], 'from': g['invited_by'], 'message': g.get('message', '')} for g in res_guild.data]
        
        # Guild join requests (if founder)
        my_guild_res = supabase.table("guilds").select("name").eq("founder", pseudo).execute()
        guild_join_requests = []
        if my_guild_res.data:
            my_guild_name = my_guild_res.data[0]['name']
            join_req_res = supabase.table("guild_join_requests").select("*").eq("guild_name", my_guild_name).eq("status", "pending").execute()
            guild_join_requests = join_req_res.data

        # Update friend count
        supabase.table("users").update({"total_friends": len(friends)}).eq("pseudo", pseudo).execute()

        socketio.emit('social_update', {
            'friends': friends,
            'friend_requests': friend_requests,
            'guild_invites': guild_invites,
            'guild_join_requests': guild_join_requests
        }, room=pseudo)
        
        # Check social butterfly achievement
        if len(friends) >= 5:
            check_achievements(pseudo, friends_count=len(friends))
            
    except Exception as e:
        logger.error(f"Social update error: {e}")

# ============================================================================
# BACKGROUND TASKS
# ============================================================================

def leaderboard_background_task():
    """Update leaderboard periodically"""
    while True:
        socketio.sleep(20)
        send_leaderboard()

def auto_clicker_task():
    """Process auto-clickers"""
    while True:
        socketio.sleep(1)
        for sid, user in list(connected_users.items()):
            if user.get('auto', 0) > 0:
                try:
                    res = supabase.table("users").select("clicks").eq("pseudo", user['pseudo']).execute()
                    if res.data:
                        new_clicks = res.data[0]['clicks'] + user['auto']
                        supabase.table("users").update({"clicks": new_clicks}).eq("pseudo", user['pseudo']).execute()
                        socketio.emit('update_score', {'clicks': new_clicks}, room=sid)
                except Exception as e:
                    logger.error(f"Auto-clicker error: {e}")

def cleanup_expired_data():
    """Cleanup expired invites and trades"""
    while True:
        socketio.sleep(3600)  # Every hour
        try:
            now = datetime.now()
            supabase.table("guild_invites").delete().lt("expires_at", now.isoformat()).execute()
            supabase.table("trade_offers").delete().lt("expires_at", now.isoformat()).execute()
            logger.info("Cleaned up expired data")
        except Exception as e:
            logger.error(f"Cleanup error: {e}")

# ============================================================================
# ROUTES
# ============================================================================

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'timestamp': datetime.now().isoformat()}), 200

@app.route('/stats')
def stats():
    try:
        users_count = supabase.table("users").select("pseudo", count='exact').execute()
        guilds_count = supabase.table("guilds").select("name", count='exact').execute()
        return jsonify({
            'users': users_count.count if hasattr(users_count, 'count') else 0,
            'guilds': guilds_count.count if hasattr(guilds_count, 'count') else 0,
            'online': len(connected_users)
        }), 200
    except:
        return jsonify({'users': 0, 'guilds': 0, 'online': len(connected_users)}), 200

# ============================================================================
# AUTHENTICATION
# ============================================================================

@socketio.on('login_action')
@rate_limit('login', max_calls=5, period=60)
def auth_logic(data):
    try:
        pseudo = data['pseudo'].strip()[:20]
        password = data['password'][:50]
        action_type = data['type']
        
        if not pseudo or not password or len(password) < 6:
            return emit('error', "Format des identifiants invalide (min 6 caractères)")
        
        # 1. On récupère l'heure actuelle en UTC (Aware)
        now = datetime.now(timezone.utc)
        
        res = supabase.table("users").select("*").eq("pseudo", pseudo).execute()
        user = res.data[0] if res.data else None

        if action_type == 'register':
            if user:
                return emit('error', "Nom d'utilisateur déjà pris")
            
            hashed_pw = bcrypt.generate_password_hash(password).decode('utf-8')
            # Insertion avec last_online en UTC
            insert_res = supabase.table("users").insert({
                "pseudo": pseudo,
                "password": hashed_pw,
                "clicks": 0,
                "multiplier": 1,
                "auto_clicker": 0,
                "prestige_level": 0,
                "prestige_points": 0,
                "last_online": now.isoformat()
            }).execute()
            
            user = insert_res.data[0]
            emit('success', "Compte créé avec succès !")

        # Vérification du mot de passe
        if user and bcrypt.check_password_hash(user['password'], password):
            # 2. Mise à jour last_online (UTC aware)
            supabase.table("users").update({
                "last_online": now.isoformat()
            }).eq("pseudo", pseudo).execute()
            
            connected_users[request.sid] = {
                'pseudo': pseudo,
                'mult': user.get('multiplier', 1),
                'guild': user.get('guild_name'),
                'auto': user.get('auto_clicker', 0),
                'powerup_mult': 1,
                'powerup_end': 0
            }
            join_room(pseudo)
            
            rank_info = get_rank(user.get('clicks', 0))
            
            # 3. GESTION DU DAILY (Correction Naive vs Aware)
            daily_available = False
            last_daily_claim = user.get('last_daily_claim')
            
            if last_daily_claim:
                # On convertit la date DB en aware (UTC)
                last_claim_dt = datetime.fromisoformat(last_daily_claim.replace('Z', '+00:00'))
                # La soustraction fonctionne maintenant car NOW est aussi aware
                if now - last_claim_dt > timedelta(hours=20):
                    daily_available = True
            else:
                daily_available = True
            
            # Envoi des données au client
            emit('login_ok', {
                'pseudo': pseudo,
                'clicks': user.get('clicks', 0),
                'mult': user.get('multiplier', 1),
                'auto': user.get('auto_clicker', 0),
                'guild': user.get('guild_name'),
                'rank': rank_info,
                'prestige': user.get('prestige_level', 0),
                'prestige_points': user.get('prestige_points', 0),
                'daily_available': daily_available,
                'daily_streak': user.get('daily_streak', 0),
                'gems': user.get('gems', 0),
                'settings': user.get('settings', {})
            })
            
            # Fonctions utilitaires
            send_leaderboard(request.sid)
            update_social_data(pseudo)
            # Attention : vérifie que get_achievements_data() ne prend pas d'argument 
            # ou envoie request.sid si nécessaire
            get_achievements_data() 
            
        else:
            emit('error', "Identifiants invalides")
            
    except Exception as e:
        logger.error(f"Auth error: {e}")
        emit('error', "Erreur d'authentification")

# ============================================================================
# GAME ACTIONS
# ============================================================================

@socketio.on('add_click')
@rate_limit('click', max_calls=50, period=1)
@authenticated
def add_click():
    u = connected_users.get(request.sid)
    
    try:
        res = supabase.table("users").select("clicks, multiplier").eq("pseudo", u['pseudo']).execute()
        if not res.data:
            return
            
        current_clicks = res.data[0]['clicks']
        mult = res.data[0]['multiplier']
        
        # Apply powerup
        powerup_mult = u.get('powerup_mult', 1)
        total_gain = mult * powerup_mult
        
        new_total = current_clicks + total_gain
        supabase.table("users").update({"clicks": new_total}).eq("pseudo", u['pseudo']).execute()
        
        rank_info = get_rank(new_total)
        emit('update_score', {'clicks': new_total, 'rank': rank_info, 'gain': total_gain})
        
        # Check achievements
        achievements = check_achievements(u['pseudo'], new_total, mult=mult)
        if achievements:
            emit('achievement_unlocked', {'achievements': achievements})
        
        if new_total % 100 == 0:
            send_leaderboard(request.sid)
        
        # Guild contribution
        if u.get('guild'):
            try:
                supabase.rpc('increment_guild_clicks', {
                    'p_guild_name': u['guild'],
                    'p_amount': total_gain
                }).execute()
            except:
                pass
                
    except Exception as e:
        logger.error(f"Click error: {e}")

@socketio.on('buy_upgrade')
@rate_limit('upgrade', max_calls=10, period=10)
@authenticated
def buy_upgrade(data):
    u = connected_users.get(request.sid)
    upgrade_type = data.get('type', 'mult')
    
    try:
        res = supabase.table("users").select("clicks, multiplier, auto_clicker").eq("pseudo", u['pseudo']).execute()
        if not res.data:
            return
            
        clicks = res.data[0]['clicks']
        mult = res.data[0]['multiplier']
        auto = res.data[0].get('auto_clicker', 0)
        
        if upgrade_type == 'mult':
            cost = mult * 100
            if clicks >= cost:
                new_mult = mult + 1
                supabase.table("users").update({
                    "clicks": clicks - cost,
                    "multiplier": new_mult
                }).eq("pseudo", u['pseudo']).execute()
                connected_users[request.sid]['mult'] = new_mult
                
                rank_info = get_rank(clicks - cost)
                emit('update_full_state', {'clicks': clicks - cost, 'mult': new_mult, 'rank': rank_info})
                emit('success', f"Multiplier upgraded to x{new_mult}! 🚀")
                
                check_achievements(u['pseudo'], clicks - cost, mult=new_mult)
            else:
                emit('error', "Not enough clicks!")
                
        elif upgrade_type == 'auto':
            cost = (auto + 1) * 1000
            if clicks >= cost:
                new_auto = auto + 1
                supabase.table("users").update({
                    "clicks": clicks - cost,
                    "auto_clicker": new_auto
                }).eq("pseudo", u['pseudo']).execute()
                connected_users[request.sid]['auto'] = new_auto
                
                rank_info = get_rank(clicks - cost)
                emit('update_full_state', {'clicks': clicks - cost, 'auto': new_auto, 'rank': rank_info})
                emit('success', f"Auto-clicker upgraded to {new_auto}/s! ⚡")
            else:
                emit('error', "Not enough clicks!")
                
    except Exception as e:
        logger.error(f"Upgrade error: {e}")

@socketio.on('buy_shop_item')
@rate_limit('shop', max_calls=10, period=60)
@authenticated
def buy_shop_item(data):
    u = connected_users.get(request.sid)
    item_id = data.get('item_id')
    
    try:
        # Get item
        item_res = supabase.table("shop_items").select("*").eq("id", item_id).execute()
        if not item_res.data:
            return emit('error', "Item not found")
        
        item = item_res.data[0]
        
        # Get user
        user_res = supabase.table("users").select("clicks, gems").eq("pseudo", u['pseudo']).execute()
        if not user_res.data:
            return
        
        clicks = user_res.data[0]['clicks']
        gems = user_res.data[0].get('gems', 0)
        
        # Check price
        if item['price_clicks'] and clicks < item['price_clicks']:
            return emit('error', "Not enough clicks!")
        if item['price_gems'] and gems < item['price_gems']:
            return emit('error', "Not enough gems!")
        
        # Deduct cost
        new_clicks = clicks - (item['price_clicks'] or 0)
        new_gems = gems - (item['price_gems'] or 0)
        supabase.table("users").update({"clicks": new_clicks, "gems": new_gems}).eq("pseudo", u['pseudo']).execute()
        
        # Apply effect
        effect = item['effect']
        if effect['type'] == 'mult':
            # Powerup
            connected_users[request.sid]['powerup_mult'] = effect['value']
            connected_users[request.sid]['powerup_end'] = time.time() + effect['duration']
            
            emit('powerup_activated', {
                'name': item['name'],
                'multiplier': effect['value'],
                'duration': effect['duration']
            })
            
            def end_powerup():
                socketio.sleep(effect['duration'])
                if request.sid in connected_users:
                    connected_users[request.sid]['powerup_mult'] = 1
                    socketio.emit('powerup_ended', room=request.sid)
            
            socketio.start_background_task(end_powerup)
            
        elif effect['type'] == 'auto':
            # Permanent auto boost
            new_auto = u['auto'] + effect['value']
            supabase.table("users").update({"auto_clicker": new_auto}).eq("pseudo", u['pseudo']).execute()
            connected_users[request.sid]['auto'] = new_auto
            emit('update_full_state', {'auto': new_auto})
            
        elif effect['type'] == 'mult_permanent':
            # Permanent mult boost
            new_mult = u['mult'] + effect['value']
            supabase.table("users").update({"multiplier": new_mult}).eq("pseudo", u['pseudo']).execute()
            connected_users[request.sid]['mult'] = new_mult
            emit('update_full_state', {'mult': new_mult})
        
        elif effect['type'] == 'instant_clicks':
            # Instant clicks
            instant_clicks = new_clicks + effect['value']
            supabase.table("users").update({"clicks": instant_clicks}).eq("pseudo", u['pseudo']).execute()
            emit('update_score', {'clicks': instant_clicks})
        
        emit('success', f"Purchased {item['name']}! 🎉")
        emit('update_score', {'clicks': new_clicks, 'gems': new_gems})
        
    except Exception as e:
        logger.error(f"Shop purchase error: {e}")

@socketio.on('claim_daily_reward')
@rate_limit('daily', max_calls=1, period=3600)
@authenticated
def claim_daily():
    u = connected_users.get(request.sid)
    
    try:
        res = supabase.table("users").select("*").eq("pseudo", u['pseudo']).execute()
        if not res.data:
            return
        
        user = res.data[0]
        can_claim = False
        
        if user.get('last_daily_claim'):
            last_claim = datetime.fromisoformat(user['last_daily_claim'].replace('Z', '+00:00'))
            hours_since = (datetime.now() - last_claim).total_seconds() / 3600
            
            if hours_since >= 20:
                can_claim = True
                # Check streak
                if hours_since <= 48:  # Within 48 hours = maintain streak
                    new_streak = user.get('daily_streak', 0) + 1
                else:
                    new_streak = 1
            else:
                return emit('error', "Daily reward already claimed!")
        else:
            can_claim = True
            new_streak = 1
        
        if can_claim:
            base_reward = 1000
            prestige_bonus = user.get('prestige_level', 0) * 500
            streak_bonus = new_streak * 100
            total_reward = base_reward + prestige_bonus + streak_bonus
            
            new_clicks = user['clicks'] + total_reward
            supabase.table("users").update({
                "clicks": new_clicks,
                "last_daily_claim": datetime.now().isoformat(),
                "daily_streak": new_streak
            }).eq("pseudo", u['pseudo']).execute()
            
            emit('update_score', {'clicks': new_clicks})
            emit('success', f"Daily reward claimed! +{total_reward} clicks! 🎁")
            emit('daily_claimed', {'reward': total_reward, 'streak': new_streak})
            
            # Check streak achievement
            check_achievements(u['pseudo'], streak=new_streak)
        
    except Exception as e:
        logger.error(f"Daily reward error: {e}")

@socketio.on('prestige')
@rate_limit('prestige', max_calls=1, period=300)
@authenticated
def prestige():
    u = connected_users.get(request.sid)
    
    try:
        res = supabase.table("users").select("clicks").eq("pseudo", u['pseudo']).execute()
        if not res.data:
            return
        
        clicks = res.data[0]['clicks']
        if clicks < 100000:
            return emit('error', "Need 100,000 clicks to prestige!")
        
        # Execute prestige
        prestige_res = supabase.rpc('prestige_user', {'p_pseudo': u['pseudo']}).execute()
        if prestige_res.data:
            result = prestige_res.data[0]
            
            # Reset client state
            connected_users[request.sid]['mult'] = 1
            connected_users[request.sid]['auto'] = 0
            
            emit('prestige_complete', {
                'new_level': result['new_prestige_level'],
                'points_gained': result['prestige_points_gained']
            })
            emit('update_full_state', {
                'clicks': 0,
                'mult': 1,
                'auto': 0,
                'prestige': result['new_prestige_level']
            })
            emit('success', f"Prestige level {result['new_prestige_level']} achieved! 🌟")
            
            # Check prestige achievement
            check_achievements(u['pseudo'], prestige=result['new_prestige_level'])
        
    except Exception as e:
        logger.error(f"Prestige error: {e}")

# ============================================================================
# SOCIAL - FRIENDS
# ============================================================================

@socketio.on('send_friend_request')
@rate_limit('friend_request', max_calls=10, period=60)
@authenticated
def send_friend_request(data):
    u = connected_users.get(request.sid)
    target = data['target'].strip()[:20]
    
    if target == u['pseudo']:
        return emit('error', "Cannot add yourself!")
    
    try:
        # Ensure consistent ordering
        user1, user2 = sorted([u['pseudo'], target])
        
        # Check if already exists
        check = supabase.table("friendships").select("*").eq("user1", user1).eq("user2", user2).execute()
        if check.data:
            return emit('error', "Friend request already exists!")
        
        # Create request
        supabase.table("friendships").insert({
            "user1": user1,
            "user2": user2,
            "status": "pending"
        }).execute()
        
        emit('success', f"Friend request sent to {target}!")
        socketio.emit('notif', f"{u['pseudo']} sent you a friend request!", room=target)
        update_social_data(target)
        
    except Exception as e:
        logger.error(f"Friend request error: {e}")
        emit('error', "User not found or error occurred")

@socketio.on('respond_friend_request')
@authenticated
def respond_friend_request(data):
    u = connected_users.get(request.sid)
    target = data['target']
    action = data['action']
    
    try:
        user1, user2 = sorted([u['pseudo'], target])
        
        if action == 'accept':
            supabase.table("friendships").update({
                "status": "accepted",
                "accepted_at": datetime.now().isoformat()
            }).eq("user1", user1).eq("user2", user2).execute()
            
            emit('success', f"You're now friends with {target}!")
            socketio.emit('notif', f"{u['pseudo']} accepted your friend request!", room=target)
        else:
            supabase.table("friendships").delete().eq("user1", user1).eq("user2", user2).execute()
            emit('success', "Friend request declined")
            
        update_social_data(u['pseudo'])
        update_social_data(target)
        
    except Exception as e:
        logger.error(f"Respond friend error: {e}")

@socketio.on('remove_friend')
@authenticated
def remove_friend(data):
    u = connected_users.get(request.sid)
    target = data['target']
    
    try:
        user1, user2 = sorted([u['pseudo'], target])
        supabase.table("friendships").delete().eq("user1", user1).eq("user2", user2).execute()
        
        update_social_data(u['pseudo'])
        update_social_data(target)
        emit('success', f"{target} removed from friends")
        
    except Exception as e:
        logger.error(f"Remove friend error: {e}")

# ============================================================================
# SOCIAL - GUILDS
# ============================================================================

@socketio.on('create_guild')
@rate_limit('create_guild', max_calls=1, period=3600)
@authenticated
def create_guild(data):
    u = connected_users.get(request.sid)
    name = data['name'].strip()[:20]
    description = data.get('description', '')[:200]
    emblem = data.get('emblem', '🛡️')
    
    try:
        supabase.table("guilds").insert({
            "name": name,
            "founder": u['pseudo'],
            "description": description,
            "emblem": emblem,
            "total_clicks": 0,
            "level": 1
        }).execute()
        
        supabase.table("users").update({"guild_name": name}).eq("pseudo", u['pseudo']).execute()
        connected_users[request.sid]['guild'] = name
        
        emit('update_full_state', {'guild': name})
        emit('success', f"Guild '{name}' created! 🏰")
        
        check_achievements(u['pseudo'])
        
    except:
        emit('error', "Guild name already taken")

@socketio.on('join_guild_request')
@rate_limit('join_guild', max_calls=5, period=60)
@authenticated
def join_guild_request(data):
    u = connected_users.get(request.sid)
    guild_name = data['name']
    message = data.get('message', '')[:200]
    
    try:
        guild_res = supabase.table("guilds").select("founder, min_clicks_to_join").eq("name", guild_name).execute()
        if not guild_res.data:
            return emit('error', "Guild not found")
        
        guild = guild_res.data[0]
        
        # Check requirements
        user_res = supabase.table("users").select("clicks").eq("pseudo", u['pseudo']).execute()
        if user_res.data[0]['clicks'] < guild['min_clicks_to_join']:
            return emit('error', f"Need {guild['min_clicks_to_join']} clicks to join!")
        
        supabase.table("guild_join_requests").insert({
            "guild_name": guild_name,
            "requester": u['pseudo'],
            "status": "pending",
            "message": message
        }).execute()
        
        emit('success', f"Join request sent to {guild_name}!")
        socketio.emit('notif', f"{u['pseudo']} wants to join your guild!", room=guild['founder'])
        update_social_data(guild['founder'])
        
    except Exception as e:
        logger.error(f"Join guild error: {e}")

@socketio.on('respond_guild_join_request')
@authenticated
def respond_guild_join_request(data):
    u = connected_users.get(request.sid)
    requester = data['requester']
    guild_name = data['guild_name']
    action = data['action']
    
    try:
        guild_res = supabase.table("guilds").select("founder, max_members, member_count").eq("name", guild_name).execute()
        if not guild_res.data or guild_res.data[0]['founder'] != u['pseudo']:
            return emit('error', "Not authorized")
        
        guild = guild_res.data[0]
        
        if action == 'accept':
            # Check member limit
            if guild['member_count'] >= guild['max_members']:
                return emit('error', "Guild is full!")
            
            supabase.table("users").update({"guild_name": guild_name}).eq("pseudo", requester).execute()
            socketio.emit('notif', f"Your request to join {guild_name} was accepted!", room=requester)
            
            for sid, user_data in connected_users.items():
                if user_data['pseudo'] == requester:
                    user_data['guild'] = guild_name
                    socketio.emit('update_full_state', {'guild': guild_name}, room=sid)
                    break
        else:
            socketio.emit('notif', f"Your request to join {guild_name} was declined", room=requester)

        supabase.table("guild_join_requests").delete().eq("guild_name", guild_name).eq("requester", requester).execute()
        
        emit('success', f"Request from {requester} processed")
        update_social_data(u['pseudo'])
        update_social_data(requester)
        
    except Exception as e:
        logger.error(f"Respond guild join error: {e}")

@socketio.on('invite_to_guild')
@rate_limit('guild_invite', max_calls=10, period=60)
@authenticated
def invite_to_guild(data):
    u = connected_users.get(request.sid)
    target = data['target']
    message = data.get('message', '')[:200]
    
    if not u.get('guild'):
        return emit('error', "You're not in a guild!")
    
    try:
        supabase.table("guild_invites").insert({
            "guild_name": u['guild'],
            "target_user": target,
            "invited_by": u['pseudo'],
            "message": message
        }).execute()
        
        emit('success', f"Guild invite sent to {target}!")
        update_social_data(target)
        
    except:
        emit('error', "Invite already sent or error occurred")

@socketio.on('respond_guild_invite')
@authenticated
def respond_guild_invite(data):
    u = connected_users.get(request.sid)
    guild_name = data['guild_name']
    action = data['action']
    
    try:
        if action == 'accept':
            supabase.table("users").update({"guild_name": guild_name}).eq("pseudo", u['pseudo']).execute()
            connected_users[request.sid]['guild'] = guild_name
            emit('update_full_state', {'guild': guild_name})
            emit('success', f"Welcome to {guild_name}! 🛡️")
        else:
            emit('success', "Guild invite declined")
            
        supabase.table("guild_invites").delete().eq("guild_name", guild_name).eq("target_user", u['pseudo']).execute()
        update_social_data(u['pseudo'])
        
    except Exception as e:
        logger.error(f"Respond guild invite error: {e}")

@socketio.on('leave_guild')
@authenticated
def leave_guild():
    u = connected_users.get(request.sid)
    
    if not u.get('guild'):
        return emit('error', "You're not in a guild!")
    
    try:
        supabase.table("users").update({"guild_name": None}).eq("pseudo", u['pseudo']).execute()
        connected_users[request.sid]['guild'] = None
        emit('update_full_state', {'guild': None})
        emit('success', "You've left your guild")
    except Exception as e:
        logger.error(f"Leave guild error: {e}")

@socketio.on('get_guilds')
def get_guilds():
    try:
        res = supabase.rpc('get_guild_leaderboard', {'p_limit': 20}).execute()
        emit('guild_list', {'guilds': res.data})
    except:
        res = supabase.table("guilds").select("*").order("total_clicks", desc=True).limit(20).execute()
        emit('guild_list', {'guilds': res.data})

@socketio.on('get_guild_data')
@authenticated
def get_guild_data():
    u = connected_users.get(request.sid)
    
    if not u.get('guild'):
        return
    
    try:
        guild_res = supabase.table("guilds").select("*").eq("name", u['guild']).execute()
        members_res = supabase.table("users").select("pseudo, clicks").eq("guild_name", u['guild']).order("clicks", desc=True).execute()
        
        if guild_res.data:
            guild_data = guild_res.data[0]
            guild_data['members'] = members_res.data
            guild_data['member_count'] = len(members_res.data)
            emit('guild_data', guild_data)
    except Exception as e:
        logger.error(f"Get guild data error: {e}")

# ============================================================================
# CHAT
# ============================================================================

@socketio.on('send_message')
@rate_limit('chat', max_calls=10, period=30)
@authenticated
def send_message(data):
    u = connected_users.get(request.sid)
    message = data['message'].strip()[:200]
    
    if not message:
        return
    
    try:
        supabase.table("chat_messages").insert({
            "user_pseudo": u['pseudo'],
            "channel": "global",
            "message": message
        }).execute()
        
        socketio.emit('new_message', {
            'user': u['pseudo'],
            'message': message,
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        logger.error(f"Chat error: {e}")

@socketio.on('get_chat_history')
def get_chat_history():
    try:
        res = supabase.table("chat_messages").select("*").eq("channel", "global").order("created_at", desc=True).limit(50).execute()
        messages = [{
            'user': m['user_pseudo'],
            'message': m['message'],
            'timestamp': m['created_at']
        } for m in reversed(res.data)]
        emit('chat_history', {'messages': messages})
    except Exception as e:
        logger.error(f"Chat history error: {e}")

# ============================================================================
# ACHIEVEMENTS & SHOP
# ============================================================================

@socketio.on('get_achievements')
@authenticated
def get_achievements_data():
    u = connected_users.get(request.sid)
    
    try:
        res = supabase.table("user_achievements").select("achievement_id").eq("user_pseudo", u['pseudo']).execute()
        unlocked_ids = [a['achievement_id'] for a in res.data]
        
        achievements_list = []
        for aid, ach in ACHIEVEMENTS.items():
            achievements_list.append({
                'id': aid,
                'name': ach['name'],
                'desc': ach['desc'],
                'reward': ach['reward'],
                'icon': ach['icon'],
                'unlocked': aid in unlocked_ids
            })
        
        emit('achievements_data', {'achievements': achievements_list})
    except Exception as e:
        logger.error(f"Get achievements error: {e}")

@socketio.on('get_shop_items')
def get_shop_items():
    try:
        res = supabase.table("shop_items").select("*").eq("available", True).execute()
        emit('shop_items', {'items': res.data})
    except Exception as e:
        logger.error(f"Get shop items error: {e}")

@socketio.on('get_social_data')
@authenticated
def get_social_data():
    u = connected_users.get(request.sid)
    update_social_data(u['pseudo'])

@socketio.on('get_leaderboard')
def get_leaderboard():
    send_leaderboard(request.sid)

# ============================================================================
# CONNECTION MANAGEMENT
# ============================================================================

@socketio.on('disconnect')
def handle_disconnect(*args): # Ajoute *args pour accepter les arguments envoyés par SocketIO
    if request.sid in connected_users:
        user = connected_users.pop(request.sid)
        logger.info(f"User {user.get('pseudo')} disconnected")
        
        if user.get('pseudo'):
            try:
                leave_room(user['pseudo'])
                # Correction ici : on utilise timezone.utc pour éviter le conflit avec Supabase
                supabase.table("users").update({
                    "last_online": datetime.now(timezone.utc).isoformat()
                }).eq("pseudo", user['pseudo']).execute()
            except Exception as e:
                logger.error(f"Error updating last_online: {e}")
                pass

@socketio.on_error_default
def default_error_handler(e):
    logger.error(f"Socket error: {e}")
    # On évite d'émettre une erreur si le client est déjà déconnecté
    try:
        emit('error', 'An error occurred')
    except:
        pass
# ============================================================================
# MAIN
# ============================================================================

if __name__ == '__main__':
    # Start background tasks
    socketio.start_background_task(leaderboard_background_task)
    socketio.start_background_task(auto_clicker_task)
    socketio.start_background_task(cleanup_expired_data)
    
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"🚀 Cookie Empire server starting on port {port}")
    
    socketio.run(
        app,
        host='0.0.0.0',
        port=port,
        debug=False,
        use_reloader=False
    )

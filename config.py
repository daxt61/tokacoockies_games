# Configuration du Jeu - Cookie Empire
GAME_CONFIG = {
    "ranks": [
        {"threshold": 0, "title": "Novice", "color": "#9ca3af"},
        {"threshold": 1000, "title": "Apprenti", "color": "#10b981"},
        {"threshold": 10000, "title": "Boulanger", "color": "#00d2ff"},
        {"threshold": 100000, "title": "Chef", "color": "#f59e0b"},
        {"threshold": 1000000, "title": "Maître du Cookie", "color": "#ef4444"}
    ],
    "shop_items": [
        {"id": "double_click", "name": "Double Clic", "cost": 5000, "icon": "🖱️", "description": "Double vos clics pendant 30s"},
        {"id": "frenzy", "name": "Frénésie", "cost": 15000, "icon": "🔥", "description": "Multiplie les gains par 5 pendant 15s"},
        {"id": "shield", "name": "Bouclier", "cost": 10000, "icon": "🛡️", "description": "Protège votre guilde"}
    ],
    "achievements": [
        {"id": "first_click", "name": "Premier Pas", "description": "Cliquez une fois", "threshold": 1, "reward": 100, "icon": "👶"},
        {"id": "click_1k", "name": "Travailleur", "description": "Atteindre 1,000 clics", "threshold": 1000, "reward": 500, "icon": "⚒️"},
        {"id": "click_10k", "name": "Entrepreneur", "description": "Atteindre 10,000 clics", "threshold": 10000, "reward": 2000, "icon": "💼"}
    ]
}

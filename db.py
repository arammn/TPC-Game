import json
import os

DB_FILE = "db.json"

DEFAULT_DB = {
    "registered_groups": [],
    "prefixes": [],
    "mutes": [],
    "history": [],
}

def load_db():
    if not os.path.exists(DB_FILE):
        return DEFAULT_DB.copy()
    with open(DB_FILE) as f:
        return json.load(f)

def save_db(data):
    with open(DB_FILE, "w") as f:
        json.dump(data, f, indent=2)

if not os.path.exists(DB_FILE):
    save_db(DEFAULT_DB)
else:
    db = load_db()
    for key, val in DEFAULT_DB.items():
        if key not in db:
            db[key] = val
    save_db(db)

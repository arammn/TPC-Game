import json

CONFIG_FILE = "config.json"

with open(CONFIG_FILE) as f:
    config = json.load(f)

BOT_TOKEN = config["BOT_TOKEN"]
GROUP_CHAT_ID = config["GROUP_CHAT_ID"]
ADMIN_IDS = config.get("ADMIN_IDS", [])

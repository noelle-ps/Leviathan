import json
import os
import datetime

WARNINGS_FILE = "warnings.json"


def load_warnings() -> dict:
    if os.path.exists(WARNINGS_FILE):
        try:
            with open(WARNINGS_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_warnings(data: dict) -> None:
    with open(WARNINGS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def add_warning(guild_id: int, user_id: int, mod_id: int, reason: str) -> int:
    data = load_warnings()
    gid = str(guild_id)
    uid = str(user_id)
    data.setdefault(gid, {}).setdefault(uid, [])
    data[gid][uid].append({
        "reason": reason,
        "mod_id": mod_id,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    })
    save_warnings(data)
    return len(data[gid][uid])


def get_warnings(guild_id: int, user_id: int) -> list:
    data = load_warnings()
    return data.get(str(guild_id), {}).get(str(user_id), [])


def clear_warnings(guild_id: int, user_id: int) -> int:
    data = load_warnings()
    gid = str(guild_id)
    uid = str(user_id)
    count = len(data.get(gid, {}).get(uid, []))
    if gid in data and uid in data[gid]:
        data[gid][uid] = []
        save_warnings(data)
    return count

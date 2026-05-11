import json
import os
import discord

OWNER_ID = 1136231768534569090
_ACCESS_FILE = "cmd_access.json"
_allowed: set[int] = set()


def _load():
    global _allowed
    if os.path.exists(_ACCESS_FILE):
        try:
            with open(_ACCESS_FILE) as f:
                _allowed = set(json.load(f).get("allowed", []))
            return
        except Exception:
            pass
    _allowed = set()


def _save():
    with open(_ACCESS_FILE, "w") as f:
        json.dump({"allowed": list(_allowed)}, f, indent=2)


_load()


def add_user(uid: int):
    _allowed.add(uid)
    _save()


def remove_user(uid: int):
    _allowed.discard(uid)
    _save()


def get_allowed() -> set[int]:
    return set(_allowed)


def has_cmd_access():
    async def predicate(interaction: discord.Interaction) -> bool:
        return interaction.user.id == OWNER_ID or interaction.user.id in _allowed
    return discord.app_commands.check(predicate)

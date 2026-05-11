import discord
import datetime
from discord import app_commands

VARS_REFERENCE = [
    # Server
    ("{server_name}",         "The server's name"),
    ("{server_id}",           "The server's ID"),
    ("{server_icon}",         "The server's icon URL (use in image/thumbnail fields)"),
    ("{server_member_count}", "Total number of members in the server"),
    ("{server_owner}",        "Mention of the server owner"),
    ("{server_owner_name}",   "Display name of the server owner"),
    ("{server_created}",      "Date the server was created"),
    ("{server_boost_level}",  "Server's current boost level"),
    ("{server_boosts}",       "Number of boosts the server has"),
    # User (the person who ran the command)
    ("{user_name}",           "Display name of the command user"),
    ("{user_tag}",            "Full tag of the command user (name#0000)"),
    ("{user_id}",             "ID of the command user"),
    ("{user_avatar}",         "Avatar URL of the command user (use in image/thumbnail fields)"),
    ("{user_mention}",        "Mention of the command user"),
    ("{user_joined}",         "Date the command user joined the server"),
    ("{user_created}",        "Date the command user's account was created"),
    ("{user_roles}",          "List of the command user's roles"),
    ("{user_role_count}",     "Number of roles the command user has"),
    # Channel
    ("{channel_name}",        "Name of the current channel"),
    ("{channel_mention}",     "Mention of the current channel (#channel)"),
    ("{channel_id}",          "ID of the current channel"),
    # Date / Time (UTC)
    ("{date}",                "Current date (UTC) e.g. May 07, 2026"),
    ("{time}",                "Current time (UTC) e.g. 14:30"),
    ("{datetime}",            "Current date and time (UTC) e.g. May 07, 2026 14:30"),
]


def resolve_vars(
    text: str,
    guild: discord.Guild | None,
    user: discord.Member | discord.User | None,
    channel: discord.abc.GuildChannel | None = None,
) -> str:
    if not text:
        return text

    now = datetime.datetime.now(datetime.timezone.utc)

    replacements: dict[str, str] = {
        "{date}":     now.strftime("%b %d, %Y"),
        "{time}":     now.strftime("%H:%M"),
        "{datetime}": now.strftime("%b %d, %Y %H:%M"),
    }

    if guild:
        replacements["{server_name}"]         = guild.name
        replacements["{server_id}"]           = str(guild.id)
        replacements["{server_icon}"]         = guild.icon.url if guild.icon else ""
        replacements["{server_member_count}"] = str(guild.member_count or 0)
        replacements["{server_boost_level}"]  = str(guild.premium_tier)
        replacements["{server_boosts}"]       = str(guild.premium_subscription_count or 0)
        if guild.owner:
            replacements["{server_owner}"]      = guild.owner.mention
            replacements["{server_owner_name}"] = guild.owner.display_name
        else:
            replacements["{server_owner}"]      = "Unknown"
            replacements["{server_owner_name}"] = "Unknown"
        replacements["{server_created}"] = guild.created_at.strftime("%b %d, %Y")

    if user:
        replacements["{user_name}"]    = user.display_name
        replacements["{user_tag}"]     = str(user)
        replacements["{user_id}"]      = str(user.id)
        replacements["{user_avatar}"]  = user.display_avatar.url
        replacements["{user_mention}"] = user.mention
        replacements["{user_created}"] = user.created_at.strftime("%b %d, %Y")
        if isinstance(user, discord.Member):
            replacements["{user_joined}"]     = user.joined_at.strftime("%b %d, %Y") if user.joined_at else "Unknown"
            roles = [r.name for r in user.roles if r.name != "@everyone"]
            replacements["{user_roles}"]      = ", ".join(roles) if roles else "None"
            replacements["{user_role_count}"] = str(len(roles))
        else:
            replacements["{user_joined}"]     = "N/A"
            replacements["{user_roles}"]      = "N/A"
            replacements["{user_role_count}"] = "N/A"

    if channel:
        replacements["{channel_name}"]    = channel.name
        replacements["{channel_id}"]      = str(channel.id)
        replacements["{channel_mention}"] = f"<#{channel.id}>"

    for placeholder, value in replacements.items():
        text = text.replace(placeholder, value)

    return text


# ── Autocomplete helpers ──────────────────────────────────────────────────────

# All variables — used for text fields (title, description, footer, author)
async def var_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    search = current.lstrip("{").lower()
    results = []
    for placeholder, desc in VARS_REFERENCE:
        if search in placeholder.lower():
            label = f"{placeholder}  —  {desc}"
            results.append(app_commands.Choice(name=label[:100], value=placeholder))
        if len(results) >= 25:
            break
    return results


# URL-type variables only — used for thumbnail and image fields
_URL_VARS = [
    ("{user_avatar}",  "Avatar URL of the command user"),
    ("{server_icon}",  "Server icon URL"),
]

async def url_var_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    search = current.lstrip("{").lower()
    return [
        app_commands.Choice(name=f"{p}  —  {d}", value=p)
        for p, d in _URL_VARS
        if search in p.lower()
    ]

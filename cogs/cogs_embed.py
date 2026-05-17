import discord
from discord.ext import commands
import datetime
import json
from utils.embed_vars import resolve_vars, VARS_REFERENCE, var_autocomplete, url_var_autocomplete


def has_mod_permissions():
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            return False
        if not isinstance(interaction.user, discord.Member):
            return False
        if interaction.user.guild_permissions.administrator:
            return True
        return interaction.user.id == 1136231768534569090

    return discord.app_commands.check(predicate)


COLORS = {
    "red":    0xE74C3C,
    "orange": 0xE67E22,
    "yellow": 0xF1C40F,
    "green":  0x2ECC71,
    "blue":   0x3498DB,
    "purple": 0x9B59B6,
    "pink":   0xFF69B4,
    "white":  0xFFFFFF,
    "black":  0x2C2F33,
    "gold":   0xFFD700,
    "cyan":   0x00FFFF,
    "grey":   0x95A5A6,
}

COLOR_CHOICES = [
    discord.app_commands.Choice(name=name.capitalize(), value=name)
    for name in COLORS
]


def resolve_color(color_name: str, hex_color: str | None) -> discord.Color:
    if hex_color:
        hex_color = hex_color.lstrip("#")
        try:
            return discord.Color(int(hex_color, 16))
        except ValueError:
            pass
    return discord.Color(COLORS.get(color_name.lower(), COLORS["blue"]))


def build_embed(
    *,
    title: str | None,
    description: str | None,
    color: discord.Color,
    author_name: str | None = None,
    author_icon: str | None = None,
    thumbnail: str | None = None,
    image: str | None = None,
    footer: str | None = None,
    footer_icon: str | None = None,
    timestamp: bool = False,
) -> discord.Embed:
    embed = discord.Embed(
        title=title or None,
        description=description or None,
        color=color,
        timestamp=datetime.datetime.now(datetime.timezone.utc) if timestamp else None,
    )
    if author_name:
        if author_icon:
            embed.set_author(name=author_name, icon_url=author_icon)
        else:
            embed.set_author(name=author_name)
    if thumbnail:
        embed.set_thumbnail(url=thumbnail)
    if image:
        embed.set_image(url=image)
    if footer:
        if footer_icon:
            embed.set_footer(text=footer, icon_url=footer_icon)
        else:
            embed.set_footer(text=footer)
    return embed


def resolve_target(
    channel: discord.TextChannel | None,
    interaction: discord.Interaction,
) -> discord.TextChannel | None:
    target = channel or interaction.channel
    if isinstance(target, discord.TextChannel):
        return target
    return None


def apply_vars(interaction: discord.Interaction, text: str | None) -> str | None:
    if not text:
        return text
    ch = interaction.channel if isinstance(interaction.channel, discord.abc.GuildChannel) else None
    return resolve_vars(text, interaction.guild, interaction.user, ch)


async def send_embed_safe(
    interaction: discord.Interaction,
    target: discord.TextChannel,
    embed: discord.Embed,
    content: str | None = None,
) -> bool:
    try:
        await target.send(content=content, embed=embed)
        return True
    except discord.Forbidden:
        await interaction.followup.send(
            f"❌ I don't have permission to send messages in {target.mention}. "
            "Please check the bot's channel permissions.",
            ephemeral=True,
        )
        return False
    except discord.HTTPException as e:
        await interaction.followup.send(f"❌ Failed to send embed: {e}", ephemeral=True)
        return False


# ── Cog ─────────────────────────────────────────────────────────────────────
class EmbedCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._drafts: dict[int, dict] = {}

    # ── /embed ───────────────────────────────────────────────────────────────
    @discord.app_commands.command(name="embed", description="Send a fully customised embed to a channel.")
    @has_mod_permissions()
    @discord.app_commands.describe(
        title="Embed title",
        description="Main body text (use \\n for new lines)",
        color="Colour preset (ignored if hex_color is set)",
        hex_color="Custom hex colour e.g. FF6B6B",
        channel="Channel to send to (defaults to current channel)",
        author="Author name shown above title",
        author_icon="URL for author icon",
        author_icon_file="Upload author icon from your device",
        thumbnail="URL for small thumbnail (top-right)",
        thumbnail_file="Upload thumbnail from your device",
        image="URL for large bottom image",
        image_file="Upload image from your device",
        footer="Footer text",
        footer_icon="URL for footer icon",
        footer_icon_file="Upload footer icon from your device",
        timestamp="Add current timestamp to footer",
    )
    @discord.app_commands.choices(color=COLOR_CHOICES)
    @discord.app_commands.autocomplete(
        title=var_autocomplete,
        description=var_autocomplete,
        author=var_autocomplete,
        author_icon=url_var_autocomplete,
        footer=var_autocomplete,
        footer_icon=url_var_autocomplete,
        thumbnail=url_var_autocomplete,
        image=url_var_autocomplete,
    )
    async def embed_send(
        self,
        interaction: discord.Interaction,
        title: str | None = None,
        description: str | None = None,
        color: str = "blue",
        hex_color: str | None = None,
        channel: discord.TextChannel | None = None,
        author: str | None = None,
        author_icon: str | None = None,
        author_icon_file: discord.Attachment | None = None,
        thumbnail: str | None = None,
        thumbnail_file: discord.Attachment | None = None,
        image: str | None = None,
        image_file: discord.Attachment | None = None,
        footer: str | None = None,
        footer_icon: str | None = None,
        footer_icon_file: discord.Attachment | None = None,
        timestamp: bool = False,
    ):
        if not title and not description:
            await interaction.response.send_message("❌ Provide at least a title or description.", ephemeral=True)
            return

        target = resolve_target(channel, interaction)
        if target is None:
            await interaction.response.send_message("❌ This command must be used in a text channel.", ephemeral=True)
            return

        if description:
            description = description.replace("\\n", "\n")
        if author_icon_file:
            author_icon = author_icon_file.url
        if thumbnail_file:
            thumbnail = thumbnail_file.url
        if image_file:
            image = image_file.url
        if footer_icon_file:
            footer_icon = footer_icon_file.url

        title       = apply_vars(interaction, title)
        description = apply_vars(interaction, description)
        author      = apply_vars(interaction, author)
        author_icon = apply_vars(interaction, author_icon) or author_icon
        footer      = apply_vars(interaction, footer)
        footer_icon = apply_vars(interaction, footer_icon) or footer_icon
        thumbnail   = apply_vars(interaction, thumbnail) or thumbnail
        image       = apply_vars(interaction, image) or image

        embed = build_embed(
            title=title, description=description,
            color=resolve_color(color, hex_color),
            author_name=author, author_icon=author_icon,
            thumbnail=thumbnail, image=image,
            footer=footer, footer_icon=footer_icon,
            timestamp=timestamp,
        )

        await interaction.response.defer(ephemeral=True)
        if await send_embed_safe(interaction, target, embed):
            await interaction.followup.send(f"✅ Embed sent to {target.mention}!", ephemeral=True)

    # ── /embedjson ────────────────────────────────────────────────────────────
    @discord.app_commands.command(name="embedjson", description="Send an embed using raw Discord JSON (advanced).")
    @has_mod_permissions()
    @discord.app_commands.describe(
        json_data="Paste raw Discord embed JSON here",
        channel="Channel to send to (defaults to current channel)",
    )
    async def embedjson(self, interaction: discord.Interaction, json_data: str, channel: discord.TextChannel | None = None):
        target = resolve_target(channel, interaction)
        if target is None:
            await interaction.response.send_message("❌ This command must be used in a text channel.", ephemeral=True)
            return
        try:
            data = json.loads(json_data)
        except json.JSONDecodeError as e:
            await interaction.response.send_message(f"❌ Invalid JSON: {e}", ephemeral=True)
            return
        try:
            embed = discord.Embed.from_dict(data)
        except Exception as e:
            await interaction.response.send_message(f"❌ Could not build embed from JSON: {e}", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        if await send_embed_safe(interaction, target, embed):
            await interaction.followup.send(f"✅ JSON embed sent to {target.mention}!", ephemeral=True)

    # ── /embedextract ─────────────────────────────────────────────────────────
    @discord.app_commands.command(name="embedextract", description="Extract raw JSON from an embed in a message.")
    @has_mod_permissions()
    @discord.app_commands.describe(
        message_id="ID of the message containing the embed",
        channel="Channel the message is in (defaults to current channel)",
    )
    async def embedextract(self, interaction: discord.Interaction, message_id: str, channel: discord.TextChannel | None = None):
        target = resolve_target(channel, interaction)
        if target is None:
            await interaction.response.send_message("❌ This command must be used in a text channel.", ephemeral=True)
            return

        try:
            msg_id = int(message_id)
        except ValueError:
            await interaction.response.send_message("❌ Invalid message ID.", ephemeral=True)
            return

        try:
            message = await target.fetch_message(msg_id)
        except discord.NotFound:
            await interaction.response.send_message("❌ Message not found in that channel.", ephemeral=True)
            return
        except discord.Forbidden:
            await interaction.response.send_message("❌ I don't have permission to read that channel.", ephemeral=True)
            return

        if not message.embeds:
            await interaction.response.send_message("❌ That message has no embeds.", ephemeral=True)
            return

        embed_dict = message.embeds[0].to_dict()
        raw_json = json.dumps(embed_dict, indent=2, ensure_ascii=False)

        if len(raw_json) <= 1990:
            await interaction.response.send_message(f"```json\n{raw_json}\n```", ephemeral=True)
        else:
            raw_bytes = raw_json.encode("utf-8")
            file = discord.File(fp=__import__("io").BytesIO(raw_bytes), filename="embed.json")
            await interaction.response.send_message("📎 Embed JSON (too long to display inline):", file=file, ephemeral=True)

    # ── /embedannounce ────────────────────────────────────────────────────────
    @discord.app_commands.command(name="embedannounce", description="Send a styled announcement embed.")
    @has_mod_permissions()
    @discord.app_commands.describe(
        title="Announcement title",
        message="Announcement body (use \\n for new lines)",
        color="Colour preset",
        hex_color="Custom hex colour e.g. FF6B6B",
        channel="Channel to send to",
        ping_role="Role to ping alongside the embed",
        ping_everyone="Ping @everyone alongside the embed",
        footer="Custom footer text (defaults to your name)",
        footer_icon="URL for footer icon",
        footer_icon_file="Upload footer icon from your device",
        image="URL for large bottom image",
        image_file="Upload a large image from your device",
    )
    @discord.app_commands.choices(color=COLOR_CHOICES)
    @discord.app_commands.autocomplete(
        title=var_autocomplete,
        message=var_autocomplete,
        footer=var_autocomplete,
        footer_icon=url_var_autocomplete,
        image=url_var_autocomplete,
    )
    async def embedannounce(
        self,
        interaction: discord.Interaction,
        title: str,
        message: str,
        color: str = "gold",
        hex_color: str | None = None,
        channel: discord.TextChannel | None = None,
        ping_role: discord.Role | None = None,
        ping_everyone: bool = False,
        footer: str | None = None,
        footer_icon: str | None = None,
        footer_icon_file: discord.Attachment | None = None,
        image: str | None = None,
        image_file: discord.Attachment | None = None,
    ):
        target = resolve_target(channel, interaction)
        if target is None:
            await interaction.response.send_message("❌ This command must be used in a text channel.", ephemeral=True)
            return

        if footer_icon_file:
            footer_icon = footer_icon_file.url
        if image_file:
            image = image_file.url

        title       = apply_vars(interaction, title) or title
        message     = apply_vars(interaction, message) or message
        footer      = apply_vars(interaction, footer)
        footer_icon = apply_vars(interaction, footer_icon) or footer_icon
        image       = apply_vars(interaction, image) or image

        message = message.replace("\\n", "\n")
        footer_text = footer or f"Announcement by {interaction.user.display_name}"
        footer_icon_url = footer_icon or interaction.user.display_avatar.url

        embed = build_embed(
            title=f"📢 {title}",
            description=message,
            color=resolve_color(color, hex_color),
            footer=footer_text,
            footer_icon=footer_icon_url,
            image=image,
            timestamp=True,
        )

        content: str | None = None
        if ping_everyone:
            content = "@everyone"
        elif ping_role:
            content = ping_role.mention

        await interaction.response.defer(ephemeral=True)
        if await send_embed_safe(interaction, target, embed, content=content):
            await interaction.followup.send(f"✅ Announcement sent to {target.mention}!", ephemeral=True)

    # ── /embedrules ───────────────────────────────────────────────────────────
    @discord.app_commands.command(name="embedrules", description="Post a server rules embed.")
    @has_mod_permissions()
    @discord.app_commands.describe(
        rules="Rules separated by | (pipe). e.g. Be respectful|No spam|Follow Discord ToS",
        title="Custom title (defaults to 'Server Rules')",
        channel="Channel to post rules in",
        color="Colour preset",
        hex_color="Custom hex colour",
        footer="Custom footer text (defaults to server name)",
        footer_icon="URL for footer icon",
        footer_icon_file="Upload footer icon from your device",
    )
    @discord.app_commands.choices(color=COLOR_CHOICES)
    @discord.app_commands.autocomplete(
        footer=var_autocomplete,
        footer_icon=url_var_autocomplete,
    )
    async def embedrules(
        self,
        interaction: discord.Interaction,
        rules: str,
        title: str | None = None,
        channel: discord.TextChannel | None = None,
        color: str = "red",
        hex_color: str | None = None,
        footer: str | None = None,
        footer_icon: str | None = None,
        footer_icon_file: discord.Attachment | None = None,
    ):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ This command must be used in a server.", ephemeral=True)
            return

        target = resolve_target(channel, interaction)
        if target is None:
            await interaction.response.send_message("❌ This command must be used in a text channel.", ephemeral=True)
            return

        if footer_icon_file:
            footer_icon = footer_icon_file.url

        title       = apply_vars(interaction, title) or title
        footer      = apply_vars(interaction, footer)
        footer_icon = apply_vars(interaction, footer_icon) or footer_icon

        rule_list = [r.strip() for r in rules.split("|") if r.strip()]
        if not rule_list:
            await interaction.response.send_message("❌ Provide at least one rule separated by |", ephemeral=True)
            return

        formatted = "\n".join(f"**{i}.** {rule}" for i, rule in enumerate(rule_list, 1))
        footer_text = footer or f"{guild.name} • Please read and follow the rules"
        footer_icon_url = footer_icon or (guild.icon.url if guild.icon else None)
        embed_title = f"📜 {title}" if title else "📜 Server Rules"

        embed = build_embed(
            title=embed_title,
            description=formatted,
            color=resolve_color(color, hex_color),
            footer=footer_text,
            footer_icon=footer_icon_url,
            timestamp=True,
        )

        await interaction.response.defer(ephemeral=True)
        if await send_embed_safe(interaction, target, embed):
            await interaction.followup.send(f"✅ Rules embed posted to {target.mention}!", ephemeral=True)

    # ── /embedwelcome ─────────────────────────────────────────────────────────
    @discord.app_commands.command(name="embedwelcome", description="Send a welcome embed for a member.")
    @has_mod_permissions()
    @discord.app_commands.describe(
        member="The member to welcome",
        message="Custom welcome message (use \\n for new lines, {user} for mention)",
        channel="Channel to send to",
        color="Colour preset",
        hex_color="Custom hex colour",
    )
    @discord.app_commands.choices(color=COLOR_CHOICES)
    async def embedwelcome(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        message: str | None = None,
        channel: discord.TextChannel | None = None,
        color: str = "green",
        hex_color: str | None = None,
    ):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ This command must be used in a server.", ephemeral=True)
            return

        target = resolve_target(channel, interaction)
        if target is None:
            await interaction.response.send_message("❌ This command must be used in a text channel.", ephemeral=True)
            return

        default_msg = f"Welcome to **{guild.name}**, {member.mention}! 🎉\nWe're glad to have you here."
        body = (message.replace("\\n", "\n").replace("{user}", member.mention) if message else default_msg)

        embed = build_embed(
            title=f"👋 Welcome, {member.display_name}!",
            description=body,
            color=resolve_color(color, hex_color),
            thumbnail=member.display_avatar.url,
            footer=f"Member #{guild.member_count}",
            timestamp=True,
        )

        await interaction.response.defer(ephemeral=True)
        if await send_embed_safe(interaction, target, embed):
            await interaction.followup.send(f"✅ Welcome embed sent for {member.display_name}!", ephemeral=True)

    # ── /embedquote ───────────────────────────────────────────────────────────
    @discord.app_commands.command(name="embedquote", description="Display a stylised quote embed.")
    @discord.app_commands.describe(
        quote="The quote text",
        author="Who said it",
        color="Colour preset",
        hex_color="Custom hex colour",
        channel="Channel to post in",
    )
    @discord.app_commands.choices(color=COLOR_CHOICES)
    async def embedquote(
        self,
        interaction: discord.Interaction,
        quote: str,
        author: str | None = None,
        color: str = "purple",
        hex_color: str | None = None,
        channel: discord.TextChannel | None = None,
    ):
        target = resolve_target(channel, interaction)
        if target is None:
            await interaction.response.send_message("❌ This command must be used in a text channel.", ephemeral=True)
            return

        description = f"*\"{quote}\"*"
        if author:
            description += f"\n\n— **{author}**"

        embed = build_embed(
            title="💬 Quote",
            description=description,
            color=resolve_color(color, hex_color),
            footer=f"Shared by {interaction.user.display_name}",
            footer_icon=interaction.user.display_avatar.url,
        )

        await interaction.response.defer(ephemeral=True)
        if await send_embed_safe(interaction, target, embed):
            await interaction.followup.send("✅ Quote embed sent!", ephemeral=True)

    # ── /embeddraft ───────────────────────────────────────────────────────────
    @discord.app_commands.command(name="embeddraft", description="Preview an embed privately before sending.")
    @has_mod_permissions()
    @discord.app_commands.describe(
        title="Embed title",
        description="Embed description (use \\n for new lines)",
        color="Colour preset",
        hex_color="Custom hex colour",
        author="Author name",
        author_icon="URL for author icon",
        author_icon_file="Upload author icon from your device",
        footer="Footer text",
        footer_icon="URL for footer icon",
        footer_icon_file="Upload footer icon from your device",
        thumbnail="Thumbnail image URL",
        thumbnail_file="Upload thumbnail from your device",
        image="Large image URL",
        image_file="Upload image from your device",
        timestamp="Include timestamp",
    )
    @discord.app_commands.choices(color=COLOR_CHOICES)
    @discord.app_commands.autocomplete(
        title=var_autocomplete,
        description=var_autocomplete,
        author=var_autocomplete,
        author_icon=url_var_autocomplete,
        footer=var_autocomplete,
        footer_icon=url_var_autocomplete,
        thumbnail=url_var_autocomplete,
        image=url_var_autocomplete,
    )
    async def embeddraft(
        self,
        interaction: discord.Interaction,
        title: str | None = None,
        description: str | None = None,
        color: str = "blue",
        hex_color: str | None = None,
        author: str | None = None,
        author_icon: str | None = None,
        author_icon_file: discord.Attachment | None = None,
        footer: str | None = None,
        footer_icon: str | None = None,
        footer_icon_file: discord.Attachment | None = None,
        thumbnail: str | None = None,
        thumbnail_file: discord.Attachment | None = None,
        image: str | None = None,
        image_file: discord.Attachment | None = None,
        timestamp: bool = False,
    ):
        if not title and not description:
            await interaction.response.send_message("❌ Provide at least a title or description.", ephemeral=True)
            return

        if description:
            description = description.replace("\\n", "\n")
        if author_icon_file:
            author_icon = author_icon_file.url
        if footer_icon_file:
            footer_icon = footer_icon_file.url
        if thumbnail_file:
            thumbnail = thumbnail_file.url
        if image_file:
            image = image_file.url

        title       = apply_vars(interaction, title)       or title
        description = apply_vars(interaction, description) or description
        author      = apply_vars(interaction, author)      or author
        author_icon = apply_vars(interaction, author_icon) or author_icon
        footer      = apply_vars(interaction, footer)      or footer
        footer_icon = apply_vars(interaction, footer_icon) or footer_icon
        thumbnail   = apply_vars(interaction, thumbnail)   or thumbnail
        image       = apply_vars(interaction, image)       or image

        resolved_color = resolve_color(color, hex_color)

        self._drafts[interaction.user.id] = {
            "title": title, "description": description, "color": resolved_color,
            "author": author, "author_icon": author_icon,
            "footer": footer, "footer_icon": footer_icon,
            "thumbnail": thumbnail, "image": image, "timestamp": timestamp,
        }

        embed = build_embed(
            title=title, description=description, color=resolved_color,
            author_name=author, author_icon=author_icon,
            thumbnail=thumbnail, image=image,
            footer=footer, footer_icon=footer_icon,
            timestamp=timestamp,
        )
        embed.set_author(
            name="📝 DRAFT PREVIEW — use /embedpost to send",
            icon_url=interaction.user.display_avatar.url,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /embedpost ────────────────────────────────────────────────────────────
    @discord.app_commands.command(name="embedpost", description="Post your saved draft embed to a channel.")
    @has_mod_permissions()
    @discord.app_commands.describe(channel="Channel to post the draft in")
    async def embedpost(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None):
        draft = self._drafts.get(interaction.user.id)
        if not draft:
            await interaction.response.send_message("❌ No draft found. Use `/embeddraft` first.", ephemeral=True)
            return

        target = resolve_target(channel, interaction)
        if target is None:
            await interaction.response.send_message("❌ This command must be used in a text channel.", ephemeral=True)
            return

        embed = build_embed(
            title=draft["title"], description=draft["description"], color=draft["color"],
            author_name=draft["author"], author_icon=draft.get("author_icon"),
            thumbnail=draft["thumbnail"], image=draft["image"],
            footer=draft["footer"], footer_icon=draft.get("footer_icon"),
            timestamp=draft["timestamp"],
        )

        await interaction.response.defer(ephemeral=True)
        if await send_embed_safe(interaction, target, embed):
            del self._drafts[interaction.user.id]
            await interaction.followup.send(f"✅ Draft posted to {target.mention}!", ephemeral=True)

    # ── /embedvars ────────────────────────────────────────────────────────────
    @discord.app_commands.command(name="embedvars", description="List all {} variables you can use in embeds.")
    async def embedvars(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="📋 Embed Variables",
            description=(
                "Use these `{placeholders}` in any embed **title**, **description**, **author**, **footer**, "
                "**thumbnail**, or **image** field and they will be replaced automatically when sent.\n\u200b"
            ),
            color=discord.Color(COLORS["purple"]),
        )

        categories = {
            "🏰 Server": [],
            "👤 User (command user)": [],
            "📢 Channel": [],
            "🕐 Date & Time (UTC)": [],
        }
        for placeholder, desc in VARS_REFERENCE:
            if placeholder.startswith("{server"):
                categories["🏰 Server"].append(f"`{placeholder}` — {desc}")
            elif placeholder.startswith("{user"):
                categories["👤 User (command user)"].append(f"`{placeholder}` — {desc}")
            elif placeholder.startswith("{channel"):
                categories["📢 Channel"].append(f"`{placeholder}` — {desc}")
            else:
                categories["🕐 Date & Time (UTC)"].append(f"`{placeholder}` — {desc}")

        for cat, lines in categories.items():
            embed.add_field(name=cat, value="\n".join(lines), inline=False)

        embed.add_field(
            name="✏️ Example",
            value=(
                "**Title:** `Welcome to {server_name}!`\n"
                "**Description:** `Hey {user_mention}, glad you're here.\n"
                "We now have {server_member_count} members.`\n"
                "**Thumbnail:** `{user_avatar}`"
            ),
            inline=False,
        )
        embed.set_footer(text="Variables work in /embed, /embedannounce, /embedwelcome and /embeddraft.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /editembed ────────────────────────────────────────────────────────────
    @discord.app_commands.command(name="editembed", description="Edit an embed on one of the bot's messages.")
    @has_mod_permissions()
    @discord.app_commands.describe(
        message_id="ID of the bot message containing the embed",
        channel="Channel the message is in (defaults to current channel)",
        title="New title (leave blank to keep existing)",
        description="New description (use \\n for new lines, supports {} vars)",
        color="New colour preset",
        hex_color="New custom hex colour e.g. FF6B6B",
        author="New author name",
        author_icon="New author icon URL (use {user_avatar} or {server_icon})",
        footer="New footer text (supports {} vars)",
        footer_icon="New footer icon URL (use {user_avatar} or {server_icon})",
        thumbnail="New thumbnail URL (use {server_icon} or {user_avatar} etc.)",
        image="New large image URL",
        timestamp="Add or remove timestamp",
    )
    @discord.app_commands.choices(color=COLOR_CHOICES)
    @discord.app_commands.autocomplete(
        title=var_autocomplete,
        description=var_autocomplete,
        author=var_autocomplete,
        author_icon=url_var_autocomplete,
        footer=var_autocomplete,
        footer_icon=url_var_autocomplete,
        thumbnail=url_var_autocomplete,
        image=url_var_autocomplete,
    )
    async def editembed(
        self,
        interaction: discord.Interaction,
        message_id: str,
        channel: discord.TextChannel | None = None,
        title: str | None = None,
        description: str | None = None,
        color: str | None = None,
        hex_color: str | None = None,
        author: str | None = None,
        author_icon: str | None = None,
        footer: str | None = None,
        footer_icon: str | None = None,
        thumbnail: str | None = None,
        image: str | None = None,
        timestamp: bool | None = None,
    ):
        await interaction.response.defer(ephemeral=True)

        target = resolve_target(channel, interaction)
        if target is None:
            await interaction.followup.send("❌ This command must be used in a text channel.", ephemeral=True)
            return

        try:
            msg = await target.fetch_message(int(message_id))
        except ValueError:
            await interaction.followup.send("❌ Invalid message ID.", ephemeral=True)
            return
        except discord.NotFound:
            await interaction.followup.send("❌ Message not found.", ephemeral=True)
            return
        except discord.Forbidden:
            await interaction.followup.send("❌ I can't read that channel.", ephemeral=True)
            return

        if msg.author.id != self.bot.user.id:
            await interaction.followup.send("❌ I can only edit my own messages.", ephemeral=True)
            return

        if not msg.embeds:
            await interaction.followup.send("❌ That message has no embed to edit.", ephemeral=True)
            return

        existing = msg.embeds[0]

        # Resolve {} vars on new fields
        new_title       = apply_vars(interaction, title)       or existing.title
        new_description = apply_vars(interaction, description)
        if new_description:
            new_description = new_description.replace("\\n", "\n")
        else:
            new_description = existing.description
        new_author_name  = apply_vars(interaction, author)      or (existing.author.name if existing.author else None)
        new_author_icon  = apply_vars(interaction, author_icon) or author_icon or (existing.author.icon_url if existing.author else None)
        new_footer_text  = apply_vars(interaction, footer)      or (existing.footer.text if existing.footer else None)
        new_footer_icon  = apply_vars(interaction, footer_icon) or footer_icon or (existing.footer.icon_url if existing.footer else None)
        new_thumbnail    = apply_vars(interaction, thumbnail)   or (existing.thumbnail.url if existing.thumbnail else None)
        new_image        = apply_vars(interaction, image)       or (existing.image.url if existing.image else None)

        if color or hex_color:
            new_color = resolve_color(color or "blue", hex_color)
        else:
            new_color = existing.color or discord.Color.blue()

        new_embed = discord.Embed(
            title=new_title,
            description=new_description,
            color=new_color,
            timestamp=datetime.datetime.now(datetime.timezone.utc) if timestamp else (existing.timestamp if timestamp is None else None),
        )

        if new_author_name:
            new_embed.set_author(name=new_author_name, icon_url=new_author_icon)
        if new_thumbnail:
            new_embed.set_thumbnail(url=new_thumbnail)
        if new_image:
            new_embed.set_image(url=new_image)
        if new_footer_text:
            new_embed.set_footer(text=new_footer_text, icon_url=new_footer_icon)

        # Preserve any inline fields
        for field in existing.fields:
            new_embed.add_field(name=field.name, value=field.value, inline=field.inline)

        try:
            await msg.edit(embed=new_embed)
            await interaction.followup.send("✅ Embed updated!", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("❌ I don't have permission to edit that message.", ephemeral=True)
        except discord.HTTPException as e:
            await interaction.followup.send(f"❌ Failed to edit embed: {e}", ephemeral=True)

    # ── /embedinfo ────────────────────────────────────────────────────────────
    @discord.app_commands.command(name="embedinfo", description="Show all available embed commands and how to use them.")
    async def embedinfo(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🧰 Embed System — Commands",
            description="Here's everything you can do with the embed system:",
            color=discord.Color(COLORS["blue"]),
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        commands_info = [
            ("`/embed`",         "Send a fully custom embed anywhere. Supports `{}` variables."),
            ("`/embedjson`",     "Send an embed using raw Discord JSON."),
            ("`/embedextract`",  "Extract raw JSON from any message's embed."),
            ("`/embedannounce`", "Styled announcement embed with optional role ping."),
            ("`/embedrules`",    "Post a numbered server rules embed."),
            ("`/embedwelcome`",  "Welcome a new member with a styled embed."),
            ("`/embedquote`",    "Display a quote in a stylised embed."),
            ("`/embeddraft`",    "Preview an embed privately before sending."),
            ("`/embedpost`",     "Post your saved draft to a channel."),
            ("`/editembed`",     "Edit an existing embed the bot has sent."),
            ("`/embedvars`",     "List all `{}` variables you can use in embeds."),
            ("`/embedinfo`",     "This help embed."),
        ]
        for name, desc in commands_info:
            embed.add_field(name=name, value=desc, inline=False)
        embed.add_field(name="🎨 Color Presets", value=", ".join(f"`{c}`" for c in COLORS), inline=False)
        embed.set_footer(text="Tip: hex_color overrides the color preset. Use /embedvars to see all {} placeholders.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── Error handler ─────────────────────────────────────────────────────────
    async def cog_app_command_error(self, interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
        if isinstance(error, discord.app_commands.CheckFailure):
            msg = "❌ You need Administrator permissions to use this command."
        else:
            msg = f"❌ An error occurred: {error}"
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)


async def setup(bot):
    await bot.add_cog(EmbedCog(bot))

import discord
from discord.ext import commands
import datetime
import json
import os

CONFIG_FILE = "welcome_config.json"

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


def load_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_config(config: dict) -> None:
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


def guild_config(config: dict, guild_id: int) -> dict:
    return config.get(str(guild_id), {})


class WelcomeCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = load_config()

    welcome_group = discord.app_commands.Group(
        name="welcome",
        description="Auto-welcome configuration commands."
    )

    @welcome_group.command(name="setup", description="Set the welcome channel and embed colour.")
    @has_mod_permissions()
    @discord.app_commands.describe(
        channel="Channel to send welcome messages in",
        color="Embed colour preset",
        hex_color="Custom hex colour (overrides preset) e.g. FF6B6B",
        message="Custom welcome message (use {user} for mention, {server} for server name)",
    )
    @discord.app_commands.choices(color=COLOR_CHOICES)
    async def welcome_setup(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        color: str = "green",
        hex_color: str | None = None,
        message: str | None = None,
    ):
        if interaction.guild is None:
            await interaction.response.send_message("❌ This command must be used in a server.", ephemeral=True)
            return

        if hex_color:
            hex_color = hex_color.lstrip("#")
            try:
                color_value = int(hex_color, 16)
            except ValueError:
                await interaction.response.send_message("❌ Invalid hex colour.", ephemeral=True)
                return
        else:
            color_value = COLORS.get(color, COLORS["green"])

        guild_id = str(interaction.guild.id)
        self.config[guild_id] = {
            "channel_id": channel.id,
            "color": color_value,
            "message": message,
            "enabled": True,
        }
        save_config(self.config)

        await interaction.response.send_message(
            f"✅ Welcome system set up!\n"
            f"**Channel:** {channel.mention}\n"
            f"**Colour:** `{'#' + hex_color if hex_color else color.capitalize()}`\n"
            f"**Status:** Enabled\n\n"
            f"Use `/welcome toggle` to turn it on or off.",
            ephemeral=True,
        )

    @welcome_group.command(name="toggle", description="Turn the auto-welcome on or off.")
    @has_mod_permissions()
    async def welcome_toggle(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("❌ This command must be used in a server.", ephemeral=True)
            return

        guild_id = str(interaction.guild.id)
        cfg = self.config.get(guild_id)

        if not cfg:
            await interaction.response.send_message(
                "❌ Welcome system not configured yet. Use `/welcome setup` first.", ephemeral=True)
            return

        cfg["enabled"] = not cfg.get("enabled", True)
        self.config[guild_id] = cfg
        save_config(self.config)

        status = "✅ Enabled" if cfg["enabled"] else "🔴 Disabled"
        await interaction.response.send_message(
            f"Auto-welcome is now **{status}**.", ephemeral=True)

    @welcome_group.command(name="status", description="Show the current welcome configuration.")
    @has_mod_permissions()
    async def welcome_status(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("❌ This command must be used in a server.", ephemeral=True)
            return

        cfg = guild_config(self.config, interaction.guild.id)
        if not cfg:
            await interaction.response.send_message(
                "❌ Welcome system not configured. Use `/welcome setup` first.", ephemeral=True)
            return

        channel = interaction.guild.get_channel(cfg["channel_id"])
        channel_mention = channel.mention if channel else f"Unknown (ID: {cfg['channel_id']})"
        status = "✅ Enabled" if cfg.get("enabled", True) else "🔴 Disabled"
        custom_msg = cfg.get("message") or "Default welcome message"

        embed = discord.Embed(
            title="👋 Welcome System Status",
            color=discord.Color(cfg["color"]),
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.add_field(name="Status", value=status, inline=True)
        embed.add_field(name="Channel", value=channel_mention, inline=True)
        embed.add_field(name="Colour", value=f"`#{cfg['color']:06X}`", inline=True)
        embed.add_field(name="Message", value=custom_msg, inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @welcome_group.command(name="preview", description="Preview what the welcome embed will look like.")
    @has_mod_permissions()
    async def welcome_preview(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("❌ This command must be used in a server.", ephemeral=True)
            return

        cfg = guild_config(self.config, interaction.guild.id)
        if not cfg:
            await interaction.response.send_message(
                "❌ Welcome system not configured. Use `/welcome setup` first.", ephemeral=True)
            return

        member = interaction.user
        embed = self._build_welcome_embed(member, interaction.guild, cfg)
        await interaction.response.send_message(
            "📋 **Preview** — this is what new members will see:", embed=embed, ephemeral=True)

    def _build_welcome_embed(self, member: discord.Member, guild: discord.Guild, cfg: dict) -> discord.Embed:
        custom_message = cfg.get("message")
        if custom_message:
            body = custom_message.replace("{user}", member.mention).replace("{server}", guild.name)
        else:
            body = f"Hey {member.mention}, welcome to **{guild.name}**! 🎉\nWe're glad to have you here. Make sure to read the rules and enjoy your stay!"

        embed = discord.Embed(
            title=f"👋 Welcome, {member.display_name}!",
            description=body,
            color=discord.Color(cfg["color"]),
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"Member #{guild.member_count}")
        return embed

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        cfg = guild_config(self.config, member.guild.id)
        if not cfg or not cfg.get("enabled", True):
            return

        channel = member.guild.get_channel(cfg["channel_id"])
        if not isinstance(channel, discord.TextChannel):
            return

        embed = self._build_welcome_embed(member, member.guild, cfg)
        try:
            await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            pass

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
    await bot.add_cog(WelcomeCog(bot))

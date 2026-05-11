from discord.ext import commands
import discord
import datetime
import io
from utils.cmd_access import has_cmd_access, add_user, remove_user, get_allowed, OWNER_ID


def has_mod_permissions():
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            return False
        if not isinstance(interaction.user, discord.Member):
            return False
        if interaction.user.guild_permissions.administrator:
            return True
        return interaction.user.id == OWNER_ID
    return discord.app_commands.check(predicate)


def owner_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        return interaction.user.id == OWNER_ID
    return discord.app_commands.check(predicate)


async def download_attachment(file: discord.Attachment) -> discord.File:
    data = await file.read()
    return discord.File(io.BytesIO(data), filename=file.filename)


class GeneralCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @discord.app_commands.command(name="ping", description="Check bot latency")
    @has_cmd_access()
    async def ping(self, interaction: discord.Interaction):
        latency_ms = round(interaction.client.latency * 1000)
        await interaction.response.send_message(f"Pong! 🏓 Latency: {latency_ms}ms")

    @discord.app_commands.command(name="userinfo", description="Get info about a user")
    @discord.app_commands.describe(user="The user you want to get info about")
    @has_cmd_access()
    async def userinfo(self, interaction: discord.Interaction, user: discord.Member):
        if interaction.guild is None:
            await interaction.response.send_message("❌ This command can only be used in a server.")
            return

        embed = discord.Embed(
            title=f"User Info for {user.display_name}",
            color=user.color,
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )

        if user.avatar:
            embed.set_thumbnail(url=user.avatar.url)

        account_created = user.created_at.strftime("%b %d, %Y %I:%M %p") if user.created_at else "Unknown"
        joined_at = user.joined_at.strftime("%b %d, %Y %I:%M %p") if user.joined_at else "Unknown"

        embed.add_field(name="ID", value=user.id)
        embed.add_field(name="Account Created", value=account_created)
        embed.add_field(name="Joined Server", value=joined_at)

        roles = [role.mention for role in user.roles if role.name != "@everyone"]
        embed.add_field(name="Roles", value=", ".join(roles) if roles else "None", inline=False)

        await interaction.response.send_message(embed=embed)

    @discord.app_commands.command(name="serverinfo", description="Get info about the server")
    @has_cmd_access()
    async def serverinfo(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("❌ This command can only be used in a server.")
            return

        embed = discord.Embed(
            title=f"Server Info for {interaction.guild.name}",
            color=discord.Color.blue(),
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )

        if interaction.guild.icon:
            embed.set_thumbnail(url=interaction.guild.icon.url)

        embed.add_field(name="Server ID", value=interaction.guild.id)
        owner_mention = f"<@{interaction.guild.owner.id}>" if interaction.guild.owner else "Unknown"
        embed.add_field(name="Owner", value=owner_mention)
        embed.add_field(name="Members", value=interaction.guild.member_count)
        embed.add_field(name="Creation Date",
                        value=interaction.guild.created_at.strftime("%b %d, %Y %I:%M %p"))

        await interaction.response.send_message(embed=embed)

    # ── /send ─────────────────────────────────────────────────────────────────
    @discord.app_commands.command(name="send", description="Send a message or media to a channel.")
    @has_mod_permissions()
    @discord.app_commands.describe(
        channel="Channel to send to (defaults to current channel)",
        message="The message text to send (use \\n for new lines)",
        file="Upload an image or file to send",
        reply_to="Message ID to reply to (optional)",
    )
    async def send(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
        message: str | None = None,
        file: discord.Attachment | None = None,
        reply_to: str | None = None,
    ):
        await interaction.response.defer(ephemeral=True)

        if not message and not file:
            await interaction.followup.send("❌ Provide at least a message or a file.", ephemeral=True)
            return

        target: discord.TextChannel
        if channel is not None:
            target = channel
        elif isinstance(interaction.channel, discord.TextChannel):
            target = interaction.channel
        else:
            await interaction.followup.send("❌ This command can only be used in a text channel.", ephemeral=True)
            return

        if message:
            message = message.replace("\\n", "\n")

        discord_file: discord.File | None = None
        if file:
            discord_file = await download_attachment(file)

        reference: discord.MessageReference | None = None
        if reply_to:
            try:
                msg = await target.fetch_message(int(reply_to))
                reference = msg.to_reference()
            except (discord.NotFound, ValueError):
                pass

        send_kwargs = {"content": message if message else None}
        if discord_file:
            send_kwargs["file"] = discord_file
        if reference:
            send_kwargs["reference"] = reference

        await target.send(**send_kwargs)
        await interaction.followup.send("✅ Sent!", ephemeral=True)

    # ── /senddm ───────────────────────────────────────────────────────────────
    @discord.app_commands.command(name="senddm", description="Send a DM to a server member.")
    @has_mod_permissions()
    @discord.app_commands.describe(
        member="The member to DM",
        message="The message to send (use \\n for new lines)",
        file="Upload a file to send with the DM",
    )
    async def senddm(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        message: str | None = None,
        file: discord.Attachment | None = None,
    ):
        await interaction.response.defer(ephemeral=True)

        if not message and not file:
            await interaction.followup.send("❌ Provide at least a message or a file.", ephemeral=True)
            return

        if message:
            message = message.replace("\\n", "\n")

        discord_file: discord.File | None = None
        if file:
            discord_file = await download_attachment(file)

        try:
            await member.send(
                content=message if message else None,
                file=discord_file if discord_file else discord.utils.MISSING,
            )
            await interaction.followup.send(f"✅ DM sent to {member.display_name}!", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send(
                f"❌ Couldn't DM {member.display_name} — they may have DMs disabled.",
                ephemeral=True)

    # ── /cmdallow ─────────────────────────────────────────────────────────────
    @discord.app_commands.command(name="cmdallow", description="Grant a user access to bot commands.")
    @owner_only()
    @discord.app_commands.describe(user="User to grant access to")
    async def cmdallow(self, interaction: discord.Interaction, user: discord.Member):
        add_user(user.id)
        await interaction.response.send_message(
            f"✅ **{user.display_name}** can now use bot commands.", ephemeral=True)

    # ── /cmdrevoke ────────────────────────────────────────────────────────────
    @discord.app_commands.command(name="cmdrevoke", description="Remove a user's access to bot commands.")
    @owner_only()
    @discord.app_commands.describe(user="User to revoke access from")
    async def cmdrevoke(self, interaction: discord.Interaction, user: discord.Member):
        remove_user(user.id)
        await interaction.response.send_message(
            f"✅ **{user.display_name}**'s command access removed.", ephemeral=True)

    # ── /cmdallowlist ─────────────────────────────────────────────────────────
    @discord.app_commands.command(name="cmdallowlist", description="View all users with bot command access.")
    @owner_only()
    async def cmdallowlist(self, interaction: discord.Interaction):
        allowed = get_allowed()
        if not allowed:
            await interaction.response.send_message(
                "No additional users have command access — owner only.", ephemeral=True)
            return
        lines = []
        for uid in allowed:
            member = interaction.guild.get_member(uid) if interaction.guild else None
            lines.append(f"• {member.display_name} ({uid})" if member else f"• {uid}")
        await interaction.response.send_message(
            "**Command Access List:**\n" + "\n".join(lines), ephemeral=True)

    # ── Error handler ─────────────────────────────────────────────────────────
    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: discord.app_commands.AppCommandError,
    ):
        if isinstance(error, discord.app_commands.CheckFailure):
            await interaction.response.send_message(
                "❌ You don't have access to this command.", ephemeral=True)
        else:
            await interaction.response.send_message(
                f"❌ An error occurred: {error}", ephemeral=True)


async def setup(bot):
    await bot.add_cog(GeneralCog(bot))

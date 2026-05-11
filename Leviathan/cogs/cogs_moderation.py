import discord
from discord.ext import commands
import datetime
from utils.warnings_manager import add_warning, get_warnings, clear_warnings


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


def parse_duration(duration: str) -> datetime.timedelta | None:
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    duration = duration.strip().lower()
    if len(duration) < 2:
        return None
    unit = duration[-1]
    if unit not in units:
        return None
    try:
        value = int(duration[:-1])
    except ValueError:
        return None
    if value < 1:
        return None
    return datetime.timedelta(seconds=value * units[unit])


# Auto-punishment thresholds
WARN_TIMEOUT_AT = 3    # timeout for 1 hour at this many warnings
WARN_BAN_AT = 5        # ban at this many warnings


class ModerationCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── /kick ─────────────────────────────────────────────────────────────────
    @discord.app_commands.command(name="kick", description="Kicks a member from the server.")
    @has_mod_permissions()
    @discord.app_commands.describe(member="Member to kick", reason="Reason for kick")
    async def kick(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided."):
        if interaction.guild is None:
            await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
            return
        if member == interaction.user:
            await interaction.response.send_message("❌ You cannot kick yourself.", ephemeral=True)
            return
        if isinstance(interaction.user, discord.Member) and interaction.user.top_role.position <= member.top_role.position:
            await interaction.response.send_message(
                f"❌ You cannot kick **{member.display_name}** as their top role is the same or higher than yours.", ephemeral=True)
            return
        try:
            await interaction.response.defer(ephemeral=True)
            await member.kick(reason=reason)
            await interaction.followup.send(f"✅ Kicked **{member.display_name}** for: {reason}")
        except discord.Forbidden:
            await interaction.followup.send("❌ I do not have permission to kick this member.")
        except discord.HTTPException as e:
            await interaction.followup.send(f"❌ An error occurred: {e}")

    # ── /ban ──────────────────────────────────────────────────────────────────
    @discord.app_commands.command(name="ban", description="Bans a member from the server.")
    @has_mod_permissions()
    @discord.app_commands.describe(member="Member to ban", reason="Reason for ban")
    async def ban(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided."):
        if interaction.guild is None:
            await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
            return
        if member == interaction.user:
            await interaction.response.send_message("❌ You cannot ban yourself.", ephemeral=True)
            return
        if isinstance(interaction.user, discord.Member) and interaction.user.top_role.position <= member.top_role.position:
            await interaction.response.send_message(
                f"❌ You cannot ban **{member.display_name}** as their top role is the same or higher than yours.", ephemeral=True)
            return
        try:
            await interaction.response.defer(ephemeral=True)
            await member.ban(reason=reason)
            await interaction.followup.send(f"✅ Banned **{member.display_name}** for: {reason}")
        except discord.Forbidden:
            await interaction.followup.send("❌ I do not have permission to ban this member.")
        except discord.HTTPException as e:
            await interaction.followup.send(f"❌ An error occurred: {e}")

    # ── /timeout ──────────────────────────────────────────────────────────────
    @discord.app_commands.command(name="timeout", description="Temporarily mute a member.")
    @has_mod_permissions()
    @discord.app_commands.describe(
        member="Member to timeout",
        duration="Duration e.g. 10m, 1h, 2d (max 28d)",
        reason="Reason for timeout",
    )
    async def timeout(self, interaction: discord.Interaction, member: discord.Member, duration: str, reason: str = "No reason provided."):
        if interaction.guild is None:
            await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
            return
        delta = parse_duration(duration)
        if delta is None:
            await interaction.response.send_message("❌ Invalid duration. Use formats like `10m`, `1h`, `2d`.", ephemeral=True)
            return
        if delta.total_seconds() > 28 * 86400:
            await interaction.response.send_message("❌ Maximum timeout duration is 28 days.", ephemeral=True)
            return
        if isinstance(interaction.user, discord.Member) and interaction.user.top_role.position <= member.top_role.position:
            await interaction.response.send_message(
                f"❌ You cannot timeout **{member.display_name}** as their role is the same or higher than yours.", ephemeral=True)
            return
        try:
            await interaction.response.defer(ephemeral=True)
            until = discord.utils.utcnow() + delta
            await member.timeout(until, reason=reason)
            await interaction.followup.send(f"✅ **{member.display_name}** has been timed out for `{duration}`. Reason: {reason}")
        except discord.Forbidden:
            await interaction.followup.send("❌ I do not have permission to timeout this member.")
        except discord.HTTPException as e:
            await interaction.followup.send(f"❌ An error occurred: {e}")

    # ── /untimeout ────────────────────────────────────────────────────────────
    @discord.app_commands.command(name="untimeout", description="Remove a timeout from a member.")
    @has_mod_permissions()
    @discord.app_commands.describe(member="Member to remove timeout from")
    async def untimeout(self, interaction: discord.Interaction, member: discord.Member):
        try:
            await interaction.response.defer(ephemeral=True)
            await member.timeout(None)
            await interaction.followup.send(f"✅ Timeout removed from **{member.display_name}**.")
        except discord.Forbidden:
            await interaction.followup.send("❌ I do not have permission to remove this timeout.")
        except discord.HTTPException as e:
            await interaction.followup.send(f"❌ An error occurred: {e}")

    # ── /warn ─────────────────────────────────────────────────────────────────
    @discord.app_commands.command(name="warn", description="Warn a member. Auto-punishes at 3 and 5 warnings.")
    @has_mod_permissions()
    @discord.app_commands.describe(member="Member to warn", reason="Reason for the warning")
    async def warn(self, interaction: discord.Interaction, member: discord.Member, reason: str):
        if interaction.guild is None:
            await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
            return
        if member.bot:
            await interaction.response.send_message("❌ You cannot warn a bot.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        count = add_warning(interaction.guild.id, member.id, interaction.user.id, reason)

        msg = f"⚠️ **{member.display_name}** has been warned. Reason: {reason}\nThey now have **{count}** warning(s)."

        # Auto-punishments
        if count >= WARN_BAN_AT:
            try:
                await member.ban(reason=f"Auto-ban: reached {WARN_BAN_AT} warnings.")
                msg += f"\n🔨 **Auto-banned** — reached {WARN_BAN_AT} warnings."
            except (discord.Forbidden, discord.HTTPException):
                msg += f"\n⚠️ Could not auto-ban (missing permissions)."
        elif count >= WARN_TIMEOUT_AT:
            try:
                until = discord.utils.utcnow() + datetime.timedelta(hours=1)
                await member.timeout(until, reason=f"Auto-timeout: reached {WARN_TIMEOUT_AT} warnings.")
                msg += f"\n⏱️ **Auto-timed out for 1 hour** — reached {WARN_TIMEOUT_AT} warnings."
            except (discord.Forbidden, discord.HTTPException):
                msg += f"\n⚠️ Could not auto-timeout (missing permissions)."

        await interaction.followup.send(msg)

    # ── /warnings ─────────────────────────────────────────────────────────────
    @discord.app_commands.command(name="warnings", description="View a member's warning history.")
    @has_mod_permissions()
    @discord.app_commands.describe(member="Member to check warnings for")
    async def warnings(self, interaction: discord.Interaction, member: discord.Member):
        if interaction.guild is None:
            await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
            return

        warns = get_warnings(interaction.guild.id, member.id)
        if not warns:
            await interaction.response.send_message(f"✅ **{member.display_name}** has no warnings.", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"⚠️ Warnings — {member.display_name}",
            color=discord.Color(0xE67E22),
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.description = f"**{len(warns)}** warning(s) total."

        for i, w in enumerate(warns, 1):
            mod = interaction.guild.get_member(w["mod_id"])
            mod_name = mod.display_name if mod else f"Unknown (ID: {w['mod_id']})"
            ts = w["timestamp"][:10]
            embed.add_field(
                name=f"Warning #{i} — {ts}",
                value=f"**Reason:** {w['reason']}\n**By:** {mod_name}",
                inline=False,
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /clearwarnings ────────────────────────────────────────────────────────
    @discord.app_commands.command(name="clearwarnings", description="Clear all warnings for a member.")
    @has_mod_permissions()
    @discord.app_commands.describe(member="Member whose warnings to clear")
    async def clearwarnings(self, interaction: discord.Interaction, member: discord.Member):
        if interaction.guild is None:
            await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
            return
        count = clear_warnings(interaction.guild.id, member.id)
        if count == 0:
            await interaction.response.send_message(f"**{member.display_name}** had no warnings to clear.", ephemeral=True)
        else:
            await interaction.response.send_message(
                f"✅ Cleared **{count}** warning(s) from **{member.display_name}**.", ephemeral=True)

    # ── /slowmode ─────────────────────────────────────────────────────────────
    @discord.app_commands.command(name="slowmode", description="Set slowmode delay for the current channel.")
    @has_mod_permissions()
    @discord.app_commands.describe(seconds="Delay in seconds (0 to disable, max 21600)")
    async def slowmode(self, interaction: discord.Interaction, seconds: int):
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("❌ This command must be used in a text channel.", ephemeral=True)
            return
        if seconds < 0 or seconds > 21600:
            await interaction.response.send_message("❌ Slowmode must be between 0 and 21600 seconds.", ephemeral=True)
            return
        try:
            await interaction.channel.edit(slowmode_delay=seconds)
            if seconds == 0:
                await interaction.response.send_message("✅ Slowmode disabled.", ephemeral=True)
            else:
                await interaction.response.send_message(f"✅ Slowmode set to **{seconds}s**.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ I don't have permission to change slowmode.", ephemeral=True)

    # ── /lock ─────────────────────────────────────────────────────────────────
    @discord.app_commands.command(name="lock", description="Lock a channel so members cannot send messages.")
    @has_mod_permissions()
    @discord.app_commands.describe(channel="Channel to lock (defaults to current channel)")
    async def lock(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None):
        if interaction.guild is None:
            await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
            return
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message("❌ Invalid channel.", ephemeral=True)
            return
        try:
            await target.set_permissions(interaction.guild.default_role, send_messages=False)
            await interaction.response.send_message(f"🔒 {target.mention} has been locked.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ I don't have permission to lock that channel.", ephemeral=True)

    # ── /unlock ───────────────────────────────────────────────────────────────
    @discord.app_commands.command(name="unlock", description="Unlock a channel so members can send messages again.")
    @has_mod_permissions()
    @discord.app_commands.describe(channel="Channel to unlock (defaults to current channel)")
    async def unlock(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None):
        if interaction.guild is None:
            await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
            return
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message("❌ Invalid channel.", ephemeral=True)
            return
        try:
            await target.set_permissions(interaction.guild.default_role, send_messages=None)
            await interaction.response.send_message(f"🔓 {target.mention} has been unlocked.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ I don't have permission to unlock that channel.", ephemeral=True)

    # ── /clear ────────────────────────────────────────────────────────────────
    @discord.app_commands.command(name="clear", description="Deletes a specified number of messages.")
    @has_mod_permissions()
    @discord.app_commands.describe(count="Number of messages to delete (max 100)")
    async def clear(self, interaction: discord.Interaction, count: int):
        if count <= 0:
            await interaction.response.send_message("❌ Please provide a number greater than 0.", ephemeral=True)
            return
        if count > 100:
            await interaction.response.send_message("❌ You can only clear up to 100 messages at a time.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        if isinstance(interaction.channel, (discord.TextChannel, discord.Thread)):
            try:
                deleted = await interaction.channel.purge(limit=count + 1)
                await interaction.followup.send(f"✅ Cleared **{len(deleted) - 1}** messages.")
            except discord.Forbidden:
                await interaction.followup.send("❌ I do not have the 'Manage Messages' permission.")
            except discord.HTTPException as e:
                await interaction.followup.send(f"❌ An error occurred: {e}")
        else:
            await interaction.followup.send("❌ Cannot clear messages in this channel type.")

    # ── /sync ─────────────────────────────────────────────────────────────────
    @discord.app_commands.command(name="sync", description="Syncs all slash commands.")
    @has_mod_permissions()
    async def sync(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await self.bot.tree.sync(guild=interaction.guild)
            await interaction.followup.send("✅ Commands have been synced!")
        except discord.Forbidden:
            await interaction.followup.send("❌ I do not have permission to sync commands.")
        except discord.HTTPException as e:
            await interaction.followup.send(f"❌ An error occurred: {e}")

    # ── /addrole ──────────────────────────────────────────────────────────────
    @discord.app_commands.command(name="addrole", description="Add a role to a member.")
    @has_mod_permissions()
    @discord.app_commands.describe(member="Member to give the role to", role="Role to add", reason="Reason")
    async def addrole(self, interaction: discord.Interaction, member: discord.Member, role: discord.Role, reason: str = "No reason provided."):
        if interaction.guild is None:
            await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
            return
        if member == interaction.user:
            await interaction.response.send_message("❌ You cannot add a role to yourself.", ephemeral=True)
            return
        if isinstance(interaction.user, discord.Member) and interaction.user.top_role.position <= role.position:
            await interaction.response.send_message(f"❌ You cannot add **{role.name}** as it is the same or higher than your top role.", ephemeral=True)
            return
        if role in member.roles:
            await interaction.response.send_message(f"❌ **{member.display_name}** already has **{role.name}**.", ephemeral=True)
            return
        try:
            await interaction.response.defer(ephemeral=True)
            await member.add_roles(role, reason=reason)
            await interaction.followup.send(f"✅ Added **{role.name}** to **{member.display_name}**.")
        except discord.Forbidden:
            await interaction.followup.send("❌ I do not have permission to add this role.")
        except discord.HTTPException as e:
            await interaction.followup.send(f"❌ An error occurred: {e}")

    # ── /removerole ───────────────────────────────────────────────────────────
    @discord.app_commands.command(name="removerole", description="Remove a role from a member.")
    @has_mod_permissions()
    @discord.app_commands.describe(member="Member to remove the role from", role="Role to remove", reason="Reason")
    async def removerole(self, interaction: discord.Interaction, member: discord.Member, role: discord.Role, reason: str = "No reason provided."):
        if interaction.guild is None:
            await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
            return
        if member == interaction.user:
            await interaction.response.send_message("❌ You cannot remove a role from yourself.", ephemeral=True)
            return
        if isinstance(interaction.user, discord.Member) and interaction.user.top_role.position <= role.position:
            await interaction.response.send_message(f"❌ You cannot remove **{role.name}** as it is the same or higher than your top role.", ephemeral=True)
            return
        if role not in member.roles:
            await interaction.response.send_message(f"❌ **{member.display_name}** doesn't have **{role.name}**.", ephemeral=True)
            return
        try:
            await interaction.response.defer(ephemeral=True)
            await member.remove_roles(role, reason=reason)
            await interaction.followup.send(f"✅ Removed **{role.name}** from **{member.display_name}**.")
        except discord.Forbidden:
            await interaction.followup.send("❌ I do not have permission to remove this role.")
        except discord.HTTPException as e:
            await interaction.followup.send(f"❌ An error occurred: {e}")

    # ── /purgerole ────────────────────────────────────────────────────────────
    @discord.app_commands.command(name="purgerole", description="Remove a role from all members.")
    @has_mod_permissions()
    @discord.app_commands.describe(role="Role to purge", exclude="Comma-separated member IDs to exclude", confirm="Set True to execute")
    async def purgerole(self, interaction: discord.Interaction, role: discord.Role, exclude: str = "", confirm: bool = False):
        if interaction.guild is None:
            await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
            return
        if isinstance(interaction.user, discord.Member) and interaction.user.top_role.position <= role.position:
            await interaction.response.send_message(f"❌ You cannot purge **{role.name}** as it is higher than your top role.", ephemeral=True)
            return

        excluded: list[discord.Member] = []
        if exclude:
            excluded = [m for m in interaction.guild.members if m.id in [int(i) for i in exclude.split(",") if i.strip().isdigit()]]

        targets = [m for m in interaction.guild.members if role in m.roles and m not in excluded]

        if not confirm:
            names = ", ".join(m.display_name for m in targets) if targets else "No members"
            await interaction.response.send_message(
                f"⚠️ This will remove **{role.name}** from **{len(targets)}** members.\n"
                f"Affected: {names}\nRun again with `confirm=True` to proceed.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        removed, failed = [], []
        for m in targets:
            try:
                await m.remove_roles(role)
                removed.append(m)
            except (discord.Forbidden, discord.HTTPException):
                failed.append(m)

        msg = f"✅ Removed **{role.name}** from {len(removed)} member(s)."
        if failed:
            msg += f"\nFailed for: {', '.join(m.display_name for m in failed)}"
        await interaction.followup.send(msg, ephemeral=True)

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
    await bot.add_cog(ModerationCog(bot))

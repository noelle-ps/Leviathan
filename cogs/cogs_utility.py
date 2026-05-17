import discord
from discord.ext import commands
import datetime
import os
import aiohttp
from utils.cmd_access import has_cmd_access

RAILWAY_API = "https://backboard.railway.app/graphql/v2"


class UtilityCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @discord.app_commands.command(name="avatar", description="Get a member's full-size avatar.")
    @discord.app_commands.describe(member="Member whose avatar to show (defaults to you)")
    @has_cmd_access()
    async def avatar(self, interaction: discord.Interaction, member: discord.Member | None = None):
        target = member or interaction.user
        embed = discord.Embed(
            title=f"🖼️ {target.display_name}'s Avatar",
            color=target.color if isinstance(target, discord.Member) else discord.Color.blurple(),
        )
        embed.set_image(url=target.display_avatar.url)
        embed.add_field(name="Download", value=f"[Click here]({target.display_avatar.url})")
        await interaction.response.send_message(embed=embed)

    @discord.app_commands.command(name="roleinfo", description="Get info about a role.")
    @discord.app_commands.describe(role="The role to inspect")
    @has_cmd_access()
    async def roleinfo(self, interaction: discord.Interaction, role: discord.Role):
        if interaction.guild is None:
            await interaction.response.send_message("❌ This command must be used in a server.", ephemeral=True)
            return

        perms = [p.replace("_", " ").title() for p, v in role.permissions if v]
        perms_str = ", ".join(perms[:10]) if perms else "None"
        if len(perms) > 10:
            perms_str += f" (+{len(perms) - 10} more)"

        embed = discord.Embed(
            title=f"🏷️ Role: {role.name}",
            color=role.color,
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.add_field(name="ID", value=str(role.id), inline=True)
        embed.add_field(name="Members", value=str(len(role.members)), inline=True)
        embed.add_field(name="Colour", value=str(role.color), inline=True)
        embed.add_field(name="Mentionable", value="Yes" if role.mentionable else "No", inline=True)
        embed.add_field(name="Hoisted", value="Yes" if role.hoist else "No", inline=True)
        embed.add_field(name="Position", value=str(role.position), inline=True)
        embed.add_field(name="Key Permissions", value=perms_str, inline=False)
        embed.set_footer(text=f"Created")
        embed.timestamp = role.created_at
        await interaction.response.send_message(embed=embed)

    @discord.app_commands.command(name="botinfo", description="Show info about Leviathan.")
    @has_cmd_access()
    async def botinfo(self, interaction: discord.Interaction):
        bot = self.bot
        uptime = datetime.datetime.now(datetime.timezone.utc) - bot.start_time
        hours, remainder = divmod(int(uptime.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        uptime_str = f"{hours}h {minutes}m {seconds}s"

        embed = discord.Embed(
            title="🤖 Leviathan — Bot Info",
            color=discord.Color(0x3498DB),
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.set_thumbnail(url=bot.user.display_avatar.url if bot.user else discord.utils.MISSING)
        embed.add_field(name="Name", value=str(bot.user), inline=True)
        embed.add_field(name="Servers", value=str(len(bot.guilds)), inline=True)
        embed.add_field(name="Ping", value=f"{round(bot.latency * 1000)}ms", inline=True)
        embed.add_field(name="Uptime", value=uptime_str, inline=True)
        embed.add_field(name="Commands", value=str(len(bot.tree.get_commands())), inline=True)
        await interaction.response.send_message(embed=embed)

    @discord.app_commands.command(name="uptime", description="Check how long the bot has been running.")
    @has_cmd_access()
    async def uptime(self, interaction: discord.Interaction):
        uptime = datetime.datetime.now(datetime.timezone.utc) - self.bot.start_time
        hours, remainder = divmod(int(uptime.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        days, hours = divmod(hours, 24)
        parts = []
        if days:
            parts.append(f"{days}d")
        if hours:
            parts.append(f"{hours}h")
        parts.append(f"{minutes}m {seconds}s")
        await interaction.response.send_message(f"⏱️ Leviathan has been online for **{' '.join(parts)}**.")

    # ── /railway ──────────────────────────────────────────────────────────────
    @discord.app_commands.command(name="railway", description="Check Railway hosting usage, credits, and billing info.")
    @has_mod_permissions()
    async def railway(self, interaction: discord.Interaction):
        token = os.getenv("RAILWAY_TOKEN")
        if not token:
            await interaction.response.send_message(
                "❌ `RAILWAY_TOKEN` secret is not set. Add it in Replit Secrets to use this command.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        query = """
        query {
          me {
            name
            email
            currentTeam {
              id
            }
          }
          subscriptions {
            edges {
              node {
                customerId
                status
                couponId
              }
            }
          }
        }
        """

        usage_query = """
        query UsageForProject($projectId: String!, $measurements: [MetricMeasurement!]!, $startDate: DateTime!, $endDate: DateTime!) {
          usageForProject(
            projectId: $projectId
            measurements: $measurements
            startDate: $startDate
            endDate: $endDate
            groupBy: []
            sampleRateSeconds: 86400
          ) {
            measurement
            values {
              value
            }
          }
        }
        """

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    RAILWAY_API,
                    json={"query": query},
                    headers=headers,
                ) as resp:
                    data = await resp.json()

                now = datetime.datetime.now(datetime.timezone.utc)
                period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

                me = data.get("data", {}).get("me", {})
                name = me.get("name") or "Unknown"
                email = me.get("email") or "Unknown"

                embed = discord.Embed(
                    title="🚂 Railway Hosting Info",
                    color=discord.Color(0x0B0D0E),
                    timestamp=now,
                )
                embed.add_field(name="Account", value=name, inline=True)
                embed.add_field(name="Email", value=email, inline=True)
                embed.add_field(name="\u200b", value="\u200b", inline=True)

                errors = data.get("errors")
                if errors:
                    embed.add_field(
                        name="⚠️ API Error",
                        value=f"```{errors[0].get('message', 'Unknown error')}```",
                        inline=False,
                    )
                else:
                    embed.add_field(
                        name="📅 Billing Period",
                        value=f"{period_start.strftime('%b %d')} → {now.strftime('%b %d, %Y')}",
                        inline=False,
                    )
                    embed.add_field(
                        name="ℹ️ Note",
                        value="For detailed credit/usage breakdown, visit [railway.app/account/billing](https://railway.app/account/billing)",
                        inline=False,
                    )

                embed.set_footer(text="Railway • Data fetched live")
                await interaction.followup.send(embed=embed, ephemeral=True)

        except aiohttp.ClientError as e:
            await interaction.followup.send(f"❌ Failed to reach Railway API: `{e}`", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Unexpected error: `{e}`", ephemeral=True)

    async def cog_app_command_error(self, interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
        if isinstance(error, discord.app_commands.CheckFailure):
            await interaction.response.send_message("❌ You don't have access to this command.", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ {error}", ephemeral=True)


async def setup(bot):
    await bot.add_cog(UtilityCog(bot))

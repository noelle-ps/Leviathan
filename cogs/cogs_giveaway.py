import discord
from discord.ext import commands
import asyncio
import datetime
import json
import os
import random
import logging

logger = logging.getLogger(__name__)

GIVEAWAYS_FILE = "data/giveaways.json"
OWNER_ID = 1136231768534569090


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


def load_giveaways() -> dict:
    if not os.path.exists(GIVEAWAYS_FILE):
        return {}
    try:
        with open(GIVEAWAYS_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_giveaways(data: dict):
    os.makedirs("data", exist_ok=True)
    with open(GIVEAWAYS_FILE, "w") as f:
        json.dump(data, f, indent=2)


class GiveawayCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.active_tasks: dict[str, asyncio.Task] = {}

    async def cog_load(self):
        giveaways = load_giveaways()
        now = datetime.datetime.now(datetime.timezone.utc).timestamp()
        for gid, data in list(giveaways.items()):
            if data.get("ended"):
                continue
            delay = max(0.0, data["end_time"] - now)
            self.active_tasks[gid] = asyncio.create_task(
                self._end_giveaway(gid, delay=delay)
            )

    # ── Embed builders ─────────────────────────────────────────────────────────

    def _build_active_embed(self, data: dict) -> discord.Embed:
        end_time = int(data["end_time"])
        prize_lines = [
            f"**{i}. {p['name']}** — {p['description']}  *(×{p['quantity']})*"
            for i, p in enumerate(data["prizes"], 1)
        ]
        embed = discord.Embed(
            title=f"🎉  {data['title']}",
            description="\n".join(prize_lines),
            color=discord.Color.gold(),
        )
        embed.add_field(
            name="⏰ Ends",
            value=f"<t:{end_time}:R>  •  <t:{end_time}:f>",
            inline=False,
        )
        embed.add_field(
            name="🏆 Total winners",
            value=str(sum(p["quantity"] for p in data["prizes"])),
            inline=True,
        )
        if data.get("required_role"):
            embed.add_field(
                name="🔒 Required role",
                value=f"<@&{data['required_role']}>",
                inline=True,
            )
        embed.set_footer(
            text=f"React with 🎉 to enter  •  ID: {data['giveaway_id']}"
        )
        embed.timestamp = datetime.datetime.fromtimestamp(
            end_time, tz=datetime.timezone.utc
        )
        return embed

    def _build_ended_embed(self, data: dict) -> discord.Embed:
        end_time = int(data["end_time"])
        prize_lines = [
            f"**{i}. {p['name']}** — {p['description']}  *(×{p['quantity']})*"
            for i, p in enumerate(data["prizes"], 1)
        ]
        embed = discord.Embed(
            title=f"🎊  {data['title']}  — Ended",
            description="\n".join(prize_lines),
            color=discord.Color.greyple(),
        )
        embed.add_field(name="Ended", value=f"<t:{end_time}:f>", inline=False)
        embed.set_footer(text=f"Giveaway ended  •  ID: {data['giveaway_id']}")
        embed.timestamp = datetime.datetime.fromtimestamp(
            end_time, tz=datetime.timezone.utc
        )
        return embed

    def _build_winners_embed(
        self, data: dict, winners_by_prize: dict[str, list[int]]
    ) -> discord.Embed:
        embed = discord.Embed(
            title=f"🏆  Giveaway Results — {data['title']}",
            color=discord.Color.gold(),
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        any_winners = False
        for prize in data["prizes"]:
            winners = winners_by_prize.get(prize["name"], [])
            if winners:
                any_winners = True
                value = "\n".join(f"<@{uid}>" for uid in winners)
            else:
                value = "*No eligible entries*"
            embed.add_field(
                name=f"🏆 {prize['name']} — {prize['description']}",
                value=value,
                inline=False,
            )
        if not any_winners:
            embed.description = "Nobody entered the giveaway."
        embed.set_footer(text=f"Giveaway ID: {data['giveaway_id']}")
        return embed

    def _build_reroll_embed(
        self, data: dict, prize: dict, new_winners: list[int]
    ) -> discord.Embed:
        embed = discord.Embed(
            title=f"🔁  Reroll — {data['title']}",
            color=discord.Color.blurple(),
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        if new_winners:
            value = "\n".join(f"<@{uid}>" for uid in new_winners)
        else:
            value = "*No eligible entries*"
        embed.add_field(
            name=f"New winners for {prize['name']} — {prize['description']}",
            value=value,
            inline=False,
        )
        embed.set_footer(text=f"Giveaway ID: {data['giveaway_id']}")
        return embed

    # ── Core draw logic ────────────────────────────────────────────────────────

    async def _collect_entrants(self, data: dict) -> set[int]:
        guild = self.bot.get_guild(data["guild_id"])
        channel = guild.get_channel(data["channel_id"]) if guild else None
        required_role_id = data.get("required_role")
        entrants: set[int] = set()
        if not channel:
            return entrants
        try:
            message = await channel.fetch_message(data["message_id"])
            for reaction in message.reactions:
                if str(reaction.emoji) == "🎉":
                    async for user in reaction.users():
                        if user.bot:
                            continue
                        member = guild.get_member(user.id) if guild else None
                        if not member:
                            continue
                        if required_role_id and not any(
                            r.id == required_role_id for r in member.roles
                        ):
                            continue
                        entrants.add(user.id)
        except (discord.NotFound, discord.HTTPException):
            pass
        return entrants

    async def _end_giveaway(self, giveaway_id: str, delay: float):
        if delay > 0:
            await asyncio.sleep(delay)

        giveaways = load_giveaways()
        data = giveaways.get(giveaway_id)
        if not data or data.get("ended"):
            return

        guild = self.bot.get_guild(data["guild_id"])
        channel = guild.get_channel(data["channel_id"]) if guild else None

        entrants = await self._collect_entrants(data)
        pool = list(entrants)
        random.shuffle(pool)
        used: set[int] = set()
        winners_by_prize: dict[str, list[int]] = {}
        for prize in data["prizes"]:
            available = [uid for uid in pool if uid not in used]
            qty = min(prize["quantity"], len(available))
            chosen = random.sample(available, qty) if qty > 0 else []
            winners_by_prize[prize["name"]] = chosen
            used.update(chosen)

        if channel:
            try:
                message = await channel.fetch_message(data["message_id"])
                await message.edit(embed=self._build_ended_embed(data))
                try:
                    await message.clear_reactions()
                except discord.Forbidden:
                    pass
            except (discord.NotFound, discord.HTTPException):
                pass

            all_ids = [uid for w in winners_by_prize.values() for uid in w]
            mention_str = " ".join(f"<@{uid}>" for uid in all_ids)
            content = f"🎊 **Giveaway ended!**" + (f"  {mention_str}" if mention_str else "")
            await channel.send(
                content=content,
                embed=self._build_winners_embed(data, winners_by_prize),
            )

        data["ended"] = True
        data["winners"] = {k: [str(v) for v in vals] for k, vals in winners_by_prize.items()}
        giveaways[giveaway_id] = data
        save_giveaways(giveaways)
        self.active_tasks.pop(giveaway_id, None)

    # ── Slash commands ─────────────────────────────────────────────────────────

    giveaway = discord.app_commands.Group(
        name="giveaway", description="Manage giveaways"
    )

    @giveaway.command(name="start", description="Start a new multi-prize giveaway")
    @discord.app_commands.check(
        lambda interaction: (
            interaction.guild is not None
            and isinstance(interaction.user, discord.Member)
            and (
                interaction.user.guild_permissions.administrator
                or interaction.user.id == OWNER_ID
            )
        )
    )
    @discord.app_commands.describe(
        title="Name of the giveaway",
        duration="How long it runs (e.g. 30m, 2h, 1d)",
        prize1_name="First prize tier name (e.g. Gold)",
        prize1_description="What prize 1 winners receive",
        prize1_quantity="How many prize 1 winners (default 1)",
        prize2_name="Second prize tier name (e.g. Silver)",
        prize2_description="What prize 2 winners receive",
        prize2_quantity="How many prize 2 winners (default 1)",
        prize3_name="Third prize tier name (e.g. Bronze)",
        prize3_description="What prize 3 winners receive",
        prize3_quantity="How many prize 3 winners (default 1)",
        channel="Channel to post in (defaults to current channel)",
        required_role="Role required to enter (optional)",
    )
    async def giveaway_start(
        self,
        interaction: discord.Interaction,
        title: str,
        duration: str,
        prize1_name: str,
        prize1_description: str,
        prize1_quantity: int = 1,
        prize2_name: str | None = None,
        prize2_description: str | None = None,
        prize2_quantity: int = 1,
        prize3_name: str | None = None,
        prize3_description: str | None = None,
        prize3_quantity: int = 1,
        channel: discord.TextChannel | None = None,
        required_role: discord.Role | None = None,
    ):
        await interaction.response.defer(ephemeral=True)

        delta = parse_duration(duration)
        if delta is None:
            await interaction.followup.send(
                "❌ Invalid duration. Use formats like `30s`, `10m`, `2h`, `1d`.",
                ephemeral=True,
            )
            return

        target: discord.TextChannel
        if channel is not None:
            target = channel
        elif isinstance(interaction.channel, discord.TextChannel):
            target = interaction.channel
        else:
            await interaction.followup.send(
                "❌ Use this command in a text channel.", ephemeral=True
            )
            return

        prizes = [
            {
                "name": prize1_name,
                "description": prize1_description,
                "quantity": max(1, prize1_quantity),
            }
        ]
        if prize2_name and prize2_description:
            prizes.append(
                {
                    "name": prize2_name,
                    "description": prize2_description,
                    "quantity": max(1, prize2_quantity),
                }
            )
        if prize3_name and prize3_description:
            prizes.append(
                {
                    "name": prize3_name,
                    "description": prize3_description,
                    "quantity": max(1, prize3_quantity),
                }
            )

        end_time = (datetime.datetime.now(datetime.timezone.utc) + delta).timestamp()
        data: dict = {
            "giveaway_id": "",
            "guild_id": interaction.guild.id,
            "channel_id": target.id,
            "message_id": 0,
            "title": title,
            "prizes": prizes,
            "end_time": end_time,
            "required_role": required_role.id if required_role else None,
            "hosted_by": interaction.user.id,
            "hosted_by_name": str(interaction.user),
            "ended": False,
            "winners": {},
        }

        message = await target.send(embed=self._build_active_embed(data))
        await message.add_reaction("🎉")

        giveaway_id = str(message.id)
        data["giveaway_id"] = giveaway_id
        data["message_id"] = message.id
        await message.edit(embed=self._build_active_embed(data))

        giveaways = load_giveaways()
        giveaways[giveaway_id] = data
        save_giveaways(giveaways)

        self.active_tasks[giveaway_id] = asyncio.create_task(
            self._end_giveaway(giveaway_id, delay=delta.total_seconds())
        )

        await interaction.followup.send(
            f"✅ Giveaway **{title}** started in {target.mention}!", ephemeral=True
        )

    @giveaway.command(name="end", description="End a giveaway early and draw winners now")
    @discord.app_commands.check(
        lambda interaction: (
            interaction.guild is not None
            and isinstance(interaction.user, discord.Member)
            and (
                interaction.user.guild_permissions.administrator
                or interaction.user.id == OWNER_ID
            )
        )
    )
    @discord.app_commands.describe(
        giveaway_id="The giveaway ID shown in the embed footer"
    )
    async def giveaway_end(self, interaction: discord.Interaction, giveaway_id: str):
        await interaction.response.defer(ephemeral=True)
        giveaways = load_giveaways()

        if giveaway_id not in giveaways:
            await interaction.followup.send("❌ Giveaway not found.", ephemeral=True)
            return
        if giveaways[giveaway_id].get("ended"):
            await interaction.followup.send(
                "❌ That giveaway has already ended.", ephemeral=True
            )
            return

        task = self.active_tasks.pop(giveaway_id, None)
        if task:
            task.cancel()

        await self._end_giveaway(giveaway_id, delay=0)
        await interaction.followup.send(
            "✅ Giveaway ended and winners drawn!", ephemeral=True
        )

    @giveaway.command(name="reroll", description="Reroll winners for a specific prize tier")
    @discord.app_commands.check(
        lambda interaction: (
            interaction.guild is not None
            and isinstance(interaction.user, discord.Member)
            and (
                interaction.user.guild_permissions.administrator
                or interaction.user.id == OWNER_ID
            )
        )
    )
    @discord.app_commands.describe(
        giveaway_id="The giveaway ID shown in the embed footer",
        prize_name="Prize tier to reroll (e.g. Gold, Silver)",
    )
    async def giveaway_reroll(
        self,
        interaction: discord.Interaction,
        giveaway_id: str,
        prize_name: str,
    ):
        await interaction.response.defer(ephemeral=True)
        giveaways = load_giveaways()

        if giveaway_id not in giveaways:
            await interaction.followup.send("❌ Giveaway not found.", ephemeral=True)
            return

        data = giveaways[giveaway_id]
        if not data.get("ended"):
            await interaction.followup.send(
                "❌ That giveaway hasn't ended yet.", ephemeral=True
            )
            return

        prize_obj = next(
            (p for p in data["prizes"] if p["name"].lower() == prize_name.lower()),
            None,
        )
        if prize_obj is None:
            names = ", ".join(p["name"] for p in data["prizes"])
            await interaction.followup.send(
                f"❌ Prize tier not found. Available: {names}", ephemeral=True
            )
            return

        entrants = await self._collect_entrants(data)
        excluded: set[int] = set()
        for pname, wlist in data["winners"].items():
            if pname.lower() != prize_name.lower():
                excluded.update(int(uid) for uid in wlist)

        pool = [uid for uid in entrants if uid not in excluded]
        qty = min(prize_obj["quantity"], len(pool))
        new_winners = random.sample(pool, qty) if qty > 0 else []

        data["winners"][prize_obj["name"]] = [str(uid) for uid in new_winners]
        giveaways[giveaway_id] = data
        save_giveaways(giveaways)

        guild = self.bot.get_guild(data["guild_id"])
        channel = guild.get_channel(data["channel_id"]) if guild else None
        if channel:
            mention_str = " ".join(f"<@{uid}>" for uid in new_winners)
            content = "🔁 **Reroll!**" + (f"  {mention_str}" if mention_str else "")
            await channel.send(
                content=content,
                embed=self._build_reroll_embed(data, prize_obj, new_winners),
            )

        await interaction.followup.send("✅ Reroll complete!", ephemeral=True)

    @giveaway.command(name="list", description="List all active giveaways in this server")
    @discord.app_commands.check(
        lambda interaction: (
            interaction.guild is not None
            and isinstance(interaction.user, discord.Member)
            and (
                interaction.user.guild_permissions.administrator
                or interaction.user.id == OWNER_ID
            )
        )
    )
    async def giveaway_list(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("❌ Server only.", ephemeral=True)
            return

        giveaways = load_giveaways()
        active = [
            d
            for d in giveaways.values()
            if not d.get("ended") and d["guild_id"] == interaction.guild.id
        ]

        if not active:
            await interaction.response.send_message(
                "No active giveaways right now.", ephemeral=True
            )
            return

        embed = discord.Embed(
            title="🎉  Active Giveaways",
            color=discord.Color.gold(),
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        for d in active:
            end_time = int(d["end_time"])
            prize_summary = ", ".join(
                f"{p['name']} ×{p['quantity']}" for p in d["prizes"]
            )
            embed.add_field(
                name=d["title"],
                value=(
                    f"Ends: <t:{end_time}:R>\n"
                    f"Prizes: {prize_summary}\n"
                    f"Channel: <#{d['channel_id']}>\n"
                    f"ID: `{d['giveaway_id']}`"
                ),
                inline=False,
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: discord.app_commands.AppCommandError,
    ):
        msg = (
            "❌ You don't have permission to use this command."
            if isinstance(error, discord.app_commands.CheckFailure)
            else f"❌ An error occurred: {error}"
        )
        if not interaction.response.is_done():
            await interaction.response.send_message(msg, ephemeral=True)
        else:
            try:
                await interaction.followup.send(msg, ephemeral=True)
            except Exception:
                pass


async def setup(bot: commands.Bot):
    await bot.add_cog(GiveawayCog(bot))

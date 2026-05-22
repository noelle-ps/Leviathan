import discord
from discord.ext import commands
import asyncio
import datetime
import json
import os
import random
import logging
from utils.embed_vars import resolve_vars

logger = logging.getLogger(__name__)

GIVEAWAYS_FILE = "data/giveaways.json"
OWNER_ID = 1136231768534569090


def is_mod():
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return False
        return interaction.user.guild_permissions.administrator or interaction.user.id == OWNER_ID
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


def _apply_vars(text: str | None, guild: discord.Guild | None, user: discord.Member | discord.User | None) -> str | None:
    if not text:
        return text
    return resolve_vars(text, guild, user)


# ── Persistent entry button ────────────────────────────────────────────────────

class GiveawayEntryButton(discord.ui.Button):
    def __init__(self, giveaway_id: str, disabled: bool = False):
        super().__init__(
            label="Enter Giveaway",
            emoji="🎉",
            style=discord.ButtonStyle.primary,
            custom_id=f"giveaway_enter:{giveaway_id}",
            disabled=disabled,
        )

    async def callback(self, interaction: discord.Interaction):
        giveaways = load_giveaways()
        giveaway_id = self.custom_id.split(":", 1)[1]
        data = giveaways.get(giveaway_id)

        if not data or data.get("ended"):
            await interaction.response.send_message(
                "❌ This giveaway has already ended.", ephemeral=True
            )
            return

        required_role_id = data.get("required_role")
        if required_role_id and interaction.guild:
            member = interaction.guild.get_member(interaction.user.id)
            if not member or not any(r.id == required_role_id for r in member.roles):
                await interaction.response.send_message(
                    f"❌ You need <@&{required_role_id}> to enter this giveaway.",
                    ephemeral=True,
                )
                return

        entrants: list[str] = data.get("entrants", [])
        uid = str(interaction.user.id)

        if uid in entrants:
            entrants.remove(uid)
            msg = "👋 You've withdrawn your entry."
        else:
            entrants.append(uid)
            msg = "🎉 You've entered! Good luck!"

        data["entrants"] = entrants
        giveaways[giveaway_id] = data
        save_giveaways(giveaways)

        try:
            updated_embed = GiveawayCog._static_build_active_embed(data)
            await interaction.response.edit_message(embed=updated_embed)
            await interaction.followup.send(msg, ephemeral=True)
        except Exception:
            await interaction.response.send_message(msg, ephemeral=True)


class GiveawayView(discord.ui.View):
    def __init__(self, giveaway_id: str, disabled: bool = False):
        super().__init__(timeout=None)
        self.add_item(GiveawayEntryButton(giveaway_id, disabled=disabled))


# ── Cog ────────────────────────────────────────────────────────────────────────

class GiveawayCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.active_tasks: dict[str, asyncio.Task] = {}

    async def cog_load(self):
        giveaways = load_giveaways()
        now = datetime.datetime.now(datetime.timezone.utc).timestamp()
        for gid, data in list(giveaways.items()):
            mid = data.get("message_id")
            if not data.get("ended"):
                self.bot.add_view(GiveawayView(gid), message_id=mid)
                delay = max(0.0, data["end_time"] - now)
                self.active_tasks[gid] = asyncio.create_task(
                    self._end_giveaway(gid, delay=delay)
                )
            else:
                self.bot.add_view(GiveawayView(gid, disabled=True), message_id=mid)

    # ── Embed builders ─────────────────────────────────────────────────────────

    @staticmethod
    def _static_build_active_embed(data: dict) -> discord.Embed:
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
        embed.add_field(name="👥 Entries", value=str(len(data.get("entrants", []))), inline=True)
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

        # Custom footer — stored as already-resolved strings
        footer_text = data.get("footer_text") or f"Click the button to enter  •  ID: {data['giveaway_id']}"
        footer_icon = data.get("footer_icon") or None
        if footer_icon:
            embed.set_footer(text=footer_text, icon_url=footer_icon)
        else:
            embed.set_footer(text=footer_text)

        # Custom image
        if data.get("image"):
            embed.set_image(url=data["image"])

        embed.timestamp = datetime.datetime.fromtimestamp(end_time, tz=datetime.timezone.utc)
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
        embed.add_field(name="Ended", value=f"<t:{end_time}:f>", inline=True)
        embed.add_field(name="Total entries", value=str(len(data.get("entrants", []))), inline=True)

        footer_icon = data.get("footer_icon") or None
        if footer_icon:
            embed.set_footer(text=f"Giveaway ended  •  ID: {data['giveaway_id']}", icon_url=footer_icon)
        else:
            embed.set_footer(text=f"Giveaway ended  •  ID: {data['giveaway_id']}")

        if data.get("image"):
            embed.set_image(url=data["image"])

        embed.timestamp = datetime.datetime.fromtimestamp(end_time, tz=datetime.timezone.utc)
        return embed

    async def _build_winner_announce_embed(
        self, data: dict, prize: dict, winner_id: str
    ) -> discord.Embed:
        """One embed per individual winner, with their own avatar as thumbnail."""
        embed = discord.Embed(
            color=discord.Color.gold(),
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.title = f"🏆  {prize['name']}"
        embed.description = (
            f"🎉 **Congratulations, <@{winner_id}>!**\n\n"
            f"You have won **{prize['description']}** from **{data['title']}**.\n\n"
            f"📩 Message the host for your reward!"
        )
        try:
            user = await self.bot.fetch_user(int(winner_id))
            embed.set_thumbnail(url=user.display_avatar.url)
        except Exception:
            pass
        embed.set_footer(text=f"{data['title']}  •  ID: {data['giveaway_id']}")
        return embed

    def _build_reroll_embed(self, data: dict, prize: dict, winner_id: int) -> discord.Embed:
        embed = discord.Embed(
            title=f"🔁  Reroll — {prize['name']}",
            color=discord.Color.blurple(),
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        if winner_id:
            embed.description = (
                f"🎉 **Congratulations, <@{winner_id}>!**\n\n"
                f"You have won **{prize['description']}** from **{data['title']}**.\n\n"
                f"📩 Message the host for your reward!"
            )
        else:
            embed.description = "*No eligible entries for this prize.*"
        embed.set_footer(text=f"{data['title']}  •  ID: {data['giveaway_id']}")
        return embed

    # ── Core end logic ─────────────────────────────────────────────────────────

    async def _end_giveaway(self, giveaway_id: str, delay: float):
        if delay > 0:
            await asyncio.sleep(delay)

        giveaways = load_giveaways()
        data = giveaways.get(giveaway_id)
        if not data or data.get("ended"):
            return

        guild = self.bot.get_guild(data["guild_id"])
        channel = guild.get_channel(data["channel_id"]) if guild else None

        entrants = [int(uid) for uid in data.get("entrants", [])]
        random.shuffle(entrants)
        used: set[int] = set()
        winners_by_prize: dict[str, list[str]] = {}
        for prize in data["prizes"]:
            available = [uid for uid in entrants if uid not in used]
            qty = min(prize["quantity"], len(available))
            chosen = random.sample(available, qty) if qty > 0 else []
            winners_by_prize[prize["name"]] = [str(uid) for uid in chosen]
            used.update(chosen)

        if channel:
            try:
                message = await channel.fetch_message(data["message_id"])
                await message.edit(
                    embed=self._build_ended_embed(data),
                    view=GiveawayView(giveaway_id, disabled=True),
                )
            except (discord.NotFound, discord.HTTPException):
                pass

        data["ended"] = True
        data["announced"] = False
        data["winners"] = winners_by_prize
        giveaways[giveaway_id] = data
        save_giveaways(giveaways)
        self.active_tasks.pop(giveaway_id, None)

    # ── Slash command group ────────────────────────────────────────────────────

    giveaway = discord.app_commands.Group(
        name="giveaway",
        description="Manage giveaways — mod only",
        default_permissions=discord.Permissions(administrator=True),
    )

    @giveaway.command(name="start", description="Start a new multi-prize giveaway")
    @is_mod()
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
        footer_text="Footer text — supports {server_name}, {date}, {user_name} etc.",
        footer_icon="Footer icon URL — supports {server_icon}, {user_avatar} etc.",
        image="Large image URL at the bottom — supports {server_icon}, {user_avatar} etc.",
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
        footer_text: str | None = None,
        footer_icon: str | None = None,
        image: str | None = None,
    ):
        await interaction.response.defer(ephemeral=True)

        delta = parse_duration(duration)
        if delta is None:
            await interaction.followup.send(
                "❌ Invalid duration. Use formats like `30s`, `10m`, `2h`, `1d`.", ephemeral=True
            )
            return

        target: discord.TextChannel
        if channel is not None:
            target = channel
        elif isinstance(interaction.channel, discord.TextChannel):
            target = interaction.channel
        else:
            await interaction.followup.send("❌ Use this command in a text channel.", ephemeral=True)
            return

        prizes = [
            {"name": prize1_name, "description": prize1_description, "quantity": max(1, prize1_quantity)}
        ]
        if prize2_name and prize2_description:
            prizes.append({"name": prize2_name, "description": prize2_description, "quantity": max(1, prize2_quantity)})
        if prize3_name and prize3_description:
            prizes.append({"name": prize3_name, "description": prize3_description, "quantity": max(1, prize3_quantity)})

        # Resolve {} variables at creation time using the command user's context
        resolved_footer_text = _apply_vars(footer_text, interaction.guild, interaction.user)
        resolved_footer_icon = _apply_vars(footer_icon, interaction.guild, interaction.user)
        resolved_image = _apply_vars(image, interaction.guild, interaction.user)

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
            "entrants": [],
            "ended": False,
            "announced": False,
            "winners": {},
            "footer_text": resolved_footer_text,
            "footer_icon": resolved_footer_icon,
            "image": resolved_image,
        }

        message = await target.send(embed=self._static_build_active_embed(data))
        giveaway_id = str(message.id)
        data["giveaway_id"] = giveaway_id
        data["message_id"] = message.id

        view = GiveawayView(giveaway_id)
        await message.edit(embed=self._static_build_active_embed(data), view=view)
        self.bot.add_view(view, message_id=message.id)

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
    @is_mod()
    @discord.app_commands.describe(giveaway_id="The giveaway ID shown in the embed footer")
    async def giveaway_end(self, interaction: discord.Interaction, giveaway_id: str):
        await interaction.response.defer(ephemeral=True)
        giveaways = load_giveaways()

        if giveaway_id not in giveaways:
            await interaction.followup.send("❌ Giveaway not found.", ephemeral=True)
            return
        if giveaways[giveaway_id].get("ended"):
            await interaction.followup.send("❌ That giveaway has already ended.", ephemeral=True)
            return

        task = self.active_tasks.pop(giveaway_id, None)
        if task:
            task.cancel()

        await self._end_giveaway(giveaway_id, delay=0)
        await interaction.followup.send(
            "✅ Giveaway ended! Use `/giveaway announce` whenever you're ready.", ephemeral=True
        )

    @giveaway.command(
        name="announce",
        description="Dramatically reveal winners — pings @everyone then reveals each prize tier",
    )
    @is_mod()
    @discord.app_commands.describe(giveaway_id="The giveaway ID shown in the embed footer")
    async def giveaway_announce(self, interaction: discord.Interaction, giveaway_id: str):
        await interaction.response.defer(ephemeral=True)
        giveaways = load_giveaways()

        if giveaway_id not in giveaways:
            await interaction.followup.send("❌ Giveaway not found.", ephemeral=True)
            return

        data = giveaways[giveaway_id]

        if not data.get("ended"):
            await interaction.followup.send(
                "❌ That giveaway hasn't ended yet. Use `/giveaway end` first.", ephemeral=True
            )
            return
        if data.get("announced"):
            await interaction.followup.send(
                "❌ Winners have already been announced. Use `/giveaway reroll` if needed.",
                ephemeral=True,
            )
            return

        channel = interaction.guild.get_channel(data["channel_id"])
        if not channel or not isinstance(channel, discord.TextChannel):
            await interaction.followup.send(
                "❌ The original giveaway channel no longer exists.", ephemeral=True
            )
            return

        winners = data.get("winners", {})

        # Hype ping
        await channel.send(
            "🎊 **@everyone — Giveaway winners are about to be announced!** 🥁 Get ready...",
            allowed_mentions=discord.AllowedMentions(everyone=True),
        )

        await asyncio.sleep(5)

        # Build flat list of (prize, winner_id) — one entry per individual winner
        all_winners: list[tuple[dict, str]] = []
        for prize in data["prizes"]:
            for uid in winners.get(prize["name"], []):
                all_winners.append((prize, uid))

        if not all_winners:
            await channel.send("*No eligible entries were found for this giveaway.*")
        else:
            for idx, (prize, uid) in enumerate(all_winners):
                embed = await self._build_winner_announce_embed(data, prize, uid)
                await channel.send(content=f"<@{uid}>", embed=embed)
                if idx < len(all_winners) - 1:
                    await asyncio.sleep(5)

        data["announced"] = True
        giveaways[giveaway_id] = data
        save_giveaways(giveaways)

        await interaction.followup.send("✅ Winners announced!", ephemeral=True)

    @giveaway.command(name="winners", description="Quietly look up the winners of any past giveaway")
    @is_mod()
    @discord.app_commands.describe(giveaway_id="The giveaway ID shown in the embed footer")
    async def giveaway_winners(self, interaction: discord.Interaction, giveaway_id: str):
        await interaction.response.defer(ephemeral=True)
        giveaways = load_giveaways()

        if giveaway_id not in giveaways:
            await interaction.followup.send("❌ Giveaway not found.", ephemeral=True)
            return

        data = giveaways[giveaway_id]

        if not data.get("ended"):
            await interaction.followup.send(
                "❌ That giveaway hasn't ended yet — no winners drawn yet.", ephemeral=True
            )
            return

        winners = data.get("winners", {})
        embed = discord.Embed(
            title=f"🏆  Winners — {data['title']}",
            color=discord.Color.gold(),
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )

        for prize in data["prizes"]:
            prize_winners = winners.get(prize["name"], [])
            if prize_winners:
                value = "\n".join(f"<@{uid}> (`{uid}`)" for uid in prize_winners)
            else:
                value = "*No eligible entries*"
            embed.add_field(
                name=f"{prize['name']} — {prize['description']}",
                value=value,
                inline=False,
            )

        end_time = int(data["end_time"])
        embed.add_field(name="Ended", value=f"<t:{end_time}:f>", inline=True)
        embed.add_field(name="Total entries", value=str(len(data.get("entrants", []))), inline=True)
        embed.add_field(name="Announced", value="✅ Yes" if data.get("announced") else "⏳ Not yet", inline=True)
        embed.set_footer(text=f"Giveaway ID: {giveaway_id}  •  Only visible to you")

        await interaction.followup.send(embed=embed, ephemeral=True)

    @giveaway.command(name="reroll", description="Reroll winners for a specific prize tier")
    @is_mod()
    @discord.app_commands.describe(
        giveaway_id="The giveaway ID shown in the embed footer",
        prize_name="Prize tier to reroll (e.g. Gold, Silver)",
    )
    async def giveaway_reroll(
        self, interaction: discord.Interaction, giveaway_id: str, prize_name: str
    ):
        await interaction.response.defer(ephemeral=True)
        giveaways = load_giveaways()

        if giveaway_id not in giveaways:
            await interaction.followup.send("❌ Giveaway not found.", ephemeral=True)
            return

        data = giveaways[giveaway_id]
        if not data.get("ended"):
            await interaction.followup.send("❌ That giveaway hasn't ended yet.", ephemeral=True)
            return

        prize_obj = next(
            (p for p in data["prizes"] if p["name"].lower() == prize_name.lower()), None
        )
        if prize_obj is None:
            names = ", ".join(p["name"] for p in data["prizes"])
            await interaction.followup.send(
                f"❌ Prize tier not found. Available: {names}", ephemeral=True
            )
            return

        entrants = [int(uid) for uid in data.get("entrants", [])]
        excluded: set[int] = {
            int(uid)
            for pname, wlist in data["winners"].items()
            if pname.lower() != prize_name.lower()
            for uid in wlist
        }
        pool = [uid for uid in entrants if uid not in excluded]
        qty = min(prize_obj["quantity"], len(pool))
        new_winners = random.sample(pool, qty) if qty > 0 else []

        data["winners"][prize_obj["name"]] = [str(uid) for uid in new_winners]
        giveaways[giveaway_id] = data
        save_giveaways(giveaways)

        guild = self.bot.get_guild(data["guild_id"])
        ch = guild.get_channel(data["channel_id"]) if guild else None
        if ch and isinstance(ch, discord.TextChannel):
            if new_winners:
                for uid in new_winners:
                    await ch.send(
                        content=f"🔁 **Reroll!**  <@{uid}>",
                        embed=self._build_reroll_embed(data, prize_obj, uid),
                    )
            else:
                await ch.send(embed=self._build_reroll_embed(data, prize_obj, 0))

        await interaction.followup.send("✅ Reroll complete!", ephemeral=True)

    @giveaway.command(name="dm", description="DM every winner of a giveaway to contact the host for their reward")
    @is_mod()
    @discord.app_commands.describe(giveaway_id="The giveaway ID shown in the embed footer")
    async def giveaway_dm(self, interaction: discord.Interaction, giveaway_id: str):
        await interaction.response.defer(ephemeral=True)
        giveaways = load_giveaways()

        if giveaway_id not in giveaways:
            await interaction.followup.send("❌ Giveaway not found.", ephemeral=True)
            return

        data = giveaways[giveaway_id]

        if not data.get("ended"):
            await interaction.followup.send(
                "❌ That giveaway hasn't ended yet — no winners drawn yet.", ephemeral=True
            )
            return

        winners = data.get("winners", {})
        all_winner_ids: list[tuple[dict, str]] = []
        for prize in data["prizes"]:
            for uid in winners.get(prize["name"], []):
                all_winner_ids.append((prize, uid))

        if not all_winner_ids:
            await interaction.followup.send("❌ No winners found for this giveaway.", ephemeral=True)
            return

        sent = 0
        failed = 0
        for prize, uid in all_winner_ids:
            embed = discord.Embed(
                title=f"🏆  You won — {data['title']}!",
                description=(
                    f"🎉 **Congratulations!**\n\n"
                    f"You have won **{prize['name']}** — {prize['description']} "
                    f"from **{data['title']}**.\n\n"
                    f"📩 Please message <@{data['hosted_by']}> to claim your reward!"
                ),
                color=discord.Color.gold(),
                timestamp=datetime.datetime.now(datetime.timezone.utc),
            )
            embed.set_footer(text=f"Giveaway ID: {giveaway_id}")
            try:
                user = await self.bot.fetch_user(int(uid))
                await user.send(embed=embed)
                sent += 1
            except (discord.Forbidden, discord.HTTPException):
                failed += 1

        parts = [f"✅ DMed **{sent}** winner{'s' if sent != 1 else ''}."]
        if failed:
            parts.append(f"⚠️ Couldn't DM **{failed}** (DMs closed or blocked).")
        await interaction.followup.send(" ".join(parts), ephemeral=True)

    @giveaway.command(name="edit", description="Edit an active giveaway's title, end time, or prizes")
    @is_mod()
    @discord.app_commands.describe(
        giveaway_id="The giveaway ID shown in the embed footer",
        title="New title (leave blank to keep current)",
        extend="Extend the end time by this amount (e.g. 30m, 2h, 1d)",
        prize1_name="Rename prize tier 1",
        prize1_description="New description for prize tier 1",
        prize2_name="Rename prize tier 2",
        prize2_description="New description for prize tier 2",
        prize3_name="Rename prize tier 3",
        prize3_description="New description for prize tier 3",
        footer_text="New footer text (supports {} vars)",
        footer_icon="New footer icon URL (supports {} vars)",
        image="New image URL (supports {} vars)",
    )
    async def giveaway_edit(
        self,
        interaction: discord.Interaction,
        giveaway_id: str,
        title: str | None = None,
        extend: str | None = None,
        prize1_name: str | None = None,
        prize1_description: str | None = None,
        prize2_name: str | None = None,
        prize2_description: str | None = None,
        prize3_name: str | None = None,
        prize3_description: str | None = None,
        footer_text: str | None = None,
        footer_icon: str | None = None,
        image: str | None = None,
    ):
        await interaction.response.defer(ephemeral=True)
        giveaways = load_giveaways()

        if giveaway_id not in giveaways:
            await interaction.followup.send("❌ Giveaway not found.", ephemeral=True)
            return

        data = giveaways[giveaway_id]

        if data.get("ended"):
            await interaction.followup.send(
                "❌ That giveaway has already ended and can't be edited.", ephemeral=True
            )
            return

        changes: list[str] = []

        if title:
            data["title"] = title
            changes.append(f"title → **{title}**")

        if extend:
            delta = parse_duration(extend)
            if delta is None:
                await interaction.followup.send(
                    "❌ Invalid duration for extend. Use e.g. `30m`, `2h`, `1d`.", ephemeral=True
                )
                return
            data["end_time"] = data["end_time"] + delta.total_seconds()
            end_ts = int(data["end_time"])
            changes.append(f"end time extended → <t:{end_ts}:f>")

            # Reschedule the auto-end task
            old_task = self.active_tasks.pop(giveaway_id, None)
            if old_task:
                old_task.cancel()
            now = datetime.datetime.now(datetime.timezone.utc).timestamp()
            new_delay = max(0.0, data["end_time"] - now)
            self.active_tasks[giveaway_id] = asyncio.create_task(
                self._end_giveaway(giveaway_id, delay=new_delay)
            )

        prize_edits = [
            (0, prize1_name, prize1_description),
            (1, prize2_name, prize2_description),
            (2, prize3_name, prize3_description),
        ]
        for idx, new_name, new_desc in prize_edits:
            if idx >= len(data["prizes"]):
                break
            if new_name:
                old = data["prizes"][idx]["name"]
                data["prizes"][idx]["name"] = new_name
                # Keep winners dict in sync if it exists
                if old in data.get("winners", {}):
                    data["winners"][new_name] = data["winners"].pop(old)
                changes.append(f"prize {idx + 1} name → **{new_name}**")
            if new_desc:
                data["prizes"][idx]["description"] = new_desc
                changes.append(f"prize {idx + 1} description → {new_desc}")

        if footer_text is not None:
            data["footer_text"] = _apply_vars(footer_text, interaction.guild, interaction.user)
            changes.append("footer text updated")
        if footer_icon is not None:
            data["footer_icon"] = _apply_vars(footer_icon, interaction.guild, interaction.user)
            changes.append("footer icon updated")
        if image is not None:
            data["image"] = _apply_vars(image, interaction.guild, interaction.user)
            changes.append("image updated")

        if not changes:
            await interaction.followup.send("Nothing to update — provide at least one field.", ephemeral=True)
            return

        giveaways[giveaway_id] = data
        save_giveaways(giveaways)

        # Refresh the live embed in the channel
        guild = self.bot.get_guild(data["guild_id"])
        channel = guild.get_channel(data["channel_id"]) if guild else None
        if channel and isinstance(channel, discord.TextChannel):
            try:
                message = await channel.fetch_message(data["message_id"])
                await message.edit(embed=self._static_build_active_embed(data))
            except (discord.NotFound, discord.HTTPException):
                pass

        summary = "\n".join(f"• {c}" for c in changes)
        await interaction.followup.send(
            f"✅ Giveaway updated:\n{summary}", ephemeral=True
        )

    @giveaway.command(name="list", description="List all active giveaways in this server")
    @is_mod()
    async def giveaway_list(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("❌ Server only.", ephemeral=True)
            return

        giveaways = load_giveaways()
        active = [
            d for d in giveaways.values()
            if not d.get("ended") and d["guild_id"] == interaction.guild.id
        ]

        if not active:
            await interaction.response.send_message("No active giveaways right now.", ephemeral=True)
            return

        embed = discord.Embed(
            title="🎉  Active Giveaways",
            color=discord.Color.gold(),
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        for d in active:
            end_time = int(d["end_time"])
            prize_summary = ", ".join(f"{p['name']} ×{p['quantity']}" for p in d["prizes"])
            embed.add_field(
                name=d["title"],
                value=(
                    f"Ends: <t:{end_time}:R>\n"
                    f"Prizes: {prize_summary}\n"
                    f"Entries: {len(d.get('entrants', []))}\n"
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

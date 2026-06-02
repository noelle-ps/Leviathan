import discord
from discord.ext import commands
import asyncio
import asyncpg
import datetime
import json
import os
import random
import logging
from utils.embed_vars import resolve_vars

logger = logging.getLogger(__name__)

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


def _apply_vars(text: str | None, guild, user) -> str | None:
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
        giveaway_id = self.custom_id.split(":", 1)[1]
        cog: GiveawayCog | None = interaction.client.cogs.get("GiveawayCog")  # type: ignore
        if cog is None:
            await interaction.response.send_message("❌ Giveaway system unavailable.", ephemeral=True)
            return

        data = await cog.db_get(giveaway_id)
        if not data or data.get("ended"):
            await interaction.response.send_message("❌ This giveaway has already ended.", ephemeral=True)
            return

        required_role_id = data.get("required_role")
        if required_role_id and interaction.guild:
            member = interaction.guild.get_member(interaction.user.id)
            if not member or not any(r.id == required_role_id for r in member.roles):
                await interaction.response.send_message(
                    f"❌ You need <@&{required_role_id}> to enter this giveaway.", ephemeral=True
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
        await cog.db_set(giveaway_id, data)

        try:
            await interaction.response.edit_message(embed=GiveawayCog._static_build_active_embed(data))
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
        self.pool: asyncpg.Pool | None = None
        self.active_tasks: dict[str, asyncio.Task] = {}

    # ── DB helpers ─────────────────────────────────────────────────────────────

    async def db_init(self):
        db_url = os.environ.get("DATABASE_URL")
        if not db_url:
            raise RuntimeError("DATABASE_URL env var is not set — cannot start giveaway cog.")
        self.pool = await asyncpg.create_pool(dsn=db_url)
        async with self.pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS giveaways (
                    id TEXT PRIMARY KEY,
                    data JSONB NOT NULL
                )
            """)

    async def db_get(self, giveaway_id: str) -> dict | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT data FROM giveaways WHERE id = $1", giveaway_id)
        if row is None:
            return None
        return dict(row["data"])

    async def db_all(self) -> dict[str, dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT id, data FROM giveaways")
        return {row["id"]: dict(row["data"]) for row in rows}

    async def db_all_for_guild(self, guild_id: int) -> list[dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT data FROM giveaways WHERE (data->>'guild_id')::bigint = $1", guild_id
            )
        return [dict(row["data"]) for row in rows]

    async def db_set(self, giveaway_id: str, data: dict):
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO giveaways (id, data) VALUES ($1, $2::jsonb)
                ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data
                """,
                giveaway_id,
                json.dumps(data),
            )

    async def db_delete(self, giveaway_id: str):
        async with self.pool.acquire() as conn:
            await conn.execute("DELETE FROM giveaways WHERE id = $1", giveaway_id)

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def cog_load(self):
        await self.db_init()
        all_giveaways = await self.db_all()
        now = datetime.datetime.now(datetime.timezone.utc).timestamp()
        for gid, data in all_giveaways.items():
            mid = data.get("message_id")
            if not data.get("ended"):
                self.bot.add_view(GiveawayView(gid), message_id=mid)
                delay = max(0.0, data["end_time"] - now)
                self.active_tasks[gid] = asyncio.create_task(
                    self._end_giveaway(gid, delay=delay)
                )
            else:
                self.bot.add_view(GiveawayView(gid, disabled=True), message_id=mid)

    async def cog_unload(self):
        for task in self.active_tasks.values():
            task.cancel()
        if self.pool:
            await self.pool.close()

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
            embed.add_field(name="🔒 Required role", value=f"<@&{data['required_role']}>", inline=True)

        footer_text = data.get("footer_text") or f"Click the button to enter  •  ID: {data['giveaway_id']}"
        footer_icon = data.get("footer_icon") or None
        if footer_icon:
            embed.set_footer(text=footer_text, icon_url=footer_icon)
        else:
            embed.set_footer(text=footer_text)

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

    async def _build_winner_announce_embed(self, data: dict, prize: dict, winner_id: str) -> discord.Embed:
        embed = discord.Embed(
            color=discord.Color.gold(),
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.title = f"🏆  {prize['name']}"
        embed.description = (
            f"🎉 **Congratulations, <@{winner_id}>!**\n\n"
            f"You have won **{prize['name']}** — {prize['description']} from **{data['title']}**.\n\n"
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
                f"You have won **{prize['name']}** — {prize['description']} from **{data['title']}**.\n\n"
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

        data = await self.db_get(giveaway_id)
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
        await self.db_set(giveaway_id, data)
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
            await interaction.followup.send("❌ Invalid duration. Use formats like `30s`, `10m`, `2h`, `1d`.", ephemeral=True)
            return

        target: discord.TextChannel
        if channel is not None:
            target = channel
        elif isinstance(interaction.channel, discord.TextChannel):
            target = interaction.channel
        else:
            await interaction.followup.send("❌ Use this command in a text channel.", ephemeral=True)
            return

        prizes = [{"name": prize1_name, "description": prize1_description, "quantity": max(1, prize1_quantity)}]
        if prize2_name and prize2_description:
            prizes.append({"name": prize2_name, "description": prize2_description, "quantity": max(1, prize2_quantity)})
        if prize3_name and prize3_description:
            prizes.append({"name": prize3_name, "description": prize3_description, "quantity": max(1, prize3_quantity)})

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
            "footer_text": _apply_vars(footer_text, interaction.guild, interaction.user),
            "footer_icon": _apply_vars(footer_icon, interaction.guild, interaction.user),
            "image": _apply_vars(image, interaction.guild, interaction.user),
        }

        message = await target.send(embed=self._static_build_active_embed(data))
        giveaway_id = str(message.id)
        data["giveaway_id"] = giveaway_id
        data["message_id"] = message.id

        view = GiveawayView(giveaway_id)
        await message.edit(embed=self._static_build_active_embed(data), view=view)
        self.bot.add_view(view, message_id=message.id)

        await self.db_set(giveaway_id, data)

        self.active_tasks[giveaway_id] = asyncio.create_task(
            self._end_giveaway(giveaway_id, delay=delta.total_seconds())
        )

        await interaction.followup.send(f"✅ Giveaway **{title}** started in {target.mention}!", ephemeral=True)

    @giveaway.command(name="end", description="End a giveaway early and draw winners now")
    @is_mod()
    @discord.app_commands.describe(giveaway_id="The giveaway ID shown in the embed footer")
    async def giveaway_end(self, interaction: discord.Interaction, giveaway_id: str):
        await interaction.response.defer(ephemeral=True)
        data = await self.db_get(giveaway_id)

        if not data:
            await interaction.followup.send("❌ Giveaway not found.", ephemeral=True)
            return
        if data.get("ended"):
            await interaction.followup.send("❌ That giveaway has already ended.", ephemeral=True)
            return

        task = self.active_tasks.pop(giveaway_id, None)
        if task:
            task.cancel()

        await self._end_giveaway(giveaway_id, delay=0)
        await interaction.followup.send("✅ Giveaway ended! Use `/giveaway announce` whenever you're ready.", ephemeral=True)

    @giveaway.command(name="announce", description="Dramatically reveal winners one by one with 5s intervals")
    @is_mod()
    @discord.app_commands.describe(giveaway_id="The giveaway ID shown in the embed footer")
    async def giveaway_announce(self, interaction: discord.Interaction, giveaway_id: str):
        await interaction.response.defer(ephemeral=True)
        data = await self.db_get(giveaway_id)

        if not data:
            await interaction.followup.send("❌ Giveaway not found.", ephemeral=True)
            return
        if not data.get("ended"):
            await interaction.followup.send("❌ That giveaway hasn't ended yet. Use `/giveaway end` first.", ephemeral=True)
            return
        if data.get("announced"):
            await interaction.followup.send("❌ Winners have already been announced. Use `/giveaway reroll` if needed.", ephemeral=True)
            return

        channel = interaction.guild.get_channel(data["channel_id"])
        if not channel or not isinstance(channel, discord.TextChannel):
            await interaction.followup.send("❌ The original giveaway channel no longer exists.", ephemeral=True)
            return

        winners = data.get("winners", {})

        await channel.send(
            "🎊 **@everyone — Giveaway winners are about to be announced!** 🥁 Get ready...",
            allowed_mentions=discord.AllowedMentions(everyone=True),
        )
        await asyncio.sleep(5)

        all_winners: list[tuple[dict, str]] = [
            (prize, uid)
            for prize in data["prizes"]
            for uid in winners.get(prize["name"], [])
        ]

        if not all_winners:
            await channel.send("*No eligible entries were found for this giveaway.*")
        else:
            for idx, (prize, uid) in enumerate(all_winners):
                embed = await self._build_winner_announce_embed(data, prize, uid)
                await channel.send(content=f"<@{uid}>", embed=embed)
                if idx < len(all_winners) - 1:
                    await asyncio.sleep(5)

        data["announced"] = True
        await self.db_set(giveaway_id, data)
        await interaction.followup.send("✅ Winners announced!", ephemeral=True)

    @giveaway.command(name="winners", description="Quietly look up the winners of any past giveaway")
    @is_mod()
    @discord.app_commands.describe(giveaway_id="The giveaway ID shown in the embed footer")
    async def giveaway_winners(self, interaction: discord.Interaction, giveaway_id: str):
        await interaction.response.defer(ephemeral=True)
        data = await self.db_get(giveaway_id)

        if not data:
            await interaction.followup.send("❌ Giveaway not found.", ephemeral=True)
            return
        if not data.get("ended"):
            await interaction.followup.send("❌ That giveaway hasn't ended yet.", ephemeral=True)
            return

        winners = data.get("winners", {})
        embed = discord.Embed(
            title=f"🏆  Winners — {data['title']}",
            color=discord.Color.gold(),
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        for prize in data["prizes"]:
            prize_winners = winners.get(prize["name"], [])
            value = "\n".join(f"<@{uid}> (`{uid}`)" for uid in prize_winners) if prize_winners else "*No eligible entries*"
            embed.add_field(name=f"{prize['name']} — {prize['description']}", value=value, inline=False)

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
    async def giveaway_reroll(self, interaction: discord.Interaction, giveaway_id: str, prize_name: str):
        await interaction.response.defer(ephemeral=True)
        data = await self.db_get(giveaway_id)

        if not data:
            await interaction.followup.send("❌ Giveaway not found.", ephemeral=True)
            return
        if not data.get("ended"):
            await interaction.followup.send("❌ That giveaway hasn't ended yet.", ephemeral=True)
            return

        prize_obj = next((p for p in data["prizes"] if p["name"].lower() == prize_name.lower()), None)
        if prize_obj is None:
            names = ", ".join(p["name"] for p in data["prizes"])
            await interaction.followup.send(f"❌ Prize tier not found. Available: {names}", ephemeral=True)
            return

        entrants = [int(uid) for uid in data.get("entrants", [])]
        excluded = {
            int(uid)
            for pname, wlist in data["winners"].items()
            if pname.lower() != prize_name.lower()
            for uid in wlist
        }
        pool = [uid for uid in entrants if uid not in excluded]
        qty = min(prize_obj["quantity"], len(pool))
        new_winners = random.sample(pool, qty) if qty > 0 else []

        data["winners"][prize_obj["name"]] = [str(uid) for uid in new_winners]
        await self.db_set(giveaway_id, data)

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

    @giveaway.command(name="dm", description="DM every winner to contact the host for their reward")
    @is_mod()
    @discord.app_commands.describe(giveaway_id="The giveaway ID shown in the embed footer")
    async def giveaway_dm(self, interaction: discord.Interaction, giveaway_id: str):
        await interaction.response.defer(ephemeral=True)
        data = await self.db_get(giveaway_id)

        if not data:
            await interaction.followup.send("❌ Giveaway not found.", ephemeral=True)
            return
        if not data.get("ended"):
            await interaction.followup.send("❌ That giveaway hasn't ended yet.", ephemeral=True)
            return

        winners = data.get("winners", {})
        all_winner_ids = [(prize, uid) for prize in data["prizes"] for uid in winners.get(prize["name"], [])]

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

    @giveaway.command(name="editwinner", description="Quietly swap or remove a winner without any public announcement")
    @is_mod()
    @discord.app_commands.describe(
        giveaway_id="The giveaway ID shown in the embed footer",
        prize_name="Prize tier the winner is in (e.g. Gold, Silver)",
        remove="User ID of the winner to remove",
        replace_with="User ID to put in their place — leave blank to auto-draw from remaining entrants",
    )
    async def giveaway_editwinner(
        self,
        interaction: discord.Interaction,
        giveaway_id: str,
        prize_name: str,
        remove: str,
        replace_with: str | None = None,
    ):
        await interaction.response.defer(ephemeral=True)
        data = await self.db_get(giveaway_id)

        if not data:
            await interaction.followup.send("❌ Giveaway not found.", ephemeral=True)
            return
        if not data.get("ended"):
            await interaction.followup.send("❌ That giveaway hasn't ended yet.", ephemeral=True)
            return

        prize_obj = next((p for p in data["prizes"] if p["name"].lower() == prize_name.lower()), None)
        if prize_obj is None:
            names = ", ".join(p["name"] for p in data["prizes"])
            await interaction.followup.send(f"❌ Prize tier not found. Available: {names}", ephemeral=True)
            return

        winners: list[str] = list(data.get("winners", {}).get(prize_obj["name"], []))
        if remove not in winners:
            await interaction.followup.send(f"❌ `{remove}` is not listed as a winner for **{prize_obj['name']}**.", ephemeral=True)
            return

        winners.remove(remove)

        if replace_with:
            all_other_winners = {uid for pname, wlist in data["winners"].items() if pname != prize_obj["name"] for uid in wlist}
            if replace_with in all_other_winners:
                await interaction.followup.send(f"❌ <@{replace_with}> already won a different prize tier.", ephemeral=True)
                return
            winners.append(replace_with)
            result_msg = f"✅ Replaced <@{remove}> with <@{replace_with}> in **{prize_obj['name']}**. Only you can see this."
        else:
            all_current_winners = {uid for pname, wlist in data["winners"].items() if pname != prize_obj["name"] for uid in wlist} | set(winners)
            pool = [uid for uid in data.get("entrants", []) if uid not in all_current_winners and uid != remove]
            if pool:
                new_uid = random.choice(pool)
                winners.append(new_uid)
                result_msg = f"✅ Removed <@{remove}> from **{prize_obj['name']}** and auto-drew <@{new_uid}> as replacement. Only you can see this."
            else:
                result_msg = f"✅ Removed <@{remove}> from **{prize_obj['name']}**. No eligible entrants left to fill the slot. Only you can see this."

        data["winners"][prize_obj["name"]] = winners
        await self.db_set(giveaway_id, data)
        await interaction.followup.send(result_msg, ephemeral=True)

    @giveaway.command(name="cancel", description="Cancel an active giveaway and delete the embed — no winners drawn")
    @is_mod()
    @discord.app_commands.describe(giveaway_id="The giveaway ID shown in the embed footer")
    async def giveaway_cancel(self, interaction: discord.Interaction, giveaway_id: str):
        await interaction.response.defer(ephemeral=True)
        data = await self.db_get(giveaway_id)

        if not data:
            await interaction.followup.send("❌ Giveaway not found.", ephemeral=True)
            return
        if data.get("ended"):
            await interaction.followup.send("❌ That giveaway has already ended.", ephemeral=True)
            return

        task = self.active_tasks.pop(giveaway_id, None)
        if task:
            task.cancel()

        guild = self.bot.get_guild(data["guild_id"])
        channel = guild.get_channel(data["channel_id"]) if guild else None
        if channel and isinstance(channel, discord.TextChannel):
            try:
                message = await channel.fetch_message(data["message_id"])
                await message.delete()
            except (discord.NotFound, discord.HTTPException):
                pass

        await self.db_delete(giveaway_id)
        await interaction.followup.send(f"✅ Giveaway **{data['title']}** has been cancelled and the embed deleted.", ephemeral=True)

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
        data = await self.db_get(giveaway_id)

        if not data:
            await interaction.followup.send("❌ Giveaway not found.", ephemeral=True)
            return
        if data.get("ended"):
            await interaction.followup.send("❌ That giveaway has already ended and can't be edited.", ephemeral=True)
            return

        changes: list[str] = []

        if title:
            data["title"] = title
            changes.append(f"title → **{title}**")

        if extend:
            delta = parse_duration(extend)
            if delta is None:
                await interaction.followup.send("❌ Invalid duration. Use e.g. `30m`, `2h`, `1d`.", ephemeral=True)
                return
            data["end_time"] = data["end_time"] + delta.total_seconds()
            end_ts = int(data["end_time"])
            changes.append(f"end time extended → <t:{end_ts}:f>")
            old_task = self.active_tasks.pop(giveaway_id, None)
            if old_task:
                old_task.cancel()
            now = datetime.datetime.now(datetime.timezone.utc).timestamp()
            new_delay = max(0.0, data["end_time"] - now)
            self.active_tasks[giveaway_id] = asyncio.create_task(
                self._end_giveaway(giveaway_id, delay=new_delay)
            )

        for idx, (new_name, new_desc) in enumerate([(prize1_name, prize1_description), (prize2_name, prize2_description), (prize3_name, prize3_description)]):
            if idx >= len(data["prizes"]):
                break
            if new_name:
                old = data["prizes"][idx]["name"]
                data["prizes"][idx]["name"] = new_name
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

        await self.db_set(giveaway_id, data)

        guild = self.bot.get_guild(data["guild_id"])
        channel = guild.get_channel(data["channel_id"]) if guild else None
        if channel and isinstance(channel, discord.TextChannel):
            try:
                message = await channel.fetch_message(data["message_id"])
                await message.edit(embed=self._static_build_active_embed(data))
            except (discord.NotFound, discord.HTTPException):
                pass

        summary = "\n".join(f"• {c}" for c in changes)
        await interaction.followup.send(f"✅ Giveaway updated:\n{summary}", ephemeral=True)

    @giveaway.command(name="entrants", description="See everyone who entered a giveaway — ephemeral")
    @is_mod()
    @discord.app_commands.describe(
        giveaway_id="The giveaway ID shown in the embed footer",
        search="Check if a specific user ID is in the pool",
    )
    async def giveaway_entrants(self, interaction: discord.Interaction, giveaway_id: str, search: str | None = None):
        await interaction.response.defer(ephemeral=True)
        data = await self.db_get(giveaway_id)

        if not data:
            await interaction.followup.send("❌ Giveaway not found.", ephemeral=True)
            return

        entrants: list[str] = data.get("entrants", [])

        if search:
            if search in entrants:
                await interaction.followup.send(f"✅ `{search}` (<@{search}>) **is** in the entrant pool.", ephemeral=True)
            else:
                await interaction.followup.send(f"❌ `{search}` is **not** in the entrant pool.", ephemeral=True)
            return

        total = len(entrants)
        if total == 0:
            await interaction.followup.send("No one has entered this giveaway yet.", ephemeral=True)
            return

        pages: list[str] = []
        for i in range(0, total, 50):
            pages.append(" ".join(f"<@{uid}>" for uid in entrants[i:i + 50]))

        embed = discord.Embed(
            title=f"👥  Entrants — {data['title']}",
            description=pages[0],
            color=discord.Color.blurple(),
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.set_footer(
            text=f"Total: {total} entr{'y' if total == 1 else 'ies'}"
            + (f"  •  Page 1/{len(pages)}" if len(pages) > 1 else "")
            + f"  •  ID: {giveaway_id}"
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

        for idx, page in enumerate(pages[1:], start=2):
            overflow = discord.Embed(description=page, color=discord.Color.blurple())
            overflow.set_footer(text=f"Page {idx}/{len(pages)}  •  ID: {giveaway_id}")
            await interaction.followup.send(embed=overflow, ephemeral=True)

    @giveaway.command(name="restore", description="Manually restore a past giveaway with known winners — owner only")
    @is_mod()
    @discord.app_commands.describe(
        giveaway_id="The original message ID of the giveaway",
        title="Giveaway title",
        prize1_name="First prize tier name",
        prize1_description="First prize description",
        prize1_winners="Winner IDs for prize 1, separated by spaces or commas",
        prize2_name="Second prize tier name (optional)",
        prize2_description="Second prize description (optional)",
        prize2_winners="Winner IDs for prize 2, separated by spaces or commas",
        prize3_name="Third prize tier name (optional)",
        prize3_description="Third prize description (optional)",
        prize3_winners="Winner IDs for prize 3, separated by spaces or commas",
    )
    async def giveaway_restore(
        self,
        interaction: discord.Interaction,
        giveaway_id: str,
        title: str,
        prize1_name: str,
        prize1_description: str,
        prize1_winners: str,
        prize2_name: str | None = None,
        prize2_description: str | None = None,
        prize2_winners: str | None = None,
        prize3_name: str | None = None,
        prize3_description: str | None = None,
        prize3_winners: str | None = None,
    ):
        await interaction.response.defer(ephemeral=True)

        def parse_ids(raw: str | None) -> list[str]:
            if not raw:
                return []
            return [uid.strip().strip("<@>") for uid in raw.replace(",", " ").split() if uid.strip()]

        prizes = []
        winners: dict[str, list[str]] = {}
        all_entrants: list[str] = []

        for name, desc, raw_winners in [
            (prize1_name, prize1_description, prize1_winners),
            (prize2_name, prize2_description, prize2_winners),
            (prize3_name, prize3_description, prize3_winners),
        ]:
            if not name or not desc:
                continue
            ids = parse_ids(raw_winners)
            prizes.append({"name": name, "description": desc, "quantity": len(ids) or 1})
            winners[name] = ids
            all_entrants.extend(uid for uid in ids if uid not in all_entrants)

        existing = await self.db_get(giveaway_id)
        if existing:
            await interaction.followup.send(
                f"❌ A giveaway with ID `{giveaway_id}` already exists. Use `/giveaway editwinner` to change winners.",
                ephemeral=True,
            )
            return

        data: dict = {
            "giveaway_id": giveaway_id,
            "guild_id": interaction.guild.id,
            "channel_id": interaction.channel.id,
            "message_id": int(giveaway_id),
            "title": title,
            "prizes": prizes,
            "end_time": datetime.datetime.now(datetime.timezone.utc).timestamp(),
            "required_role": None,
            "hosted_by": interaction.user.id,
            "hosted_by_name": str(interaction.user),
            "entrants": all_entrants,
            "ended": True,
            "announced": True,
            "winners": winners,
            "footer_text": None,
            "footer_icon": None,
            "image": None,
        }

        await self.db_set(giveaway_id, data)

        # Build a summary embed to confirm what was saved
        embed = discord.Embed(
            title=f"✅  Restored — {title}",
            color=discord.Color.green(),
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        for prize in prizes:
            prize_winners = winners.get(prize["name"], [])
            value = "\n".join(f"<@{uid}> (`{uid}`)" for uid in prize_winners) or "*none*"
            embed.add_field(name=f"{prize['name']} — {prize['description']}", value=value, inline=False)
        embed.set_footer(text=f"ID: {giveaway_id}  •  Only visible to you")
        await interaction.followup.send(
            "Giveaway restored. Use `/giveaway winners` or `/giveaway dm` with the ID below.",
            embed=embed,
            ephemeral=True,
        )

    @giveaway.command(name="list", description="List all active giveaways in this server")
    @is_mod()
    async def giveaway_list(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("❌ Server only.", ephemeral=True)
            return

        all_data = await self.db_all_for_guild(interaction.guild.id)
        active = [d for d in all_data if not d.get("ended")]

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

    async def cog_app_command_error(self, interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
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

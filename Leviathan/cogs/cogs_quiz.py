import json
import os
import re
import asyncio
import random
import discord
from discord import app_commands
from discord.ext import commands
from groq import AsyncGroq

QUESTIONS_FILE = "data/quiz_questions.json"
SCORES_FILE    = "data/quiz_scores.json"
MAX_PER_CAT    = 500
DEFAULT_TIMER  = 20
LOBBY_TIMER    = 60
OWNER_ID       = 1136231768534569090

MODE_ALL   = "all"
MODE_FIRST = "first"
MODE_ZERO  = "zero"
MODE_VS    = "vs"

# ── persistence ────────────────────────────────────────────────────────────────

def _load_q() -> dict:
    if os.path.exists(QUESTIONS_FILE):
        with open(QUESTIONS_FILE) as f:
            return json.load(f)
    return {}

def _save_q(data: dict):
    os.makedirs("data", exist_ok=True)
    with open(QUESTIONS_FILE, "w") as f:
        json.dump(data, f, indent=2)

def _load_scores() -> dict:
    if os.path.exists(SCORES_FILE):
        with open(SCORES_FILE) as f:
            return json.load(f)
    return {}

def _save_scores(data: dict):
    os.makedirs("data", exist_ok=True)
    with open(SCORES_FILE, "w") as f:
        json.dump(data, f, indent=2)

def _next_id(pool: list) -> int:
    return max((q["id"] for q in pool), default=0) + 1

def _q_type(q: dict) -> str:
    return q.get("type", "mc")

def _q_points(q: dict) -> int:
    return q.get("points", 1)

# ── zero-mode question selection ───────────────────────────────────────────────

def _select_zero_questions(pool: list, target: int) -> list | None:
    """Pick a subset of questions whose points sum to exactly target."""
    twos = [q for q in pool if _q_points(q) == 2]
    ones = [q for q in pool if _q_points(q) != 2]
    random.shuffle(twos)
    random.shuffle(ones)
    for use_twos in range(min(len(twos), target // 2), -1, -1):
        need_ones = target - use_twos * 2
        if 0 <= need_ones <= len(ones):
            selected = twos[:use_twos] + ones[:need_ones]
            random.shuffle(selected)
            return selected
    return None

# ── MC answer buttons ──────────────────────────────────────────────────────────

class AnswerView(discord.ui.View):
    def __init__(self, session: dict, q: dict):
        super().__init__(timeout=session["timer"])
        self.session       = session
        self.q             = q
        self.first_correct = asyncio.Event()

    async def _handle(self, interaction: discord.Interaction, choice: str):
        uid = interaction.user.id
        if uid not in self.session["players"]:
            await interaction.response.send_message(
                "❌ You're not registered. Ask the owner to add you.", ephemeral=True)
            return
        if uid in self.session["answered"]:
            await interaction.response.send_message("✅ Already answered!", ephemeral=True)
            return
        self.session["answered"].add(uid)
        correct = self.q["answer"].upper()
        pts     = _q_points(self.q)
        if choice == correct:
            self.session["scores"][uid] = self.session["scores"].get(uid, 0) + pts
            if self.session.get("mode") == MODE_ZERO:
                self.session["zero_remaining"] -= pts
            await interaction.response.send_message(f"✅ **Correct!** +{pts} pt(s)", ephemeral=True)
            if self.session.get("mode") in (MODE_FIRST, MODE_VS):
                self.first_correct.set()
                self.stop()
        else:
            cor_t = self.q.get(correct.lower(), correct)
            await interaction.response.send_message(
                f"❌ **Wrong!** Answer: **{correct}** — {cor_t}", ephemeral=True)

    @discord.ui.button(label="A", style=discord.ButtonStyle.primary)
    async def btn_a(self, i: discord.Interaction, b: discord.ui.Button):
        await self._handle(i, "A")

    @discord.ui.button(label="B", style=discord.ButtonStyle.primary)
    async def btn_b(self, i: discord.Interaction, b: discord.ui.Button):
        await self._handle(i, "B")

    @discord.ui.button(label="C", style=discord.ButtonStyle.primary)
    async def btn_c(self, i: discord.Interaction, b: discord.ui.Button):
        await self._handle(i, "C")

    @discord.ui.button(label="D", style=discord.ButtonStyle.primary)
    async def btn_d(self, i: discord.Interaction, b: discord.ui.Button):
        await self._handle(i, "D")

# ── bulk add modal ─────────────────────────────────────────────────────────────

class BulkAddModal(discord.ui.Modal, title="Bulk Add Quiz Questions"):
    body = discord.ui.TextInput(
        label="One question per line — /quiz guide for format",
        style=discord.TextStyle.paragraph,
        placeholder=(
            "MC:    mc | Question | A | B | C | D | Answer | Category | Points\n"
            "Typed: typed | Question | Ans1;Ans2 | Category | Points\n\n"
            "mc | What is Luffy's power? | Rubber | Fire | Ice | Speed | A | anime | 1\n"
            "typed | What country is Tokyo in? | Japan | general | 1"
        ),
        required=True,
        max_length=4000,
    )

    def __init__(self, cog: "QuizCog"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        data         = _load_q()
        added, errors = 0, []

        for idx, line in enumerate(self.body.value.strip().splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split("|")]
            qtype = parts[0].lower() if parts else ""

            if qtype == "mc":
                if len(parts) != 9:
                    errors.append(f"Line {idx} (mc): need 9 fields, got {len(parts)}")
                    continue
                _, q_text, a, b, c, d, ans, cat, pts_s = parts
                ans = ans.upper()
                if ans not in ("A", "B", "C", "D"):
                    errors.append(f"Line {idx}: answer must be A/B/C/D, got '{ans}'")
                    continue
                try:
                    pts = max(1, min(2, int(pts_s)))
                except ValueError:
                    pts = 1
                cat = cat.lower()
                data.setdefault(cat, [])
                if len(data[cat]) >= MAX_PER_CAT:
                    errors.append(f"Line {idx}: '{cat}' is full (500 max)")
                    continue
                data[cat].append({
                    "id": _next_id(data[cat]), "type": "mc",
                    "question": q_text,
                    "a": a, "b": b, "c": c, "d": d,
                    "answer": ans, "category": cat, "points": pts,
                })
                added += 1

            elif qtype == "typed":
                if len(parts) != 5:
                    errors.append(f"Line {idx} (typed): need 5 fields, got {len(parts)}")
                    continue
                _, q_text, answers_raw, cat, pts_s = parts
                answers = [a.strip() for a in answers_raw.split(";") if a.strip()]
                if not answers:
                    errors.append(f"Line {idx}: no answers provided")
                    continue
                try:
                    pts = max(1, min(2, int(pts_s)))
                except ValueError:
                    pts = 1
                cat = cat.lower()
                data.setdefault(cat, [])
                if len(data[cat]) >= MAX_PER_CAT:
                    errors.append(f"Line {idx}: '{cat}' is full (500 max)")
                    continue
                data[cat].append({
                    "id": _next_id(data[cat]), "type": "typed",
                    "question": q_text,
                    "answers": answers,
                    "category": cat, "points": pts,
                })
                added += 1

            elif len(parts) == 7:
                # Backward-compat: old format with no type prefix
                q_text, a, b, c, d, ans, cat = parts
                ans = ans.upper()
                if ans not in ("A", "B", "C", "D"):
                    errors.append(f"Line {idx}: answer must be A/B/C/D")
                    continue
                cat = cat.lower()
                data.setdefault(cat, [])
                if len(data[cat]) >= MAX_PER_CAT:
                    errors.append(f"Line {idx}: '{cat}' is full")
                    continue
                data[cat].append({
                    "id": _next_id(data[cat]), "type": "mc",
                    "question": q_text,
                    "a": a, "b": b, "c": c, "d": d,
                    "answer": ans, "category": cat, "points": 1,
                })
                added += 1

            else:
                errors.append(f"Line {idx}: unknown format — start with 'mc' or 'typed'")

        _save_q(data)
        msg = f"✅ Added **{added}** question(s)."
        if errors:
            msg += "\n⚠️ Errors:\n" + "\n".join(errors[:8])
        await interaction.followup.send(msg, ephemeral=True)

# ── cog ────────────────────────────────────────────────────────────────────────

class QuizCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot      = bot
        self.groq     = AsyncGroq(api_key=os.environ["GROQ_API_KEY"])
        self.sessions: dict[int, dict] = {}

    quiz = app_commands.Group(name="quiz", description="Quiz system")

    # ── internal helpers ───────────────────────────────────────────────────────

    def _is_owner(self, i: discord.Interaction) -> bool:
        return i.user.id == OWNER_ID

    def _member_name(self, guild, uid: int) -> str:
        m = guild.get_member(uid) if guild else None
        return m.display_name if m else str(uid)

    def _lobby_embed(self, s: dict) -> discord.Embed:
        guild = self.bot.get_guild(s["guild_id"])
        names = [self._member_name(guild, uid) for uid in s["players"]]
        mode_labels = {
            MODE_ALL:   "All to Answer",
            MODE_FIRST: "First to Answer",
            MODE_ZERO:  f"Road to Zero ({s.get('zero_target', '?')} pts)",
            MODE_VS:    "1v1 VS",
        }
        e = discord.Embed(title="🎮 Quiz Lobby", color=discord.Color.blurple())
        e.add_field(name="Category",  value=s["category"].title(), inline=True)
        e.add_field(name="Mode",      value=mode_labels.get(s["mode"], s["mode"]), inline=True)
        e.add_field(name="Questions", value=str(s["count"]), inline=True)
        e.add_field(name="Timer",     value=f"{s['timer']}s / question", inline=True)
        e.add_field(
            name=f"Players ({len(names)})",
            value="\n".join(f"• {n}" for n in names) or "None yet",
            inline=False,
        )
        state = "🟡 Lobby open — `/quiz join` to register" if s["state"] == "lobby" else "🟢 Running"
        e.set_footer(text=f"{state} • Auto-starts in 60s • /quiz begin to start now")
        return e

    # ── lobby countdown ────────────────────────────────────────────────────────

    async def _lobby_countdown(self, session: dict):
        channel = self.bot.get_channel(session["channel_id"])
        await asyncio.sleep(30)
        if session.get("ended") or session["state"] != "lobby":
            return
        if channel:
            await channel.send("⏱ **30 seconds** left to join! Type `/quiz join`")
        await asyncio.sleep(20)
        if session.get("ended") or session["state"] != "lobby":
            return
        if channel:
            await channel.send("⏱ **10 seconds** left!")
        await asyncio.sleep(10)
        if session.get("ended") or session["state"] != "lobby":
            return
        if not session["players"]:
            if channel:
                await channel.send("❌ No players joined — quiz cancelled.")
            self.sessions.pop(session["guild_id"], None)
            return
        session["state"] = "running"
        if channel:
            await channel.send("🚀 Lobby timer ended — starting quiz now!")
        await asyncio.sleep(2)
        asyncio.create_task(self._run_session(session))

    # ── question runners ───────────────────────────────────────────────────────

    async def _ask_mc(self, session: dict, q: dict, channel, num: int, total: int):
        view  = AnswerView(session, q)
        pts   = _q_points(q)
        mode  = session["mode"]
        timer = session["timer"]
        badge = "🔥 " if pts == 2 else ""

        e = discord.Embed(
            title=f"❓ {badge}Question {num}/{total}  [{pts} pt{'s' if pts>1 else ''}]",
            description=(
                f"**{q['question']}**\n\n"
                f"🅰  {q['a']}\n"
                f"🅱  {q['b']}\n"
                f"🅲  {q['c']}\n"
                f"🅳  {q['d']}"
            ),
            color=discord.Color.gold(),
        )
        mode_hint = "First correct answer wins!" if mode in (MODE_FIRST, MODE_VS) else "Everyone gets one chance"
        e.set_footer(text=f"⏱ {timer}s • {mode_hint} • {session['category'].title()}")
        await channel.send(embed=e, view=view)

        if mode in (MODE_FIRST, MODE_VS):
            try:
                await asyncio.wait_for(view.first_correct.wait(), timeout=timer)
            except asyncio.TimeoutError:
                pass
            view.stop()
        else:
            await asyncio.sleep(timer)
            view.stop()

    async def _ask_typed(self, session: dict, q: dict, channel, num: int, total: int):
        correct_set = {a.lower() for a in q.get("answers", [])}
        pts         = _q_points(q)
        mode        = session["mode"]
        timer       = session["timer"]
        badge       = "🔥 " if pts == 2 else ""

        e = discord.Embed(
            title=f"✏️ {badge}Question {num}/{total}  [{pts} pt{'s' if pts>1 else ''}]",
            description=f"**{q['question']}**\n\n*Type your answer in the chat!*",
            color=discord.Color.orange(),
        )
        e.set_footer(text=f"⏱ {timer}s • Typed answer • Exact match required • {session['category'].title()}")
        await channel.send(embed=e)

        deadline = asyncio.get_event_loop().time() + timer

        def _make_check(pending: set):
            def check(m: discord.Message):
                return m.channel.id == channel.id and m.author.id in pending
            return check

        if mode in (MODE_FIRST, MODE_VS):
            while asyncio.get_event_loop().time() < deadline:
                remaining  = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    break
                unanswered = {uid for uid in session["players"] if uid not in session["answered"]}
                if not unanswered:
                    break
                try:
                    msg = await asyncio.wait_for(
                        self.bot.wait_for("message", check=_make_check(unanswered)),
                        timeout=remaining,
                    )
                    session["answered"].add(msg.author.id)
                    if msg.content.strip().lower() in correct_set:
                        session["scores"][msg.author.id] = session["scores"].get(msg.author.id, 0) + pts
                        if mode == MODE_ZERO:
                            session["zero_remaining"] -= pts
                        await msg.add_reaction("✅")
                        break
                    else:
                        await msg.add_reaction("❌")
                except asyncio.TimeoutError:
                    break
        else:
            while asyncio.get_event_loop().time() < deadline:
                remaining  = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    break
                unanswered = {uid for uid in session["players"] if uid not in session["answered"]}
                if not unanswered:
                    break
                try:
                    msg = await asyncio.wait_for(
                        self.bot.wait_for("message", check=_make_check(unanswered)),
                        timeout=min(remaining, 1.5),
                    )
                    session["answered"].add(msg.author.id)
                    if msg.content.strip().lower() in correct_set:
                        session["scores"][msg.author.id] = session["scores"].get(msg.author.id, 0) + pts
                        await msg.add_reaction("✅")
                    else:
                        await msg.add_reaction("❌")
                except asyncio.TimeoutError:
                    continue

    async def _reveal(self, session: dict, q: dict, channel, guild):
        qtype = _q_type(q)
        if qtype == "mc":
            cor_l = q["answer"].upper()
            cor_t = q.get(cor_l.lower(), "")
            title = f"✅ Answer: **{cor_l}** — {cor_t}"
        else:
            alts  = " / ".join(q.get("answers", []))
            title = f"✅ Answer: {alts}"

        rev = discord.Embed(title=title, color=discord.Color.green())

        if session["mode"] == MODE_ZERO:
            rem = session.get("zero_remaining", 0)
            rev.add_field(name="Points left to zero", value=f"**{rem}**", inline=True)
        else:
            lines = []
            for uid in session["players"]:
                name = self._member_name(guild, uid)
                pts  = session["scores"].get(uid, 0)
                tick = "✅" if uid in session["answered"] else "—"
                lines.append(f"{tick} {name} · **{pts} pts**")
            if lines:
                rev.add_field(name="Scores so far", value="\n".join(lines), inline=False)

        await channel.send(embed=rev)
        await asyncio.sleep(3)

    # ── main session runner ────────────────────────────────────────────────────

    async def _run_session(self, session: dict):
        channel = self.bot.get_channel(session["channel_id"])
        guild   = self.bot.get_guild(session["guild_id"])
        if not channel:
            return

        for idx, q in enumerate(session["questions"]):
            if session.get("ended"):
                break
            session["current_q"] = idx
            session["answered"]  = set()

            num = idx + 1
            if _q_type(q) == "mc":
                await self._ask_mc(session, q, channel, num, len(session["questions"]))
            else:
                await self._ask_typed(session, q, channel, num, len(session["questions"]))

            await self._reveal(session, q, channel, guild)

            if session["mode"] == MODE_ZERO and session.get("zero_remaining", 1) <= 0:
                uid  = next(iter(session["players"]))
                name = self._member_name(guild, uid)
                await channel.send(f"🎉 **{name} reached ZERO!** You win!")
                break

        await self._finish(session, channel)

    async def _finish(self, session: dict, channel=None):
        session["ended"] = True
        gid   = session["guild_id"]
        guild = self.bot.get_guild(gid)
        ch    = channel or self.bot.get_channel(session["channel_id"])

        sdata = _load_scores()
        gkey  = str(gid)
        sdata.setdefault(gkey, {})
        for uid, pts in session["scores"].items():
            ukey = str(uid)
            name = self._member_name(guild, uid)
            sdata[gkey].setdefault(ukey, {"name": name, "points": 0, "correct": 0, "played": 0})
            sdata[gkey][ukey]["points"]  += pts
            sdata[gkey][ukey]["correct"] += pts
            sdata[gkey][ukey]["played"]  += len(session["questions"])
            sdata[gkey][ukey]["name"]     = name
        _save_scores(sdata)

        if ch:
            medals = ["🥇", "🥈", "🥉"]
            ranked = sorted(session["scores"].items(), key=lambda x: x[1], reverse=True)
            lines  = []
            for rank, (uid, pts) in enumerate(ranked):
                name = self._member_name(guild, uid)
                icon = medals[rank] if rank < 3 else f"**{rank+1}.**"
                lines.append(f"{icon} {name} — {pts} pts")
            e = discord.Embed(
                title="🏆 Final Results",
                description="\n".join(lines) or "No scores.",
                color=discord.Color.gold(),
            )
            await ch.send(embed=e)

        self.sessions.pop(gid, None)

    # ── /quiz guide ────────────────────────────────────────────────────────────

    @quiz.command(name="guide", description="Show the bulk-add format guide")
    async def quiz_guide(self, i: discord.Interaction):
        e = discord.Embed(title="📖 Bulk Add Format Guide", color=discord.Color.blurple())
        e.add_field(
            name="Multiple Choice (mc)",
            value=(
                "```mc | Question | A | B | C | D | Answer | Category | Points```\n"
                "• **Answer** = A, B, C, or D\n"
                "• **Points** = 1 (normal) or 2 (hard/bonus)\n\n"
                "**Example:**\n"
                "```mc | What is Luffy's Devil Fruit? | Gum-Gum | Fire-Fire | Ice-Ice | Dark-Dark | A | anime | 1```"
            ),
            inline=False,
        )
        e.add_field(
            name="Typed Answer (typed)",
            value=(
                "```typed | Question | Answer1;Answer2;Answer3 | Category | Points```\n"
                "• Separate valid answers with **;**\n"
                "• Exact match only — no typos\n"
                "• Use alternatives for different correct phrasings\n\n"
                "**Example:**\n"
                "```typed | What country is Tokyo in? | Japan;Japan (Asia) | general | 1```"
            ),
            inline=False,
        )
        e.add_field(
            name="Tips",
            value=(
                "• One question per line\n"
                "• Max **500** questions per category\n"
                "• Points 1 = normal, 2 = hard (used in Road to Zero)\n"
                "• Categories are case-insensitive\n"
                "• Lines starting with # are ignored (comments)\n"
                "• Blank lines are ignored"
            ),
            inline=False,
        )
        await i.response.send_message(embed=e, ephemeral=True)

    # ── /quiz start ────────────────────────────────────────────────────────────

    @quiz.command(name="start", description="Open a quiz lobby (owner only)")
    @app_commands.describe(
        category="Category to pull questions from",
        count="Number of questions (1–20)",
        mode="Game mode",
        timer="Seconds per question (default 20)",
    )
    @app_commands.choices(mode=[
        app_commands.Choice(name="All to Answer",   value=MODE_ALL),
        app_commands.Choice(name="First to Answer", value=MODE_FIRST),
    ])
    async def quiz_start(self, i: discord.Interaction, category: str, count: int = 10,
                         mode: str = MODE_ALL, timer: int = DEFAULT_TIMER):
        if not self._is_owner(i):
            return await i.response.send_message("❌ Owner only.", ephemeral=True)
        gid = i.guild_id
        if gid in self.sessions:
            return await i.response.send_message("❌ Quiz already running. `/quiz end` first.", ephemeral=True)
        count = max(1, min(20, count))
        timer = max(10, min(120, timer))
        cat   = category.lower()
        pool  = _load_q().get(cat, [])
        if not pool:
            return await i.response.send_message(
                f"❌ No questions in **{cat}**. Use `/quiz bulkadd` to add some.", ephemeral=True)
        questions = random.sample(pool, min(count, len(pool)))
        session = {
            "guild_id":   gid,
            "channel_id": i.channel_id,
            "category":   cat,
            "mode":       mode,
            "timer":      timer,
            "count":      len(questions),
            "questions":  questions,
            "players":    set(),
            "scores":     {},
            "current_q":  0,
            "answered":   set(),
            "state":      "lobby",
            "ended":      False,
        }
        self.sessions[gid] = session
        await i.response.send_message(embed=self._lobby_embed(session))
        asyncio.create_task(self._lobby_countdown(session))

    # ── /quiz vs ───────────────────────────────────────────────────────────────

    @quiz.command(name="vs", description="1v1 VS quiz — two players, two categories (owner only)")
    @app_commands.describe(
        player1="First player",  category1="Category for player 1",
        player2="Second player", category2="Category for player 2",
        count="Questions per category (1–10, default 5)",
        timer="Seconds per question (default 20)",
    )
    async def quiz_vs(self, i: discord.Interaction,
                      player1: discord.Member, category1: str,
                      player2: discord.Member, category2: str,
                      count: int = 5, timer: int = DEFAULT_TIMER):
        if not self._is_owner(i):
            return await i.response.send_message("❌ Owner only.", ephemeral=True)
        gid = i.guild_id
        if gid in self.sessions:
            return await i.response.send_message("❌ Quiz already running.", ephemeral=True)
        count = max(1, min(10, count))
        timer = max(10, min(120, timer))
        data  = _load_q()
        cat1, cat2 = category1.lower(), category2.lower()
        pool1, pool2 = data.get(cat1, []), data.get(cat2, [])
        if not pool1:
            return await i.response.send_message(f"❌ No questions in **{cat1}**.", ephemeral=True)
        if not pool2:
            return await i.response.send_message(f"❌ No questions in **{cat2}**.", ephemeral=True)
        questions = random.sample(pool1, min(count, len(pool1))) + \
                    random.sample(pool2, min(count, len(pool2)))
        random.shuffle(questions)
        session = {
            "guild_id":   gid,
            "channel_id": i.channel_id,
            "category":   f"{cat1} + {cat2}",
            "mode":       MODE_VS,
            "timer":      timer,
            "count":      len(questions),
            "questions":  questions,
            "players":    {player1.id, player2.id},
            "scores":     {player1.id: 0, player2.id: 0},
            "current_q":  0,
            "answered":   set(),
            "state":      "running",
            "ended":      False,
        }
        self.sessions[gid] = session
        await i.response.send_message(
            f"⚔️ **VS Quiz!** {player1.mention} vs {player2.mention}\n"
            f"**{cat1.title()}** + **{cat2.title()}** · {len(questions)} questions · {timer}s timer\n"
            f"Starting in **3 seconds**…"
        )
        await asyncio.sleep(3)
        asyncio.create_task(self._run_session(session))

    # ── /quiz zero ─────────────────────────────────────────────────────────────

    @quiz.command(name="zero", description="Road to Zero — solo mode (owner only)")
    @app_commands.describe(
        player="The solo player",
        category="Category to pull questions from",
        target="Starting point total — questions selected to sum to exactly this",
        timer="Seconds per question (default 20)",
    )
    async def quiz_zero(self, i: discord.Interaction, player: discord.Member,
                        category: str, target: int = 20, timer: int = DEFAULT_TIMER):
        if not self._is_owner(i):
            return await i.response.send_message("❌ Owner only.", ephemeral=True)
        gid = i.guild_id
        if gid in self.sessions:
            return await i.response.send_message("❌ Quiz already running.", ephemeral=True)
        timer = max(10, min(120, timer))
        cat   = category.lower()
        pool  = _load_q().get(cat, [])
        if not pool:
            return await i.response.send_message(f"❌ No questions in **{cat}**.", ephemeral=True)
        questions = _select_zero_questions(pool, target)
        if questions is None:
            return await i.response.send_message(
                f"❌ Not enough questions to reach exactly **{target}** points in **{cat}**.\n"
                f"Add more 1-point and 2-point questions then try again.", ephemeral=True)
        session = {
            "guild_id":       gid,
            "channel_id":     i.channel_id,
            "category":       cat,
            "mode":           MODE_ZERO,
            "timer":          timer,
            "count":          len(questions),
            "questions":      questions,
            "players":        {player.id},
            "scores":         {player.id: 0},
            "current_q":      0,
            "answered":       set(),
            "state":          "running",
            "ended":          False,
            "zero_target":    target,
            "zero_remaining": target,
        }
        self.sessions[gid] = session
        await i.response.send_message(
            f"🎯 **Road to Zero** — {player.mention}\n"
            f"Category: **{cat.title()}** · Target: **{target} pts** · "
            f"{len(questions)} questions · {timer}s timer\n"
            f"Answer every question correctly to reach zero!\n"
            f"Starting in **3 seconds**…"
        )
        await asyncio.sleep(3)
        asyncio.create_task(self._run_session(session))

    # ── /quiz ai ───────────────────────────────────────────────────────────────

    @quiz.command(name="ai", description="AI-generated quiz on any topic (owner only)")
    @app_commands.describe(
        topic="Topic to generate questions about",
        count="Number of questions (1–20)",
        difficulty="Difficulty level",
        mode="Game mode",
        timer="Seconds per question (default 20)",
    )
    @app_commands.choices(
        difficulty=[
            app_commands.Choice(name="Easy",       value="easy"),
            app_commands.Choice(name="Medium",     value="medium"),
            app_commands.Choice(name="Hard",       value="hard"),
            app_commands.Choice(name="Impossible", value="impossible"),
        ],
        mode=[
            app_commands.Choice(name="All to Answer",   value=MODE_ALL),
            app_commands.Choice(name="First to Answer", value=MODE_FIRST),
        ],
    )
    async def quiz_ai(self, i: discord.Interaction, topic: str, count: int = 5,
                      difficulty: str = "medium", mode: str = MODE_ALL,
                      timer: int = DEFAULT_TIMER):
        if not self._is_owner(i):
            return await i.response.send_message("❌ Owner only.", ephemeral=True)
        gid = i.guild_id
        if gid in self.sessions:
            return await i.response.send_message("❌ Quiz already running.", ephemeral=True)
        count = max(1, min(20, count))
        timer = max(10, min(120, timer))
        await i.response.send_message(
            f"🤖 Generating **{count}** {difficulty} questions about **{topic}**…")
        diff_guide = {
            "easy":       "very straightforward — a beginner would know these",
            "medium":     "moderate — requires some knowledge of the topic",
            "hard":       "challenging — requires solid expertise",
            "impossible": "extremely obscure — only a true expert would know",
        }
        prompt = (
            f"Generate exactly {count} multiple-choice quiz questions about: {topic}\n"
            f"Difficulty: {difficulty} ({diff_guide.get(difficulty, '')})\n"
            f"Assign points: easier questions within the set get 1 point, harder ones get 2 points.\n"
            f"Return ONLY a valid JSON array with no markdown or extra text.\n"
            f'Each item must be: {{"question": "...", "a": "...", "b": "...", "c": "...", "d": "...", '
            f'"answer": "A"|"B"|"C"|"D", "points": 1|2}}'
        )
        try:
            resp = await self.groq.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=2000,
                temperature=0.7,
            )
            raw = resp.choices[0].message.content.strip()
            raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.M).strip()
            questions = json.loads(raw)
            if not isinstance(questions, list) or not questions:
                raise ValueError("Empty or invalid response")
            for idx, q in enumerate(questions[:count]):
                q["id"]     = idx + 1
                q["type"]   = "mc"
                q["answer"] = q.get("answer", "A").upper()
                q["points"] = max(1, min(2, int(q.get("points", 1))))
        except Exception as err:
            return await i.edit_original_response(content=f"❌ Generation failed: {err}")
        session = {
            "guild_id":   gid,
            "channel_id": i.channel_id,
            "category":   f"AI · {topic}",
            "mode":       mode,
            "timer":      timer,
            "count":      len(questions[:count]),
            "questions":  questions[:count],
            "players":    set(),
            "scores":     {},
            "current_q":  0,
            "answered":   set(),
            "state":      "lobby",
            "ended":      False,
        }
        self.sessions[gid] = session
        await i.edit_original_response(content=None, embed=self._lobby_embed(session))
        asyncio.create_task(self._lobby_countdown(session))

    # ── /quiz join ─────────────────────────────────────────────────────────────

    @quiz.command(name="join", description="Register for the current quiz")
    async def quiz_join(self, i: discord.Interaction):
        s = self.sessions.get(i.guild_id)
        if not s:
            return await i.response.send_message("❌ No open quiz lobby.", ephemeral=True)
        if s["state"] != "lobby":
            return await i.response.send_message(
                "❌ Quiz already started — ask the owner to add you with `/quiz addplayer`.", ephemeral=True)
        uid = i.user.id
        if uid in s["players"]:
            return await i.response.send_message("✅ Already registered!", ephemeral=True)
        s["players"].add(uid)
        s["scores"][uid] = 0
        await i.response.send_message(f"✅ **{i.user.display_name}** joined!", embed=self._lobby_embed(s))

    # ── /quiz begin ────────────────────────────────────────────────────────────

    @quiz.command(name="begin", description="Start the quiz now, skipping the lobby timer (owner only)")
    async def quiz_begin(self, i: discord.Interaction):
        if not self._is_owner(i):
            return await i.response.send_message("❌ Owner only.", ephemeral=True)
        s = self.sessions.get(i.guild_id)
        if not s:
            return await i.response.send_message("❌ No lobby open.", ephemeral=True)
        if s["state"] != "lobby":
            return await i.response.send_message("❌ Already running.", ephemeral=True)
        if not s["players"]:
            return await i.response.send_message("❌ No players registered yet.", ephemeral=True)
        s["state"] = "running"
        await i.response.send_message("🚀 Starting quiz in **3 seconds**…")
        await asyncio.sleep(3)
        asyncio.create_task(self._run_session(s))

    # ── /quiz addplayer ────────────────────────────────────────────────────────

    @quiz.command(name="addplayer", description="Add a player (works in lobby or mid-quiz) — owner only")
    @app_commands.describe(member="Member to add")
    async def quiz_addplayer(self, i: discord.Interaction, member: discord.Member):
        if not self._is_owner(i):
            return await i.response.send_message("❌ Owner only.", ephemeral=True)
        s = self.sessions.get(i.guild_id)
        if not s:
            return await i.response.send_message("❌ No active quiz.", ephemeral=True)
        if member.id in s["players"]:
            return await i.response.send_message(f"✅ {member.display_name} is already in.", ephemeral=True)
        s["players"].add(member.id)
        s["scores"][member.id] = 0
        await i.response.send_message(f"✅ **{member.display_name}** added to the quiz.")

    # ── /quiz removeplayer ─────────────────────────────────────────────────────

    @quiz.command(name="removeplayer", description="Remove a player (works in lobby or mid-quiz) — owner only")
    @app_commands.describe(member="Member to remove")
    async def quiz_removeplayer(self, i: discord.Interaction, member: discord.Member):
        if not self._is_owner(i):
            return await i.response.send_message("❌ Owner only.", ephemeral=True)
        s = self.sessions.get(i.guild_id)
        if not s:
            return await i.response.send_message("❌ No active quiz.", ephemeral=True)
        s["players"].discard(member.id)
        s["scores"].pop(member.id, None)
        await i.response.send_message(f"✅ **{member.display_name}** removed.")

    # ── /quiz players ──────────────────────────────────────────────────────────

    @quiz.command(name="players", description="See who's in the current session")
    async def quiz_players(self, i: discord.Interaction):
        s = self.sessions.get(i.guild_id)
        if not s:
            return await i.response.send_message("❌ No active quiz.", ephemeral=True)
        await i.response.send_message(embed=self._lobby_embed(s))

    # ── /quiz end ──────────────────────────────────────────────────────────────

    @quiz.command(name="end", description="Force-end the quiz (owner only)")
    async def quiz_end(self, i: discord.Interaction):
        if not self._is_owner(i):
            return await i.response.send_message("❌ Owner only.", ephemeral=True)
        s = self.sessions.get(i.guild_id)
        if not s:
            return await i.response.send_message("❌ No active quiz.", ephemeral=True)
        await i.response.send_message("🛑 Ending quiz and tallying scores…")
        await self._finish(s, i.channel)

    # ── /quiz add ──────────────────────────────────────────────────────────────

    @quiz.command(name="add", description="Add one MC question (owner only)")
    @app_commands.describe(
        category="Category", question="Question text",
        a="Option A", b="Option B", c="Option C", d="Option D",
        answer="Correct letter: A / B / C / D",
        points="1 = normal, 2 = hard/bonus (default 1)",
    )
    async def quiz_add(self, i: discord.Interaction, category: str, question: str,
                       a: str, b: str, c: str, d: str, answer: str, points: int = 1):
        if not self._is_owner(i):
            return await i.response.send_message("❌ Owner only.", ephemeral=True)
        ans = answer.upper()
        if ans not in ("A", "B", "C", "D"):
            return await i.response.send_message("❌ Answer must be A, B, C, or D.", ephemeral=True)
        pts  = max(1, min(2, points))
        cat  = category.lower()
        data = _load_q()
        data.setdefault(cat, [])
        if len(data[cat]) >= MAX_PER_CAT:
            return await i.response.send_message(f"❌ **{cat}** is full (500 max).", ephemeral=True)
        qid = _next_id(data[cat])
        data[cat].append({
            "id": qid, "type": "mc",
            "question": question,
            "a": a, "b": b, "c": c, "d": d,
            "answer": ans, "category": cat, "points": pts,
        })
        _save_q(data)
        await i.response.send_message(
            f"✅ MC question **#{qid}** added to **{cat}** ({pts} pt).", ephemeral=True)

    # ── /quiz bulkadd ──────────────────────────────────────────────────────────

    @quiz.command(name="bulkadd", description="Paste many questions at once — run /quiz guide first (owner only)")
    async def quiz_bulkadd(self, i: discord.Interaction):
        if not self._is_owner(i):
            return await i.response.send_message("❌ Owner only.", ephemeral=True)
        await i.response.send_modal(BulkAddModal(self))

    # ── /quiz remove ───────────────────────────────────────────────────────────

    @quiz.command(name="remove", description="Remove a question by its ID (owner only)")
    @app_commands.describe(category="Category name", question_id="ID shown in /quiz list")
    async def quiz_remove(self, i: discord.Interaction, category: str, question_id: int):
        if not self._is_owner(i):
            return await i.response.send_message("❌ Owner only.", ephemeral=True)
        cat  = category.lower()
        data = _load_q()
        pool = data.get(cat, [])
        new  = [q for q in pool if q["id"] != question_id]
        if len(new) == len(pool):
            return await i.response.send_message(
                f"❌ No question **#{question_id}** in **{cat}**.", ephemeral=True)
        data[cat] = new
        _save_q(data)
        await i.response.send_message(
            f"✅ Question **#{question_id}** removed from **{cat}**.", ephemeral=True)

    # ── /quiz list ─────────────────────────────────────────────────────────────

    @quiz.command(name="list", description="View questions in a category with their IDs (owner only)")
    @app_commands.describe(category="Category name", page="Page number (20 per page)")
    async def quiz_list(self, i: discord.Interaction, category: str, page: int = 1):
        if not self._is_owner(i):
            return await i.response.send_message("❌ Owner only.", ephemeral=True)
        cat  = category.lower()
        pool = _load_q().get(cat, [])
        if not pool:
            return await i.response.send_message(f"❌ No questions in **{cat}**.", ephemeral=True)
        per_page    = 20
        total_pages = max(1, (len(pool) + per_page - 1) // per_page)
        page        = max(1, min(page, total_pages))
        chunk       = pool[(page - 1) * per_page: page * per_page]
        lines = []
        for q in chunk:
            pts  = _q_points(q)
            star = "🔥" if pts == 2 else "  "
            if _q_type(q) == "mc":
                lines.append(f"{star}**#{q['id']}** [{pts}pt] {q['question']}  *(Ans: {q.get('answer','?')})*")
            else:
                preview = "; ".join(q.get("answers", [])[:2])
                lines.append(f"{star}**#{q['id']}** [{pts}pt] ✏️ {q['question']}  *(Typed: {preview})*")
        e = discord.Embed(
            title=f"📋 {cat.title()} — Page {page}/{total_pages}  ({len(pool)}/500)",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        e.set_footer(text="🔥 = 2 pts  •  ✏️ = typed answer  •  /quiz remove to delete")
        await i.response.send_message(embed=e, ephemeral=True)

    # ── /quiz categories ───────────────────────────────────────────────────────

    @quiz.command(name="categories", description="See all quiz categories and counts")
    async def quiz_categories(self, i: discord.Interaction):
        data = _load_q()
        if not data:
            return await i.response.send_message("❌ No categories yet.", ephemeral=True)
        lines = []
        for cat, qs in sorted(data.items()):
            mc_c    = sum(1 for q in qs if _q_type(q) == "mc")
            typed_c = len(qs) - mc_c
            pts_tot = sum(_q_points(q) for q in qs)
            lines.append(
                f"**{cat.title()}** — {len(qs)} questions "
                f"({mc_c} MC · {typed_c} typed) · {pts_tot} total pts"
            )
        e = discord.Embed(
            title="📚 Quiz Categories",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        await i.response.send_message(embed=e)

    # ── /quiz leaderboard ──────────────────────────────────────────────────────

    @quiz.command(name="leaderboard", description="All-time quiz leaderboard")
    async def quiz_leaderboard(self, i: discord.Interaction):
        data   = _load_scores().get(str(i.guild_id), {})
        if not data:
            return await i.response.send_message("❌ No scores yet.", ephemeral=True)
        ranked = sorted(data.values(), key=lambda x: x["points"], reverse=True)
        medals = ["🥇", "🥈", "🥉"]
        lines  = []
        for rank, entry in enumerate(ranked[:15]):
            icon = medals[rank] if rank < 3 else f"**{rank+1}.**"
            pct  = round(entry["correct"] / entry["played"] * 100) if entry["played"] else 0
            lines.append(f"{icon} {entry['name']} — **{entry['points']} pts** ({pct}% accuracy)")
        e = discord.Embed(
            title="🏆 All-Time Quiz Leaderboard",
            description="\n".join(lines),
            color=discord.Color.gold(),
        )
        await i.response.send_message(embed=e)


# ── setup ──────────────────────────────────────────────────────────────────────

async def setup(bot: commands.Bot):
    await bot.add_cog(QuizCog(bot))

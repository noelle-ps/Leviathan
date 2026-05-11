import discord
from discord.ext import commands
import random
import re
from utils.cmd_access import has_cmd_access

EIGHT_BALL_RESPONSES = [
    "It is certain.", "It is decidedly so.", "Without a doubt.",
    "Yes, definitely.", "You may rely on it.", "As I see it, yes.",
    "Most likely.", "Outlook good.", "Yes.", "Signs point to yes.",
    "Reply hazy, try again.", "Ask again later.", "Better not tell you now.",
    "Cannot predict now.", "Concentrate and ask again.",
    "Don't count on it.", "My reply is no.", "My sources say no.",
    "Outlook not so good.", "Very doubtful.",
]

ROASTS = [
    "You're the reason they put instructions on shampoo.",
    "I'd roast you, but my mom said I'm not allowed to burn trash.",
    "You're like a cloud — when you disappear, it's a beautiful day.",
    "I'd agree with you, but then we'd both be wrong.",
    "You have your entire life to be an idiot. Take today off.",
    "Somewhere out there, a tree is tirelessly producing oxygen for you. Go apologize to it.",
    "You're not stupid — you just have bad luck thinking.",
    "If laughter is the best medicine, your face must be curing diseases.",
    "You are proof that evolution can go in reverse.",
    "I've met rocks with more personality.",
]

COMPLIMENTS = [
    "You're genuinely one of a kind — and that's a good thing! 🌟",
    "The world is a better place with you in it. 💛",
    "You radiate good energy wherever you go.",
    "Your potential is limitless. Keep going! 🚀",
    "You make people smile more than you realise.",
    "You're doing better than you think you are.",
    "You have a remarkable ability to make hard things look easy.",
    "Your kindness doesn't go unnoticed. 🌼",
    "You inspire people without even trying.",
    "Honestly? You're pretty amazing.",
]


def parse_dice(expr: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"(\d+)d(\d+)", expr.strip().lower())
    if match:
        return int(match.group(1)), int(match.group(2))
    return None


class FunCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @discord.app_commands.command(name="8ball", description="Ask the magic 8-ball a question.")
    @discord.app_commands.describe(question="Your yes/no question")
    @has_cmd_access()
    async def eight_ball(self, interaction: discord.Interaction, question: str):
        response = random.choice(EIGHT_BALL_RESPONSES)
        embed = discord.Embed(
            title="🎱 Magic 8-Ball",
            color=discord.Color(0x2C2F33),
        )
        embed.add_field(name="Question", value=question, inline=False)
        embed.add_field(name="Answer", value=response, inline=False)
        await interaction.response.send_message(embed=embed)

    @discord.app_commands.command(name="coinflip", description="Flip a coin.")
    @has_cmd_access()
    async def coinflip(self, interaction: discord.Interaction):
        result = random.choice(["Heads 🪙", "Tails 🪙"])
        await interaction.response.send_message(f"The coin landed on **{result}**!")

    @discord.app_commands.command(name="roll", description="Roll dice. e.g. 2d6 or 1d20")
    @discord.app_commands.describe(dice="Dice expression like 2d6 or 1d20 (default: 1d6)")
    @has_cmd_access()
    async def roll(self, interaction: discord.Interaction, dice: str = "1d6"):
        parsed = parse_dice(dice)
        if not parsed:
            await interaction.response.send_message(
                "❌ Invalid format. Use something like `1d6`, `2d10`, `1d20`.", ephemeral=True)
            return
        count, sides = parsed
        if count < 1 or count > 100:
            await interaction.response.send_message("❌ Number of dice must be between 1 and 100.", ephemeral=True)
            return
        if sides < 2 or sides > 1000:
            await interaction.response.send_message("❌ Sides must be between 2 and 1000.", ephemeral=True)
            return
        rolls = [random.randint(1, sides) for _ in range(count)]
        total = sum(rolls)
        rolls_str = ", ".join(str(r) for r in rolls) if count > 1 else str(rolls[0])
        embed = discord.Embed(title=f"🎲 Rolling {dice}", color=discord.Color(0x9B59B6))
        if count > 1:
            embed.add_field(name="Rolls", value=rolls_str, inline=False)
            embed.add_field(name="Total", value=str(total), inline=False)
        else:
            embed.description = f"You rolled a **{total}**!"
        await interaction.response.send_message(embed=embed)

    @discord.app_commands.command(name="poll", description="Create a reaction poll.")
    @discord.app_commands.describe(
        question="The poll question",
        options="Options separated by | e.g. Yes | No | Maybe",
    )
    @has_cmd_access()
    async def poll(self, interaction: discord.Interaction, question: str, options: str = "Yes | No"):
        option_list = [o.strip() for o in options.split("|") if o.strip()]
        if len(option_list) < 2:
            await interaction.response.send_message("❌ Provide at least 2 options separated by |", ephemeral=True)
            return
        if len(option_list) > 10:
            await interaction.response.send_message("❌ Maximum 10 options allowed.", ephemeral=True)
            return

        number_emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
        description = "\n".join(f"{number_emojis[i]} {opt}" for i, opt in enumerate(option_list))

        embed = discord.Embed(
            title=f"📊 {question}",
            description=description,
            color=discord.Color(0x3498DB),
        )
        embed.set_footer(text=f"Poll by {interaction.user.display_name}")

        await interaction.response.send_message(embed=embed)
        message = await interaction.original_response()
        for i in range(len(option_list)):
            await message.add_reaction(number_emojis[i])

    @discord.app_commands.command(name="roast", description="Roast a member (all in good fun!).")
    @discord.app_commands.describe(member="The member to roast")
    @has_cmd_access()
    async def roast(self, interaction: discord.Interaction, member: discord.Member):
        if member == interaction.user:
            await interaction.response.send_message("Roasting yourself? Brave. 😂", ephemeral=True)
            return
        roast = random.choice(ROASTS)
        await interaction.response.send_message(f"🔥 {member.mention}, {roast}")

    @discord.app_commands.command(name="compliment", description="Send a compliment to a member.")
    @discord.app_commands.describe(member="The member to compliment")
    @has_cmd_access()
    async def compliment(self, interaction: discord.Interaction, member: discord.Member):
        comp = random.choice(COMPLIMENTS)
        await interaction.response.send_message(f"💛 {member.mention}, {comp}")

    async def cog_app_command_error(self, interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
        if isinstance(error, discord.app_commands.CheckFailure):
            await interaction.response.send_message("❌ You don't have access to this command.", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ {error}", ephemeral=True)


async def setup(bot):
    await bot.add_cog(FunCog(bot))

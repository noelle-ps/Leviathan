from discord.ext import commands
import discord
from utils.paginator import Paginator


class RoleCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @discord.app_commands.command(name="listrole", description="Save role members to a file")
    @discord.app_commands.describe(role="Select the role to list members of")
    async def listrole(self, interaction: discord.Interaction, role: discord.Role):
        if interaction.guild is None:
            await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
            return

        members = [member.name for member in role.members]
        count = len(members)

        if count == 0:
            await interaction.response.send_message(f"No members found in role **{role.name}**.", ephemeral=True)
            return

        with open("role_members.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(members))

        await interaction.response.send_message(
            f"📄 Role **{role.name}** has **{count} members**. List saved to file:",
            file=discord.File("role_members.txt"))

    @discord.app_commands.command(name="listrolepage", description="Show paginated members of a role")
    @discord.app_commands.describe(role="Select the role to list members of")
    async def listrolepage(self, interaction: discord.Interaction, role: discord.Role):
        if interaction.guild is None:
            await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
            return

        members = [member.name for member in role.members]
        if not members:
            await interaction.response.send_message(f"No members found in role **{role.name}**.", ephemeral=True)
            return

        view = Paginator(members, per_page=10)
        await interaction.response.send_message(view.get_page_content(), view=view)


async def setup(bot):
    await bot.add_cog(RoleCog(bot))

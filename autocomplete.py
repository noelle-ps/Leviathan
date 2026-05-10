import discord


async def role_autocomplete(
        interaction: discord.Interaction,
        current: str) -> list[discord.app_commands.Choice[str]]:
    choices: list[discord.app_commands.Choice[str]] = []
    if interaction.guild:
        for role in interaction.guild.roles:
            if role.name.lower().startswith(current.lower()):
                choices.append(
                    discord.app_commands.Choice(name=role.name,
                                                value=role.name))
    return choices[:25]

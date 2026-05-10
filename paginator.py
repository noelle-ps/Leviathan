import discord


class Paginator(discord.ui.View):

    def __init__(self, entries, per_page=10):
        super().__init__(timeout=None)
        self.entries = entries
        self.per_page = per_page
        self.page = 0

    def get_page_content(self):
        start = self.page * self.per_page
        end = start + self.per_page
        page_entries = self.entries[start:end]
        content = "\n".join(page_entries)
        return f"**Members (Page {self.page+1}/{self.total_pages()}):**\n{content}"

    def total_pages(self):
        return (len(self.entries) - 1) // self.per_page + 1

    @discord.ui.button(label="⬅️ Previous",
                       style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction,
                       button: discord.ui.Button):
        if self.page > 0:
            self.page -= 1
            await interaction.response.edit_message(
                content=self.get_page_content(), view=self)

    @discord.ui.button(label="➡️ Next", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction,
                   button: discord.ui.Button):
        if self.page < self.total_pages() - 1:
            self.page += 1
            await interaction.response.edit_message(
                content=self.get_page_content(), view=self)

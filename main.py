import logging
import os

import discord
from discord import app_commands
from discord.ext import commands

from store import Store

extension_list = (
    "src.discordbot.timelogic",
    "src.discordbot.birthday",
    "src.discordbot.remind",
    "src.discordbot.fun",
    "src.discordbot.seal",
    "src.discordbot.music",
)


class HomeBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.store = Store("bot.db")

    async def setup_hook(self):
        for name in extension_list:
            await self.load_extension(name)
        await self.tree.sync()

    async def on_ready(self):
        print(f"logged in as {self.user}")


bot = HomeBot()


@bot.tree.error
async def catch_tree_error(interaction, error):
    match error:
        case app_commands.MissingPermissions():
            missing = ", ".join(error.missing_permissions)
            text = f"You need these permissions for that: {missing}."
        case app_commands.NoPrivateMessage():
            text = "That only works inside a server."
        case app_commands.CheckFailure():
            text = "You can't use that here."
        case _:
            logging.getLogger("bot").error("command failed", exc_info=error)
            text = "Something broke on my end."
    if interaction.response.is_done():
        await interaction.followup.send(text, ephemeral=True)
    else:
        await interaction.response.send_message(text, ephemeral=True)


if __name__ == "__main__":
    bot.run(os.environ["DISCORD_TOKEN"])

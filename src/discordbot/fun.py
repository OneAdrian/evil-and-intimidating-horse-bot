import asyncio
import os
from io import BytesIO

import discord
from discord import app_commands
from discord.ext import commands
from fromsoft import GRADIENTS, fromsoft_banner

font_file = os.environ.get("FONT_PATH", "agmena.ttf")
soop_min = 7
soop_max = 30


class FunCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="fromsoft", description="Verb a noun in Elden Ring style")
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.describe(
        text="The text to render",
        color=("Hex code(s) or: " + " ".join(GRADIENTS))[:100],
        hidden="Only you see the result",
        all_caps="Capitalise everything",
    )
    async def fromsoft(
        self,
        interaction: discord.Interaction,
        text: str,
        color: str = "#ffd042",
        hidden: bool = False,
        all_caps: bool = True,
    ):
        await interaction.response.defer(ephemeral=hidden)
        if all_caps:
            text = text.upper()
        try:
            banner = await asyncio.to_thread(fromsoft_banner, text, GRADIENTS.get(color, color), font_file)
        except FileNotFoundError:
            await interaction.followup.send(f"The font {font_file} is missing on the bot's side.")
            return
        except (ValueError, TypeError):
            await interaction.followup.send("That colour didn't parse. Use hex, space separated hexes or a gradient name.")
            return
        buffer = BytesIO()
        banner.save(buffer, "PNG")
        buffer.seek(0)
        await interaction.followup.send(file=discord.File(buffer, filename="image.png"))

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or message.webhook_id or message.guild is None:
            return
        text = message.content.strip()
        if not soop_min <= len(text) <= soop_max:
            return
        if "soop" not in text.lower():
            return
        try:
            await message.guild.edit(name=text, reason=f"soop rename by {message.author}")
        except discord.HTTPException:
            pass


async def setup(bot):
    await bot.add_cog(FunCog(bot))

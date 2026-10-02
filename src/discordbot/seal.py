import os
import random
from datetime import time
from time import time as epoch_now
from zoneinfo import ZoneInfo

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

klipy_base = "https://api.klipy.com/api/v1"
seal_query = "seal animal"
repeat_gap = 20 * 86400
go_time = time(15, 0, tzinfo=ZoneInfo("Europe/London"))


def gif_link(item):
    sizes = item.get("file") or {}
    for size in ("md", "hd", "sm"):
        link = ((sizes.get(size) or {}).get("gif") or {}).get("url")
        if link:
            return link
    return None


class SealCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.klipy_key = os.environ["KLIPY_KEY"]
        self.daily_seal.start()

    async def cog_unload(self):
        self.daily_seal.cancel()

    @app_commands.command(name="sealect", description="Pick the channel for the daily seal")
    @app_commands.describe(channel="Channel the seal gets posted in")
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.checks.has_permissions(manage_channels=True)
    @app_commands.guild_only()
    async def sealect(self, interaction: discord.Interaction, channel: discord.TextChannel):
        self.bot.store.set_pref(interaction.guild_id, "seal_channel", channel.id)
        await interaction.response.send_message(
            f"Seals will land in {channel.mention} from now on.", ephemeral=True
        )

    async def pick_seal(self):
        now = epoch_now()
        self.bot.store.run("delete from seal_log where posted_at < ?", now - repeat_gap)
        recent = {row["gif_id"] for row in self.bot.store.many("select gif_id from seal_log")}
        pages = list(range(1, 6))
        random.shuffle(pages)
        async with aiohttp.ClientSession() as session:
            for page in pages:
                params = {"q": seal_query, "page": page, "per_page": 50}
                async with session.get(f"{klipy_base}/{self.klipy_key}/gifs/search", params=params) as reply:
                    if reply.status != 200:
                        continue
                    payload = await reply.json()
                items = (payload.get("data") or {}).get("data") or []
                random.shuffle(items)
                for item in items:
                    raw_id = item.get("slug") or item.get("id")
                    link = gif_link(item)
                    if raw_id is None or link is None or str(raw_id) in recent:
                        continue
                    self.bot.store.run(
                        "insert into seal_log (gif_id, posted_at) values (?, ?)", str(raw_id), now
                    )
                    return link
        return None

    @tasks.loop(time=go_time)
    async def daily_seal(self):
        targets = self.bot.store.many("select seal_channel from guild_prefs where seal_channel is not null")
        if not targets:
            return
        link = await self.pick_seal()
        if link is None:
            return
        embed = discord.Embed()
        embed.set_image(url=link)
        embed.set_footer(text="via KLIPY")
        for row in targets:
            channel = self.bot.get_channel(row["seal_channel"])
            if channel is None:
                continue
            try:
                await channel.send(embed=embed)
            except discord.HTTPException:
                pass

    @daily_seal.before_loop
    async def wait_for_bot(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(SealCog(bot))

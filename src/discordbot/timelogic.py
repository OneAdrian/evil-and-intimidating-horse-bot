from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

from zones import long_stamp, zone_for, zone_from_text


class TimeCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="timezone", description="Save your timezone for time and reminder commands")
    @app_commands.describe(zone="A city like Rome or London, or GMT+2 / UTC-5, or a common spelling of the time zone (CEST, EDT)")
    async def set_timezone(self, interaction: discord.Interaction, zone: str):
        result = zone_from_text(zone)
        if result is None:
            await interaction.response.send_message(
                "Couldn't read that. Try GMT+2, UTC-5, CEST or Europe/Rome.", ephemeral=True
            )
            return
        label = result[1]
        self.bot.store.run(
            "insert into zones (user_id, zone) values (?, ?) "
            "on conflict(user_id) do update set zone = excluded.zone",
            interaction.user.id,
            label,
        )
        await interaction.response.send_message(f"Timezone saved as {label}.", ephemeral=True)

    @app_commands.command(name="time", description="Show someone's local time")
    @app_commands.describe(user="Whose time to show, defaults to you")
    async def show_time(self, interaction: discord.Interaction, user: discord.User | None = None):
        person = user or interaction.user
        zone = zone_for(self.bot.store, person.id)
        if zone is None:
            if person.id == interaction.user.id:
                text = "You haven't set a timezone yet, use /timezone."
            else:
                text = f"{person.display_name} hasn't set a timezone."
            await interaction.response.send_message(text, ephemeral=True)
            return
        await interaction.response.send_message(
            f"{person.display_name}: {long_stamp(datetime.now(zone))}"
        )


async def setup(bot):
    await bot.add_cog(TimeCog(bot))

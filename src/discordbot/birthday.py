import calendar
from datetime import datetime, time, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks


class BirthdayCog(commands.GroupCog, group_name="birthday", group_description="Birthday announcements"):
    def __init__(self, bot):
        super().__init__()
        self.bot = bot
        self.midnight_sweep.start()

    async def cog_unload(self):
        self.midnight_sweep.cancel()

    @app_commands.command(name="set", description="Save your birthday")
    @app_commands.describe(day="Day of the month", month="Month number")
    async def set_day(
        self,
        interaction: discord.Interaction,
        day: app_commands.Range[int, 1, 31],
        month: app_commands.Range[int, 1, 12],
    ):
        if day > calendar.monthrange(2024, month)[1]:
            await interaction.response.send_message("That date doesn't exist.", ephemeral=True)
            return
        self.bot.store.run(
            "insert into birthdays (user_id, day, month) values (?, ?, ?) "
            "on conflict(user_id) do update set day = excluded.day, month = excluded.month",
            interaction.user.id,
            day,
            month,
        )
        await interaction.response.send_message(
            f"Birthday saved as {day} {calendar.month_name[month]}.", ephemeral=True
        )

    @app_commands.command(name="channel", description="Pick where birthday messages are sent")
    @app_commands.describe(channel="Channel for birthday messages")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def pick_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if interaction.guild is None:
            raise app_commands.NoPrivateMessage()
        self.bot.store.set_pref(interaction.guild.id, "birthday_channel", channel.id)
        await interaction.response.send_message(
            f"Birthday messages will go to {channel.mention}.", ephemeral=True
        )

    async def find_member(self, guild, user_id):
        member = guild.get_member(user_id)
        if member is not None:
            return member
        try:
            return await guild.fetch_member(user_id)
        except discord.HTTPException:
            return None

    @tasks.loop(time=time(0, 0, tzinfo=timezone.utc))
    async def midnight_sweep(self):
        today = datetime.now(timezone.utc)
        dates = [(today.day, today.month)]
        if dates[0] == (28, 2) and not calendar.isleap(today.year):
            dates.append((29, 2))
        rows = []
        for day, month in dates:
            rows += self.bot.store.many(
                "select user_id from birthdays where day = ? and month = ?", day, month
            )
        if not rows:
            return
        for guild in self.bot.guilds:
            channel_id = self.bot.store.get_pref(guild.id, "birthday_channel")
            channel = guild.get_channel(channel_id) if channel_id else None
            if channel is None:
                continue
            for row in rows:
                member = await self.find_member(guild, row["user_id"])
                if member is None:
                    continue
                try:
                    await channel.send(f"Happy birthday {member.mention}!")
                except discord.HTTPException:
                    pass

    @midnight_sweep.before_loop
    async def wait_for_bot(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(BirthdayCog(bot))

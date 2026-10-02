import logging
import re
from dataclasses import dataclass
from datetime import timezone
from time import time as epoch_now

import discord
from discord import app_commands
from discord.ext import commands, tasks

from when import next_clock_fire, read_clock_rule, read_moment, read_span
from zones import zone_for

message_link = re.compile(
    r"https?://(?:(?:ptb|canary)\.)?discord(?:app)?\.com/channels/(\d+)/(\d+)/(\d+)"
)
default_wait = 3600
min_repeat = 300
max_wait = 315360000
max_active = 50
body_limit = 3500
full_text = "You've got too many active reminders already."


class ReminderProblem(Exception):
    pass


@dataclass
class Schedule:
    fire_at: float
    every_seconds: float | None = None
    weekday: int | None = None
    clock_hour: int | None = None
    clock_minute: int | None = None

    @property
    def repeats(self):
        return self.every_seconds is not None or self.clock_hour is not None


def describe_message(message):
    lines = [message.jump_url]
    if message.content:
        quoted = "\n".join(f"> {line}" for line in message.content.splitlines())
        lines.append(f"{message.author.display_name} said:\n{quoted}")
    lines.extend(attachment.url for attachment in message.attachments)
    for embed in message.embeds:
        spot = embed.url or embed.image.url or embed.thumbnail.url
        if spot:
            lines.append(spot)
    return "\n".join(lines)[:body_limit]


class RemindAboutModal(discord.ui.Modal, title="Remind me about this"):
    when_box = discord.ui.TextInput(label="When (30m, 2d, or unix time)", default="1h", max_length=40)

    def __init__(self, cog, target_message):
        super().__init__()
        self.cog = cog
        self.target_message = target_message

    async def on_submit(self, interaction):
        now = epoch_now()
        moment = read_moment(self.when_box.value, now)
        problem = self.cog.moment_problem(moment, now)
        if problem is None and self.cog.sender_is_full(interaction.user.id):
            problem = full_text
        if problem:
            await interaction.response.send_message(problem, ephemeral=True)
            return
        self.cog.save_reminder(
            interaction, interaction.user.id, describe_message(self.target_message), Schedule(fire_at=moment), now
        )
        await interaction.response.send_message(f"Okay, reminding you <t:{int(moment)}:R>.", ephemeral=True)


class RemindCog(commands.GroupCog, group_name="remind", group_description="Set and manage reminders"):
    def __init__(self, bot):
        super().__init__()
        self.bot = bot
        self.menu_entry = app_commands.ContextMenu(name="Remind me about this", callback=self.menu_remind)
        self.bot.tree.add_command(self.menu_entry)
        self.ticker.start()

    async def cog_unload(self):
        self.ticker.cancel()
        self.bot.tree.remove_command(self.menu_entry.name, type=self.menu_entry.type)

    async def interaction_check(self, interaction):
        if interaction.guild is None:
            raise app_commands.NoPrivateMessage()
        return True

    async def refuse(self, interaction, text):
        await interaction.response.send_message(text, ephemeral=True)

    def moment_problem(self, moment, now):
        if moment is None:
            return "Couldn't read that time. Use 30m, 2d, 1h30m or a unix timestamp."
        if moment <= now:
            return "That time has already passed."
        if moment - now > max_wait:
            return "That's too far away."
        return None

    def sender_is_full(self, sender_id):
        row = self.bot.store.one("select count(*) as amount from reminders where sender_id = ?", sender_id)
        return row["amount"] >= max_active

    def third_party_problem(self, guild_id, target):
        if not self.bot.store.get_pref(guild_id, "others_can_remind"):
            return "Reminders for other people are turned off in this server."
        if self.bot.store.one("select 1 from remind_optouts where user_id = ?", target.id):
            return f"{target.display_name} doesn't take reminders from other people."
        return None

    def plan_schedule(self, time_text, recurring_text, target_id, now):
        if time_text and recurring_text:
            raise ReminderProblem("Use either time or recurring, not both.")
        if recurring_text:
            span = read_span(recurring_text)
            if span is not None:
                if span < min_repeat or span > max_wait:
                    raise ReminderProblem("Recurring gaps must be between 5 minutes and 10 years.")
                return Schedule(fire_at=now + span, every_seconds=span)
            rule = read_clock_rule(recurring_text)
            if rule is None:
                raise ReminderProblem("Couldn't read recurring. Try 3d or Saturdays 15:00.")
            weekday, hour, minute = rule
            zone = zone_for(self.bot.store, target_id) or timezone.utc
            fire_at = next_clock_fire(weekday, hour, minute, zone, now)
            return Schedule(fire_at=fire_at, weekday=weekday, clock_hour=hour, clock_minute=minute)
        moment = now + default_wait if not time_text else read_moment(time_text, now)
        problem = self.moment_problem(moment, now)
        if problem:
            raise ReminderProblem(problem)
        return Schedule(fire_at=moment)

    async def quote_message(self, interaction, found):
        guild_id, channel_id, message_id = (int(part) for part in found.groups())
        link = found.group(0)
        if guild_id != interaction.guild_id:
            return link
        channel = interaction.guild.get_channel_or_thread(channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            return link
        rights = channel.permissions_for(interaction.user)
        if not (rights.view_channel and rights.read_message_history):
            return link
        try:
            original = await channel.fetch_message(message_id)
        except discord.HTTPException:
            return link
        return describe_message(original)

    def save_reminder(self, interaction, target_id, body, schedule, now):
        self.bot.store.run(
            "insert into reminders (guild_id, channel_id, sender_id, target_id, body, made_at, fire_at, "
            "every_seconds, weekday, clock_hour, clock_minute) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            interaction.guild_id,
            interaction.channel_id,
            interaction.user.id,
            target_id,
            body[:body_limit],
            now,
            schedule.fire_at,
            schedule.every_seconds,
            schedule.weekday,
            schedule.clock_hour,
            schedule.clock_minute,
        )

    @app_commands.command(name="add", description="Set a reminder")
    @app_commands.describe(
        reminder="Text, a link, or a message link",
        who="Who to ping, defaults to you",
        time="30m, 2d, 1h30m or a unix timestamp, defaults to 1h",
        recurring="3d, or a day and time like Saturdays 15:00",
    )
    async def add(
        self,
        interaction: discord.Interaction,
        reminder: app_commands.Range[str, 1, 1500],
        who: discord.Member | None = None,
        time: str | None = None,
        recurring: str | None = None,
    ):
        target = who or interaction.user
        now = epoch_now()
        if target.id != interaction.user.id:
            blocked = self.third_party_problem(interaction.guild_id, target)
            if blocked:
                return await self.refuse(interaction, blocked)
        if self.sender_is_full(interaction.user.id):
            return await self.refuse(interaction, full_text)
        try:
            schedule = self.plan_schedule(time, recurring, target.id, now)
        except ReminderProblem as problem:
            return await self.refuse(interaction, str(problem))
        text = reminder.strip()
        match text.lower():
            case "reply":
                return await self.refuse(
                    interaction,
                    "Slash commands can't be sent as replies. Right click the message, Apps, Remind me about this.",
                )
            case _ if (found := message_link.fullmatch(text)):
                await interaction.response.defer()
                body = await self.quote_message(interaction, found)
            case _:
                await interaction.response.defer()
                body = text
        self.save_reminder(interaction, target.id, body, schedule, now)
        repeat_note = " It repeats." if schedule.repeats else ""
        await interaction.followup.send(
            f"Reminder set for {target.display_name}, first one <t:{int(schedule.fire_at)}:R>.{repeat_note}",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="list", description="List your active reminders")
    @app_commands.describe(who="Only you can list your own reminders")
    async def list_mine(self, interaction: discord.Interaction, who: discord.Member | None = None):
        target = who or interaction.user
        if target.id != interaction.user.id:
            return await self.refuse(interaction, "Only the pinged user can list their own reminders.")
        rows = self.bot.store.many(
            "select * from reminders where target_id = ? and guild_id = ? order by fire_at",
            target.id,
            interaction.guild_id,
        )
        if not rows:
            return await self.refuse(interaction, "No active reminders.")
        lines = []
        for row in rows[:15]:
            repeating = row["every_seconds"] is not None or row["clock_hour"] is not None
            preview = row["body"].replace("\n", " ")[:60]
            lines.append(f"<t:{int(row['fire_at'])}:R>{' (repeats)' if repeating else ''}: {preview}")
        if len(rows) > 15:
            lines.append(f"...and {len(rows) - 15} more")
        await self.refuse(interaction, "\n".join(lines))

    @app_commands.command(name="remove", description="Remove all active reminders for a user")
    @app_commands.describe(who="Only you, unless you're an administrator")
    async def remove_all(self, interaction: discord.Interaction, who: discord.Member | None = None):
        target = who or interaction.user
        is_admin = interaction.user.guild_permissions.administrator
        if target.id != interaction.user.id and not is_admin:
            return await self.refuse(
                interaction, f"Only {target.display_name} can manage their own reminders."
            )
        cursor = self.bot.store.run(
            "delete from reminders where target_id = ? and guild_id = ?", target.id, interaction.guild_id
        )
        await self.refuse(interaction, f"Removed {cursor.rowcount} reminder(s) for {target.display_name}.")

    @app_commands.command(name="others", description="Allow or block other people from reminding you")
    @app_commands.describe(allowed="True to let others set reminders for you")
    async def others(self, interaction: discord.Interaction, allowed: bool):
        if allowed:
            self.bot.store.run("delete from remind_optouts where user_id = ?", interaction.user.id)
        else:
            self.bot.store.run("insert or ignore into remind_optouts (user_id) values (?)", interaction.user.id)
        state = "can" if allowed else "can't"
        await self.refuse(interaction, f"Other people {state} set reminders for you now.")

    @app_commands.command(name="serverothers", description="Allow or block reminders for others in this server")
    @app_commands.describe(allowed="True to let people use who: on other members")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def serverothers(self, interaction: discord.Interaction, allowed: bool):
        self.bot.store.set_pref(interaction.guild_id, "others_can_remind", int(allowed))
        state = "allowed" if allowed else "disabled"
        await self.refuse(interaction, f"Reminders for other people are now {state} here.")

    async def menu_remind(self, interaction: discord.Interaction, message: discord.Message):
        if interaction.guild is None:
            return await self.refuse(interaction, "That only works inside a server.")
        await interaction.response.send_modal(RemindAboutModal(self, message))

    async def deliver(self, row):
        text = f"Reminder from <t:{int(row['made_at'])}:R>:\n{row['body']}"
        if row["sender_id"] != row["target_id"]:
            text += f"\n\nSet by <@{row['sender_id']}>"
        embed = discord.Embed(title="Reminder", description=text, colour=discord.Colour.blurple())
        pings = discord.AllowedMentions(
            users=[discord.Object(id=row["target_id"])], roles=False, everyone=False
        )
        channel = self.bot.get_channel(row["channel_id"])
        if channel is not None:
            try:
                await channel.send(content=f"<@{row['target_id']}>", embed=embed, allowed_mentions=pings)
                return
            except discord.HTTPException:
                pass
        user = await self.bot.fetch_user(row["target_id"])
        await user.send(embed=embed)

    def move_or_drop(self, row, now):
        match (row["every_seconds"], row["clock_hour"]):
            case (None, None):
                self.bot.store.run("delete from reminders where id = ?", row["id"])
                return
            case (seconds, None):
                skipped = int((now - row["fire_at"]) // seconds) + 1
                next_at = row["fire_at"] + skipped * seconds
            case _:
                zone = zone_for(self.bot.store, row["target_id"]) or timezone.utc
                next_at = next_clock_fire(row["weekday"], row["clock_hour"], row["clock_minute"], zone, now)
        self.bot.store.run(
            "update reminders set fire_at = ?, made_at = ? where id = ?", next_at, now, row["id"]
        )

    @tasks.loop(seconds=2)
    async def ticker(self):
        now = epoch_now()
        due = self.bot.store.many("select * from reminders where fire_at <= ? order by fire_at", now)
        for row in due:
            try:
                await self.deliver(row)
            except Exception:
                logging.getLogger("bot").exception("reminder %s failed", row["id"])
            self.move_or_drop(row, now)

    @ticker.before_loop
    async def wait_for_bot(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(RemindCog(bot))

import sqlite3

schema = """
create table if not exists zones (
    user_id integer primary key,
    zone text not null
);
create table if not exists birthdays (
    user_id integer primary key,
    day integer not null,
    month integer not null
);
create table if not exists guild_prefs (
    guild_id integer primary key,
    birthday_channel integer,
    seal_channel integer,
    others_can_remind integer not null default 1,
    soop_enabled integer not null default 0
);
create table if not exists remind_optouts (
    user_id integer primary key
);
create table if not exists reminders (
    id integer primary key autoincrement,
    guild_id integer not null,
    channel_id integer not null,
    sender_id integer not null,
    target_id integer not null,
    body text not null,
    made_at real not null,
    fire_at real not null,
    every_seconds real,
    weekday integer,
    clock_hour integer,
    clock_minute integer
);
create index if not exists reminders_due on reminders (fire_at);
create table if not exists seal_log (
    gif_id text primary key,
    posted_at real not null
);
"""

pref_defaults = {"birthday_channel": None, "seal_channel": None, "others_can_remind": 1, "soop_enabled": 0}


class Store:
    def __init__(self, path):
        self.link = sqlite3.connect(path)
        self.link.row_factory = sqlite3.Row
        self.link.executescript(schema)
        columns = {row["name"] for row in self.link.execute("pragma table_info(guild_prefs)")}
        if "soop_enabled" not in columns:
            self.link.execute("alter table guild_prefs add column soop_enabled integer not null default 0")
            self.link.commit()

    def run(self, sql, *params):
        cursor = self.link.execute(sql, params)
        self.link.commit()
        return cursor

    def one(self, sql, *params):
        return self.link.execute(sql, params).fetchone()

    def many(self, sql, *params):
        return self.link.execute(sql, params).fetchall()

    def get_pref(self, guild_id, column):
        if column not in pref_defaults:
            raise ValueError(column)
        row = self.one(f"select {column} from guild_prefs where guild_id = ?", guild_id)
        return pref_defaults[column] if row is None else row[column]

    def set_pref(self, guild_id, column, value):
        if column not in pref_defaults:
            raise ValueError(column)
        self.run(
            f"insert into guild_prefs (guild_id, {column}) values (?, ?) "
            f"on conflict(guild_id) do update set {column} = excluded.{column}",
            guild_id,
            value,
        )
import asyncio
import logging
import os
import random
import re
from collections import deque
from pathlib import Path

import aiohttp
import discord
from discord.ext import commands

log = logging.getLogger(__name__)

EMBED_COLOR = discord.Color.from_rgb(134, 77, 232)
UMA_COLOR = discord.Color.from_rgb(255, 233, 133)
HORSE_DIR = Path("./assets/horses")
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
KLIPY_URL = "https://api.klipy.com/api/v1/{key}/gifs/search"

ER_CHANCE = 1 / 1
HUNGER_CHANCE = 1 / 1
SEAL_CHANCE = 1 / 1
HORSEY_CHANCE = 1 / 1
SPECIAL_UMA_CHANCE = 1 / 8
RECENT_LIMIT = 10

SEAL_QUERIES = ["seal", "sea lion", "walrus"]
SPECIAL_UMAS = ["Oguri Cap", "TM Opera O", "Seiun Sky", "Manhattan Cafe"]

ER_PATTERN = re.compile(r"\b(\w{2,10}er)\b")

HUNGRY = r"(?!hanger\b)h[uao]*[mn]+g+[rwl]*[yie]+r?"
LEAD_INS = (
    r"(?:i|im|i'm|ima|i'ma|ive|i've|we|were|we're|so+|very|really|rly|super|hella|"
    r"kinda|sorta|getting|gettin|still|literally|actually|too|always|feeling|feel|am|are|is|be)"
)
HUNGER_PATTERNS = [
    re.compile(rf"^\W*{HUNGRY}\W*$", re.I),
    re.compile(rf"\b{LEAD_INS}\s+(?:\w+\s+){{0,2}}{HUNGRY}\b", re.I),
    re.compile(r"\b(?:starv(?:ing|in|ed)|st[ae]?[rw]+v(?:ing|in|ed)|famished|ravenous|peckish)\b", re.I),
    re.compile(
        r"\b(?:need|want|wan|gimme|gotta|get me|give me|bring me)\s+(?:some\s+|a\s+)?"
        r"(?:food|foods|snacks?|dinner|lunch|breakfast|eats)\b",
        re.I,
    ),
    re.compile(r"\bfeed me\b", re.I),
    re.compile(r"\b(?:wanna\s+eat|(?:need|want|gotta|have|got)\s+to\s+eat|need\s+eat)\b", re.I),
    re.compile(r"\bi\s+could\s+(?:really\s+)?eat\b", re.I),
    re.compile(r"\bhaven'?t\s+eaten\b", re.I),
    re.compile(r"\b(?:tummy|belly|stomach|tum)\s+(?:is\s+|'s\s+)?(?:growl|rumbl|empty)\w*", re.I),
]
SEAL_PATTERN = re.compile(r"\b(?:seals?|sealions?|sea\s+lions?|pinnipeds?|walrus(?:es)?)\b", re.I)
HORSE_WORD = r"(?:horsey|horseys|horsie|horsies|honse[sy]*|uma\w*)"
HORSEY_PATTERN = re.compile(
    rf"\b{HORSE_WORD}\b|\bone\s+of\s+(?:my|the|our|your|his|her|their|these|those)\s+(?:horses?|{HORSE_WORD})\b",
    re.I,
)


def pick_gif_url(item):
    files = item.get("file") or item.get("files") or {}
    for size in ("md", "hd", "sm", None):
        node = files.get(size) if size else files
        gif = (node or {}).get("gif") if isinstance(node, dict) else None
        if isinstance(gif, dict) and gif.get("url"):
            return gif["url"]
    return None


class Chatter(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.session = None
        self.last_horse = None
        self.recent_seals = deque(maxlen=RECENT_LIMIT)
        self.recent_umas = deque(maxlen=RECENT_LIMIT)

    async def cog_load(self):
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))

    async def cog_unload(self):
        if self.session:
            await self.session.close()

    def pick_horse_image(self):
        if not HORSE_DIR.is_dir():
            log.warning("horse dir not found: %s", HORSE_DIR.resolve())
            return None
        files = [path for path in HORSE_DIR.iterdir() if path.suffix.lower() in IMAGE_SUFFIXES]
        if not files:
            log.warning("no images in %s", HORSE_DIR.resolve())
            return None
        if len(files) > 1 and self.last_horse in files:
            files.remove(self.last_horse)
        choice = random.choice(files)
        self.last_horse = choice
        return choice

    async def fetch_gif(self, query, recent, user_id):
        key = os.getenv("KLIPY_KEY")
        if not key:
            log.warning("KLIPY_KEY is not set")
            return None
        if not self.session:
            log.warning("http session not ready")
            return None
        pages = [random.randint(1, 3), 1, 2]
        for page in pages:
            params = {"q": query, "page": page, "per_page": 24, "customer_id": str(user_id)}
            try:
                async with self.session.get(KLIPY_URL.format(key=key), params=params) as response:
                    if response.status != 200:
                        log.warning("klipy %s for %r: %s", response.status, query, (await response.text())[:200])
                        continue
                    payload = await response.json()
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as error:
                log.warning("klipy request failed for %r: %r", query, error)
                continue
            data = payload.get("data") if isinstance(payload, dict) else None
            items = data.get("data") if isinstance(data, dict) else data
            if not isinstance(items, list):
                log.warning("unexpected klipy payload for %r: %s", query, str(payload)[:200])
                continue
            options = []
            for item in items:
                url = pick_gif_url(item)
                gif_id = str(item.get("id") or url)
                if url and gif_id not in recent:
                    options.append((gif_id, url))
            if options:
                gif_id, url = random.choice(options)
                recent.append(gif_id)
                return url
            log.warning("no usable gifs for %r page %s (%s items)", query, page, len(items))
        return None

    async def send_gif(self, message, query, recent, color):
        url = await self.fetch_gif(query, recent, message.author.id)
        if not url:
            return
        embed = discord.Embed(color=color)
        embed.set_image(url=url)
        await message.reply(embed=embed, mention_author=False)

    async def handle_er(self, message, text):
        matches = ER_PATTERN.findall(text)
        if matches and random.random() < ER_CHANCE:
            word = random.choice(matches)
            await message.reply(f"{word}? i 'ardly know er!", mention_author=False)

    async def handle_hunger(self, message, text):
        if not any(pattern.search(text) for pattern in HUNGER_PATTERNS):
            return
        if random.random() >= HUNGER_CHANCE:
            return
        path = self.pick_horse_image()
        if not path:
            return
        file = discord.File(path, filename=f"horse{path.suffix.lower()}")
        embed = discord.Embed(color=EMBED_COLOR)
        embed.set_image(url=f"attachment://{file.filename}")
        await message.reply(embed=embed, file=file, mention_author=False)

    async def handle_seal(self, message, text):
        if SEAL_PATTERN.search(text) and random.random() < SEAL_CHANCE:
            await self.send_gif(message, random.choice(SEAL_QUERIES), self.recent_seals, EMBED_COLOR)

    async def handle_horsey(self, message, text):
        if not HORSEY_PATTERN.search(text) or random.random() >= HORSEY_CHANCE:
            return
        if random.random() < SPECIAL_UMA_CHANCE:
            query = f"{random.choice(SPECIAL_UMAS)} umamusume"
            color = UMA_COLOR
        else:
            query = "umamusume"
            color = EMBED_COLOR
        await self.send_gif(message, query, self.recent_umas, color)

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.content:
            return
        text = message.content.replace("’", "'")
        handlers = (self.handle_er, self.handle_hunger, self.handle_seal, self.handle_horsey)
        for handler in handlers:
            try:
                await handler(message, text)
            except Exception:
                log.exception("%s failed", handler.__name__)


async def setup(bot):
    await bot.add_cog(Chatter(bot))
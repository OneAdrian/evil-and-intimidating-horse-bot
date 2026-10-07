import logging
import re
from urllib.parse import urlsplit, urlunsplit

import discord
from discord.ext import commands

log = logging.getLogger("embedfix")

EMBED_DOMAINS = {
    "x.com": "boypussyx.com",
    "twitter.com": "boypussyx.com",
    "tumblr.com": "tpmblr.com",
    "reddit.com": "fxreddit.com",
    "pixiv.net": "phixiv.net",
    "bsky.app": "bsyy.app",
    "instagram.com": "kkinstagram.com",
    "tiktok.com": "tnktok.com",
}

DROPPED_SUBDOMAINS = {"m", "mobile", "old", "new", "np"}
MAX_LINKS_PER_MESSAGE = 5
TRAILING_PUNCTUATION = ".,!?;:'\"*~|"

LINK_PATTERN = re.compile(
    r"(?<![<\w@/.\-])(?:https?://)?(?:[\w\-]+\.)+[a-z]{2,}(?::\d+)?(?:/[^\s<>()\[\]]*)?",
    re.I,
)


def fix_link(raw):
    url = raw.rstrip(TRAILING_PUNCTUATION)
    if "://" not in url:
        url = f"https://{url}"
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if not parts.path.strip("/"):
        return None
    for original, embedder in EMBED_DOMAINS.items():
        if host != original and not host.endswith(f".{original}"):
            continue
        labels = host[: -len(original)].rstrip(".").split(".") if host != original else []
        labels = [label for label in labels if label and label not in DROPPED_SUBDOMAINS]
        netloc = ".".join([*labels, embedder])
        return urlunsplit(("https", netloc, parts.path, parts.query, parts.fragment))
    return None


def fix_links(text):
    fixed = []
    for match in LINK_PATTERN.finditer(text):
        link = fix_link(match.group(0))
        if link and link not in fixed:
            fixed.append(link)
    return fixed[:MAX_LINKS_PER_MESSAGE]


class EmbedFixer(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or message.guild is None or not message.content:
            return
        links = fix_links(message.content)
        if not links:
            return
        permissions = message.channel.permissions_for(message.guild.me)
        if not (permissions.send_messages and permissions.embed_links):
            return
        try:
            await message.reply("\n".join(links), mention_author=False)
        except discord.HTTPException:
            log.warning("could not send fixed links in channel %s", message.channel.id)


async def setup(bot):
    await bot.add_cog(EmbedFixer(bot))
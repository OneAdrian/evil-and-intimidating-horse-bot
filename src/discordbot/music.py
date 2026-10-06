import asyncio
import colorsys
import html
import io
import logging
import os
import re
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from urllib.parse import quote

import aiohttp
import discord
from PIL import Image
from cachetools import TTLCache
from discord import app_commands
from discord.ext import commands

log = logging.getLogger("music_resolver")

TIMEOUT = aiohttp.ClientTimeout(total=8)
MAX_LINKS_PER_MESSAGE = 3
BASE_COLOR = (134, 77, 232)
EMBED_COLOR = discord.Color.from_rgb(*BASE_COLOR)
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MIN_SATURATION = 0.4
MIN_BRIGHTNESS = 0.35
MIN_VIVID_SHARE = 0.05
HUE_BINS = 12

SPOTIFY_URL = re.compile(
    r"https?://open\.spotify\.com/(?:intl-[a-z]{2}/)?(?P<kind>track|album|artist)/(?P<id>[A-Za-z0-9]{22})",
    re.I,
)
APPLE_URL = re.compile(
    r"https?://(?:geo\.)?music\.apple\.com/(?P<country>[a-z]{2})/(?P<kind>album|song|artist)/"
    r"(?:[^/?#\s>]+/)?(?P<id>\d+)(?:\?[^\s>]*?\bi=(?P<track>\d+))?",
    re.I,
)
YOUTUBE_MUSIC_URL = re.compile(
    r"https?://music\.youtube\.com/(?:watch\?(?:[^\s>]*?&)?v=(?P<video>[\w-]{11})"
    r"|playlist\?(?:[^\s>]*?&)?list=(?P<playlist>[\w-]+)|channel/(?P<channel>UC[\w-]{22}))",
    re.I,
)
TIDAL_URL = re.compile(
    r"https?://(?:listen\.)?tidal\.com/(?:browse/)?(?P<kind>track|album|artist)/(?P<id>\d+)",
    re.I,
)
DEEZER_URL = re.compile(
    r"https?://(?:www\.)?deezer\.com/(?:[a-z]{2}(?:-[a-z]{2})?/)?(?P<kind>track|album|artist)/(?P<id>\d+)",
    re.I,
)
DEEZER_SHORT_URL = re.compile(r"https?://(?:deezer\.page\.link|link\.deezer\.com/s)/[\w-]+", re.I)


class ResolverError(Exception):
    pass


class RateLimited(ResolverError):
    def __init__(self, retry_after):
        super().__init__(f"rate limited for {retry_after:.0f}s")
        self.retry_after = retry_after


class Unresolvable(ResolverError):
    pass


class ProviderTimeout(ResolverError):
    pass


@dataclass(frozen=True)
class Ref:
    platform: str
    kind: str
    ident: str
    country: str = "us"

    @property
    def key(self):
        return (self.platform, self.kind, self.ident)


@dataclass
class Media:
    kind: str
    title: str
    artist: str
    artwork: str | None = None
    isrc: str | None = None
    upc: str | None = None
    links: dict[str, str] = field(default_factory=dict)


@dataclass
class Match:
    url: str
    artwork: str | None = None
    isrc: str | None = None
    upc: str | None = None


@dataclass
class Candidate:
    title: str
    artist: str
    url: str
    artwork: str | None = None
    ident: str | None = None


def retry_delay(value):
    try:
        return min(float(value), 3600.0)
    except (TypeError, ValueError):
        return 30.0


async def request_json(http, method, url, **kwargs):
    try:
        async with http.request(method, url, timeout=TIMEOUT, **kwargs) as response:
            if response.status == 429:
                raise RateLimited(retry_delay(response.headers.get("Retry-After")))
            if response.status == 404:
                raise Unresolvable(url)
            response.raise_for_status()
            return await response.json(content_type=None)
    except asyncio.TimeoutError as error:
        raise ProviderTimeout(url) from error


def normalize(text):
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())


def artist_similarity(first, second):
    first, second = normalize(first), normalize(second)
    if first and second and (first in second or second in first):
        return 1.0
    return SequenceMatcher(None, first, second).ratio()


def best_match(candidates, media, threshold=0.78):
    if media.kind == "artist":
        threshold = max(threshold, 0.9)
    best, best_score = None, 0.0
    for candidate in candidates:
        title_score = SequenceMatcher(None, normalize(candidate.title), normalize(media.title)).ratio()
        if media.kind == "artist":
            score = title_score
        else:
            score = 0.65 * title_score + 0.35 * artist_similarity(candidate.artist, media.artist)
        if score > best_score:
            best, best_score = candidate, score
    return best if best_score >= threshold else None


def to_ean(upc):
    return upc.lstrip("0").zfill(13)


def split_youtube(title, channel):
    title = html.unescape(title)
    channel = html.unescape(channel)
    if channel.endswith(" - Topic"):
        return title, channel[: -len(" - Topic")]
    if " - " in title:
        artist, _, name = title.partition(" - ")
        return name.strip(), artist.strip()
    return title, channel


def dominant_color(data):
    with Image.open(io.BytesIO(data)) as image:
        image.draft("RGB", (96, 96))
        small = image.convert("RGB")
    small.thumbnail((48, 48))
    pixels = list(small.getdata())
    hues = {}
    for pixel in pixels:
        hue, saturation, value = colorsys.rgb_to_hsv(*(channel / 255 for channel in pixel))
        if saturation >= MIN_SATURATION and value >= MIN_BRIGHTNESS:
            hues.setdefault(int(hue * HUE_BINS + 0.5) % HUE_BINS, []).append(pixel)
    if sum(len(group) for group in hues.values()) >= len(pixels) * MIN_VIVID_SHARE:
        group = max(hues.values(), key=len)
    else:
        buckets = {}
        for pixel in pixels:
            buckets.setdefault(tuple(channel >> 5 for channel in pixel), []).append(pixel)
        group = max(buckets.values(), key=len)
    return tuple(round(sum(channel) / len(group)) for channel in zip(*group))


def extract_refs(text):
    found = {}

    def add(ref):
        found.setdefault(ref.key, ref)

    for match in SPOTIFY_URL.finditer(text):
        add(Ref("spotify", match["kind"].lower(), match["id"]))
    for match in APPLE_URL.finditer(text):
        country = match["country"].lower()
        if match["track"]:
            add(Ref("apple", "track", match["track"], country))
        elif match["kind"].lower() == "song":
            add(Ref("apple", "track", match["id"], country))
        elif match["kind"].lower() == "artist":
            add(Ref("apple", "artist", match["id"], country))
        else:
            add(Ref("apple", "album", match["id"], country))
    for match in YOUTUBE_MUSIC_URL.finditer(text):
        if match["video"]:
            add(Ref("youtube", "track", match["video"]))
        elif match["channel"]:
            add(Ref("youtube", "artist", match["channel"]))
        else:
            add(Ref("youtube", "album", match["playlist"]))
    for match in TIDAL_URL.finditer(text):
        add(Ref("tidal", match["kind"].lower(), match["id"]))
    for match in DEEZER_URL.finditer(text):
        add(Ref("deezer", match["kind"].lower(), match["id"]))
    return list(found.values())


class ClientCredentials:
    def __init__(self, http, token_url, client_id, client_secret):
        self.http = http
        self.token_url = token_url
        self.auth = aiohttp.BasicAuth(client_id, client_secret)
        self.token = None
        self.expires_at = 0.0
        self.lock = asyncio.Lock()

    async def headers(self):
        async with self.lock:
            if self.token is None or time.monotonic() >= self.expires_at:
                data = await request_json(
                    self.http,
                    "POST",
                    self.token_url,
                    data={"grant_type": "client_credentials"},
                    auth=self.auth,
                )
                self.token = data["access_token"]
                self.expires_at = time.monotonic() + int(data.get("expires_in", 3600)) - 60
        return {"Authorization": f"Bearer {self.token}"}


class Provider:
    key = ""
    label = ""
    emoji = ""

    def __init__(self, http):
        self.http = http
        self.enabled = True

    def canonical(self, ref):
        raise NotImplementedError

    async def fetch(self, ref):
        raise NotImplementedError

    async def find(self, media):
        raise NotImplementedError


class Spotify(Provider):
    key = "spotify"
    label = "Spotify"
    emoji = "<:spotify:YOUR-SPOTIFY-EMOJI-ID-HERE>"
    api = "https://api.spotify.com/v1"

    def __init__(self, http, client_id, client_secret):
        super().__init__(http)
        self.enabled = bool(client_id and client_secret)
        self.credentials = (
            ClientCredentials(http, "https://accounts.spotify.com/api/token", client_id, client_secret)
            if self.enabled
            else None
        )

    def canonical(self, ref):
        return f"https://open.spotify.com/{ref.kind}/{ref.ident}"

    @staticmethod
    def candidate(item, kind):
        album = item["album"] if kind == "track" else item
        images = album.get("images") or []
        return Candidate(
            item["name"],
            ", ".join(artist["name"] for artist in item.get("artists", [])),
            item["external_urls"]["spotify"],
            images[0]["url"] if images else None,
        )

    async def fetch(self, ref):
        data = await request_json(
            self.http, "GET", f"{self.api}/{ref.kind}s/{ref.ident}", headers=await self.credentials.headers()
        )
        candidate = self.candidate(data, ref.kind)
        external_ids = data.get("external_ids") or {}
        return Media(
            ref.kind,
            candidate.title,
            candidate.artist,
            candidate.artwork,
            external_ids.get("isrc"),
            external_ids.get("upc"),
        )

    @staticmethod
    def rank(item, query, artist):
        score = SequenceMatcher(None, normalize(item["name"]), normalize(query)).ratio()
        if artist:
            score += max((artist_similarity(entry["name"], artist) for entry in item.get("artists", [])), default=0.0)
        return score

    async def top_result(self, query, kind, artist=None, album=None):
        def clean(text):
            return text.replace('"', " ").strip()

        if kind == "artist":
            attempts = [query]
        else:
            strict = f'{kind}:"{clean(query)}"'
            if artist:
                strict += f' artist:"{clean(artist)}"'
            if album and kind == "track":
                strict += f' album:"{clean(album)}"'
            attempts = [strict, " ".join(filter(None, (query, artist, album if kind == "track" else None)))]
        headers = await self.credentials.headers()
        for text in attempts:
            data = await request_json(
                self.http,
                "GET",
                f"{self.api}/search",
                params={"q": text, "type": kind, "limit": 10},
                headers=headers,
            )
            items = [item for item in (data.get(f"{kind}s") or {}).get("items") or [] if item]
            if items:
                best = max(items, key=lambda item: self.rank(item, query, artist))
                return best["external_urls"]["spotify"]
        return None

    async def find(self, media):
        headers = await self.credentials.headers()
        identifier = None
        if media.kind == "track" and media.isrc:
            identifier = f"isrc:{media.isrc}"
        elif media.kind == "album" and media.upc:
            identifier = f"upc:{media.upc}"
        for query in filter(None, (identifier, f"{media.title} {media.artist}")):
            data = await request_json(
                self.http,
                "GET",
                f"{self.api}/search",
                params={"q": query, "type": media.kind, "limit": 5},
                headers=headers,
            )
            items = (data.get(f"{media.kind}s") or {}).get("items") or []
            candidates = [self.candidate(item, media.kind) for item in items if item]
            if query == identifier and candidates:
                chosen = candidates[0]
            else:
                chosen = best_match(candidates, media)
            if chosen:
                return Match(chosen.url, chosen.artwork)
        return None


class AppleMusic(Provider):
    key = "apple"
    label = "Apple Music"
    emoji = "<:applemusic:YOUR-APPLE-EMOJI-ID-HERE>"
    api = "https://itunes.apple.com"

    def canonical(self, ref):
        path = {"track": "song", "album": "album", "artist": "artist"}[ref.kind]
        return f"https://music.apple.com/{ref.country}/{path}/{ref.ident}"

    @staticmethod
    def artwork(url):
        return url.replace("100x100bb", "600x600bb") if url else None

    @staticmethod
    def clean(url):
        return re.sub(r"[?&]uo=\d+", "", url)

    async def fetch(self, ref):
        data = await request_json(
            self.http, "GET", f"{self.api}/lookup", params={"id": ref.ident, "country": ref.country}
        )
        if not data["results"]:
            raise Unresolvable(ref.ident)
        item = data["results"][0]
        if ref.kind == "artist":
            return Media(ref.kind, item["artistName"], "")
        return Media(
            ref.kind,
            item.get("trackName") or item["collectionName"],
            item["artistName"],
            self.artwork(item.get("artworkUrl100")),
        )

    async def find(self, media):
        if media.kind == "album" and media.upc:
            data = await request_json(
                self.http, "GET", f"{self.api}/lookup", params={"upc": media.upc, "country": "us"}
            )
            albums = [item for item in data["results"] if item.get("collectionViewUrl")]
            if albums:
                return Match(self.clean(albums[0]["collectionViewUrl"]), self.artwork(albums[0].get("artworkUrl100")))
        data = await request_json(
            self.http,
            "GET",
            f"{self.api}/search",
            params={
                "term": f"{media.title} {media.artist}",
                "media": "music",
                "entity": {"track": "song", "album": "album", "artist": "musicArtist"}[media.kind],
                "limit": 5,
                "country": "us",
            },
        )
        if media.kind == "artist":
            candidates = [
                Candidate(item["artistName"], "", item["artistLinkUrl"])
                for item in data["results"]
                if item.get("artistLinkUrl")
            ]
        else:
            candidates = [
                Candidate(
                    item.get("trackName") or item["collectionName"],
                    item["artistName"],
                    item.get("trackViewUrl") or item["collectionViewUrl"],
                    self.artwork(item.get("artworkUrl100")),
                )
                for item in data["results"]
                if item.get("trackViewUrl") or item.get("collectionViewUrl")
            ]
        chosen = best_match(candidates, media)
        return Match(self.clean(chosen.url), chosen.artwork) if chosen else None


class YouTubeMusic(Provider):
    key = "youtube"
    label = "YouTube Music"
    emoji = "<:youtubemusic:YOUR-YOUTUBE-EMOJI-ID-HERE>"
    api = "https://www.googleapis.com/youtube/v3"

    def __init__(self, http, api_key):
        super().__init__(http)
        self.api_key = api_key
        self.enabled = bool(api_key)

    @staticmethod
    def link(kind, ident):
        if kind == "artist":
            return f"https://music.youtube.com/channel/{ident}"
        if kind == "track":
            return f"https://music.youtube.com/watch?v={ident}"
        return f"https://music.youtube.com/playlist?list={ident}"

    def canonical(self, ref):
        return self.link(ref.kind, ref.ident)

    @staticmethod
    def thumbnail(snippet):
        thumbnails = snippet.get("thumbnails", {})
        for size in ("maxres", "standard", "high", "medium", "default"):
            if size in thumbnails:
                return thumbnails[size]["url"]
        return None

    async def get(self, path, **params):
        try:
            return await request_json(self.http, "GET", f"{self.api}/{path}", params={**params, "key": self.api_key})
        except aiohttp.ClientResponseError as error:
            if error.status == 403:
                raise RateLimited(3600.0) from error
            raise

    async def fetch(self, ref):
        endpoint = {"track": "videos", "album": "playlists", "artist": "channels"}[ref.kind]
        data = await self.get(endpoint, part="snippet", id=ref.ident)
        if not data.get("items"):
            raise Unresolvable(ref.ident)
        snippet = data["items"][0]["snippet"]
        if ref.kind == "artist":
            return Media(ref.kind, re.sub(r" - Topic$", "", html.unescape(snippet["title"])), "", self.thumbnail(snippet))
        title, artist = split_youtube(snippet["title"], snippet["channelTitle"])
        if ref.kind == "album":
            title = re.sub(r"^Album - ", "", title)
        return Media(ref.kind, title, artist, self.thumbnail(snippet))

    async def find(self, media):
        kind = {"track": "video", "album": "playlist", "artist": "channel"}[media.kind]
        if kind == "channel":
            query = media.title
        else:
            query = f"{media.title} {media.artist}" + ("" if kind == "video" else " album")
        params = {"part": "snippet", "type": kind, "q": query, "maxResults": 5}
        if kind == "video":
            params["videoCategoryId"] = "10"
        data = await self.get("search", **params)
        candidates = []
        for item in data.get("items", []):
            ident = item["id"].get("videoId") or item["id"].get("playlistId") or item["id"].get("channelId")
            if not ident:
                continue
            if media.kind == "artist":
                title, artist = re.sub(r" - Topic$", "", html.unescape(item["snippet"]["title"])), ""
            else:
                title, artist = split_youtube(item["snippet"]["title"], item["snippet"]["channelTitle"])
            candidates.append(Candidate(title, artist, self.link(media.kind, ident), self.thumbnail(item["snippet"])))
        chosen = best_match(candidates, media, threshold=0.7)
        return Match(chosen.url, chosen.artwork) if chosen else None


class Tidal(Provider):
    key = "tidal"
    label = "Tidal"
    emoji = "<:tidal:YOUR-TIDAL-EMOJI-ID-HERE>"
    api = "https://openapi.tidal.com/v2"

    def __init__(self, http, client_id, client_secret):
        super().__init__(http)
        self.enabled = bool(client_id and client_secret)
        self.credentials = (
            ClientCredentials(http, "https://auth.tidal.com/v1/oauth2/token", client_id, client_secret)
            if self.enabled
            else None
        )

    def canonical(self, ref):
        return f"https://tidal.com/browse/{ref.kind}/{ref.ident}"

    async def headers(self):
        return {**await self.credentials.headers(), "Accept": "application/vnd.api+json"}

    @staticmethod
    def artwork(item):
        links = ((item or {}).get("attributes") or {}).get("imageLinks") or []
        if not links:
            return None
        return max(links, key=lambda link: (link.get("meta") or {}).get("width", 0))["href"]

    async def fetch(self, ref):
        include = {"track": "artists,albums", "album": "artists", "artist": ""}[ref.kind]
        params = {"countryCode": "US", **({"include": include} if include else {})}
        data = await request_json(
            self.http,
            "GET",
            f"{self.api}/{ref.kind}s/{ref.ident}",
            params=params,
            headers=await self.headers(),
        )
        main = data["data"]
        if ref.kind == "artist":
            return Media(ref.kind, main["attributes"]["name"], "", self.artwork(main))
        included = data.get("included", [])
        artists = ", ".join(item["attributes"]["name"] for item in included if item["type"] == "artists")
        album = next((item for item in included if item["type"] == "albums"), main if ref.kind == "album" else None)
        return Media(
            ref.kind,
            main["attributes"]["title"],
            artists,
            self.artwork(album),
            main["attributes"].get("isrc"),
            main["attributes"].get("barcodeId"),
        )

    async def find_artist(self, media):
        data = await request_json(
            self.http,
            "GET",
            f"{self.api}/searchResults/{quote(media.title, safe='')}",
            params={"countryCode": "US", "include": "artists"},
            headers=await self.headers(),
        )
        candidates = [
            Candidate(item["attributes"]["name"], "", f"https://tidal.com/browse/artist/{item['id']}", self.artwork(item))
            for item in data.get("included", [])
            if item["type"] == "artists"
        ]
        chosen = best_match(candidates, media)
        return Match(chosen.url, chosen.artwork) if chosen else None

    async def find(self, media):
        if media.kind == "artist":
            return await self.find_artist(media)
        if media.kind == "track" and media.isrc:
            path, params = "tracks", {"filter[isrc]": media.isrc}
        elif media.kind == "album" and media.upc:
            path, params = "albums", {"filter[barcodeId]": to_ean(media.upc)}
        else:
            return None
        data = await request_json(
            self.http,
            "GET",
            f"{self.api}/{path}",
            params={**params, "countryCode": "US"},
            headers=await self.headers(),
        )
        if not data.get("data"):
            return None
        return Match(f"https://tidal.com/browse/{media.kind}/{data['data'][0]['id']}")


class Deezer(Provider):
    key = "deezer"
    label = "Deezer"
    emoji = "<:deezer:YOUR-DEEZER-EMOJI-ID-HERE>"
    api = "https://api.deezer.com"

    def canonical(self, ref):
        return f"https://www.deezer.com/{ref.kind}/{ref.ident}"

    async def get(self, path, **params):
        data = await request_json(self.http, "GET", f"{self.api}/{path}", params=params or None)
        error = data.get("error") if isinstance(data, dict) else None
        if error:
            if error.get("code") == 4:
                raise RateLimited(5.0)
            raise Unresolvable(path)
        return data

    @staticmethod
    def cover(kind, data):
        if kind == "artist":
            return data.get("picture_xl")
        album = data.get("album") or {} if kind == "track" else data
        return album.get("cover_xl")

    async def fetch(self, ref):
        data = await self.get(f"{ref.kind}/{ref.ident}")
        if ref.kind == "artist":
            return Media(ref.kind, data["name"], "", self.cover(ref.kind, data))
        return Media(
            ref.kind,
            data["title"],
            data["artist"]["name"],
            self.cover(ref.kind, data),
            data.get("isrc"),
            data.get("upc"),
        )

    def to_match(self, kind, data):
        return Match(data["link"], self.cover(kind, data), data.get("isrc"), data.get("upc"))

    async def find(self, media):
        path = None
        if media.kind == "track" and media.isrc:
            path = f"track/isrc:{media.isrc}"
        elif media.kind == "album" and media.upc:
            path = f"album/upc:{to_ean(media.upc)}"
        if path:
            try:
                return self.to_match(media.kind, await self.get(path))
            except Unresolvable:
                pass
        data = await self.get(f"search/{media.kind}", q=f"{media.title} {media.artist}".strip(), limit=5)
        if media.kind == "artist":
            candidates = [
                Candidate(item["name"], "", item["link"], ident=str(item["id"]))
                for item in data.get("data", [])
            ]
        else:
            candidates = [
                Candidate(item["title"], item["artist"]["name"], item["link"], ident=str(item["id"]))
                for item in data.get("data", [])
            ]
        chosen = best_match(candidates, media)
        if chosen is None:
            return None
        return self.to_match(media.kind, await self.get(f"{media.kind}/{chosen.ident}"))


class MusicResolver(commands.Cog):
    def __init__(self, bot, http):
        self.bot = bot
        self.http = http
        candidates = (
            Spotify(http, os.getenv("SPOTIFY_CLIENT_ID", ""), os.getenv("SPOTIFY_CLIENT_SECRET", "")),
            AppleMusic(http),
            YouTubeMusic(http, os.getenv("YOUTUBE_API_KEY", "")),
            Tidal(http, os.getenv("TIDAL_CLIENT_ID", ""), os.getenv("TIDAL_CLIENT_SECRET", "")),
            Deezer(http),
        )
        self.providers = {provider.key: provider for provider in candidates if provider.enabled}
        for provider in candidates:
            if not provider.enabled:
                log.info("%s disabled, missing credentials", provider.label)
        self.cache = TTLCache(maxsize=512, ttl=3600)
        self.failures = TTLCache(maxsize=256, ttl=300)
        self.colors = TTLCache(maxsize=512, ttl=3600)
        self.pending = {}
        self.cooldowns = {}

    async def cog_unload(self):
        await self.http.close()

    def cooling(self, provider):
        return time.monotonic() < self.cooldowns.get(provider.key, 0.0)

    def cool(self, provider, seconds):
        self.cooldowns[provider.key] = time.monotonic() + seconds
        log.warning("%s cooling down for %.0fs", provider.label, seconds)

    async def expand_short_links(self, text):
        expanded = []
        for url in DEEZER_SHORT_URL.findall(text)[:2]:
            try:
                async with self.http.get(url, allow_redirects=True, timeout=TIMEOUT) as response:
                    expanded.append(str(response.url))
            except (aiohttp.ClientError, asyncio.TimeoutError):
                log.warning("could not expand %s", url)
        return f"{text} {' '.join(expanded)}" if expanded else text

    async def safe_find(self, provider, media):
        if self.cooling(provider):
            return None
        try:
            return await provider.find(media)
        except RateLimited as error:
            self.cool(provider, error.retry_after)
        except Unresolvable:
            log.debug("%s found nothing for %s", provider.label, media.title)
        except (ProviderTimeout, aiohttp.ClientError) as error:
            log.warning("%s failed: %s", provider.label, error)
        except Exception:
            log.exception("%s lookup crashed", provider.label)
        return None

    @staticmethod
    def merge(media, provider, match):
        if match is None:
            return
        media.links[provider.key] = match.url
        media.artwork = media.artwork or match.artwork
        media.isrc = media.isrc or match.isrc
        media.upc = media.upc or match.upc

    async def build(self, ref):
        source = self.providers.get(ref.platform)
        if source is None:
            raise Unresolvable(f"{ref.platform} is not configured")
        if self.cooling(source):
            raise RateLimited(0.0)
        try:
            media = await source.fetch(ref)
        except RateLimited as error:
            self.cool(source, error.retry_after)
            raise
        media.links[source.key] = source.canonical(ref)
        remaining = [provider for provider in self.providers.values() if provider.key != source.key]
        deezer = self.providers.get("deezer")
        if deezer in remaining and media.kind != "artist" and not (media.isrc or media.upc):
            remaining.remove(deezer)
            self.merge(media, deezer, await self.safe_find(deezer, media))
        matches = await asyncio.gather(*(self.safe_find(provider, media) for provider in remaining))
        for provider, match in zip(remaining, matches):
            self.merge(media, provider, match)
        return media

    async def resolve(self, ref):
        key = ref.key
        if key in self.cache:
            return self.cache[key]
        if key in self.failures:
            return None
        task = self.pending.get(key)
        if task is None:
            task = asyncio.create_task(self.build(ref))
            self.pending[key] = task
            task.add_done_callback(lambda _: self.pending.pop(key, None))
        try:
            media = await asyncio.shield(task)
        except RateLimited:
            return None
        except (Unresolvable, ProviderTimeout, aiohttp.ClientError) as error:
            log.warning("could not resolve %s: %s", key, error)
            self.failures[key] = True
            return None
        except Exception:
            log.exception("resolving %s crashed", key)
            self.failures[key] = True
            return None
        self.cache[key] = media
        return media

    def build_embed(self, media):
        lines = [
            f"{provider.emoji} [{provider.label}]({media.links[provider.key]})"
            for provider in self.providers.values()
            if provider.key in media.links
        ]
        embed = discord.Embed(title=media.title[:256], description="\n".join(lines), color=EMBED_COLOR)
        if media.artist:
            embed.set_author(name=media.artist[:256])
        if media.artwork:
            embed.set_thumbnail(url=media.artwork)
        embed.set_footer(text=media.kind.title())
        return embed

    def build_view(self, media):
        view = discord.ui.View(timeout=None)
        for provider in self.providers.values():
            url = media.links.get(provider.key)
            if url:
                view.add_item(
                    discord.ui.Button(
                        style=discord.ButtonStyle.link, label=provider.label, url=url, emoji=provider.emoji
                    )
                )
        return view

    async def artwork_color(self, url):
        if not url:
            return None
        if url in self.colors:
            return self.colors[url]
        color = None
        try:
            async with self.http.get(url, timeout=TIMEOUT) as response:
                response.raise_for_status()
                chunks, size = [], 0
                async for chunk in response.content.iter_chunked(65536):
                    size += len(chunk)
                    if size > MAX_IMAGE_BYTES:
                        raise ValueError("image too large")
                    chunks.append(chunk)
            color = await asyncio.to_thread(dominant_color, b"".join(chunks))
        except Exception:
            log.warning("could not read a color from %s", url, exc_info=True)
        self.colors[url] = color
        return color

    async def render(self, media):
        global EMBED_COLOR
        color = await self.artwork_color(media.artwork)
        EMBED_COLOR = discord.Color.from_rgb(*(color or BASE_COLOR))
        try:
            return {"embed": self.build_embed(media), "view": self.build_view(media)}
        finally:
            EMBED_COLOR = discord.Color.from_rgb(*BASE_COLOR)

    async def fail_search(self, interaction, text):
        try:
            await interaction.delete_original_response()
        except discord.HTTPException:
            pass
        await interaction.followup.send(text, ephemeral=True)

    @app_commands.command(name="search", description="Search for a song, artist or album, and get a link for every platform")
    @app_commands.describe(
        query="Song, Album or Artist name to search for",
        type="Optional: Kind of result you're looking for, e.g. Album, Song. If none, defaults to Song",
        artist="Optional: The artist, for songs and albums",
        album="Optional: The album, for songs",
    )
    @app_commands.choices(
        type=[
            app_commands.Choice(name="Artist", value="artist"),
            app_commands.Choice(name="Album", value="album"),
            app_commands.Choice(name="Song", value="track"),
        ]
    )
    async def search(
        self,
        interaction: discord.Interaction,
        query: app_commands.Range[str, 1, 200],
        type: app_commands.Choice[str] | None = None,
        artist: app_commands.Range[str, 1, 100] | None = None,
        album: app_commands.Range[str, 1, 100] | None = None,
    ):
        if type is None:
            type = app_commands.Choice(name="song", value="track")
        
        spotify = self.providers.get("spotify")
        if spotify is None:
            return await interaction.response.send_message("Spotify search isn't set up on this bot.", ephemeral=True)
        if self.cooling(spotify):
            return await interaction.response.send_message(
                "Spotify is rate limiting me right now, try again in a bit.", ephemeral=True
            )
        await interaction.response.defer()
        try:
            url = await spotify.top_result(query, type.value, artist, album)
        except RateLimited as error:
            self.cool(spotify, error.retry_after)
            return await self.fail_search(interaction, "Spotify is rate limiting me right now, try again in a bit.")
        except (ProviderTimeout, aiohttp.ClientError, Unresolvable) as error:
            log.warning("spotify search failed: %s", error)
            return await self.fail_search(interaction, "I couldn't reach Spotify, try again in a moment.")
        except Exception:
            log.exception("spotify search crashed")
            return await self.fail_search(interaction, "Something broke while searching Spotify.")
        if url is None:
            return await self.fail_search(
                interaction, f"Spotify found no {type.name.lower()} matching \"{query[:100]}\"."
            )
        refs = extract_refs(url)
        media = await self.resolve(refs[0]) if refs else None
        if media is None:
            return await self.fail_search(
                interaction, f"Spotify found {url} but I couldn't load its details right now. If this happens, please contact Adrian."
            )
        await interaction.followup.send(**await self.render(media))

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or message.guild is None or not message.content:
            return
        permissions = message.channel.permissions_for(message.guild.me)
        if not (permissions.send_messages and permissions.embed_links):
            return
        text = await self.expand_short_links(message.content)
        refs = extract_refs(text)[:MAX_LINKS_PER_MESSAGE]
        if not refs:
            return
        async with message.channel.typing():
            results = await asyncio.gather(*(self.resolve(ref) for ref in refs))
        for media in results:
            if media is None or len(media.links) < 2:
                continue
            try:
                payload = await self.render(media)
                await message.reply(**payload, mention_author=False)
            except discord.HTTPException:
                log.warning("could not send music embed in channel %s", message.channel.id)


async def setup(bot):
    session = aiohttp.ClientSession(headers={"User-Agent": "HomeBot/1.0"})
    await bot.add_cog(MusicResolver(bot, session))
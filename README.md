## What is evil and intimidating horse bot?
horsebot is just a little bot I made for me and my friends. I put it up on github so anyone could use it. In this readme you'll find a quick breakdown of the core logic and then a quick start guide.
## main.py
main.py creates `HomeBot`. It turns on the message-content intent, opens bot.db for any previous data through `Store`, and in `setup_hook` loads each extension and then runs tree.sync(), pushing the commands to discord. Fairly standard discord bot stuff.

There are two main behaviours:
- Slash commands happen exclusively when someone directly invokes them
	- `/soopinator`, `/search`, `/remind` (and it's whole family of commands, explained later), `/fromsoft`, `/sealect`, `/timezone`, `/time`, `/birthday`
- **Listeners** and **Loops** do *not* need to be invoked, and run on their own;
	- `on_message` handlers do music link embeds, embed fixer and the soopinator
	- `ticker` delivers due reminders
	- `daily_seal` posts the seal at 15:00 in the `/sealect`ed channel

**State** lives in one SQLite file (bot.db) through `Store`, a thin wrapper with `run`, `one`,`many`,`get_pref` and `set_pref`. `guild_prefs` also live here, and these hold the server settings for the bot, such as soopinator being on or the selected seal or birthday channel.

## Quick Start guide:
### Requirements:
- **Tools:**
	- `uv`: the only tool you need to install yourself! Downloads the correct version of python (3.13) if absent.
- **Dependencies** (all conveniently managed by uv):
	- `aiohttp`, `discord-py`, `fromsoft-generator`, `geonamescache`,`tzdata`, `cachetools` and `pillow`.
- **Environment Variables [IMPORTANT]**:

| VARIABLE                                        | NEEDED FOR                               | REQUIRED?                                                                                |
| ----------------------------------------------- | ---------------------------------------- | ---------------------------------------------------------------------------------------- |
| `DISCORD_TOKEN`                                 | literally everything lmao                | **yes**                                                                                  |
| `KLIPY_KEY`                                     | daily seal; potentially more if I add it | **yes**, as long as `src.discordbot.seal` is loaded, which it is by default              |
| `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET` | Spotify links and `/search`              | optional; Spotify will be skipped if absent, `/search` will not function                 |
| TIDAL_CLIENT_ID`and `TIDAL_CLIENT_SECRET`       | TIDAL matching                           | optional; TIDAL will be skipped if absent                                                |
| `YOUTUBE_API_KEY`                               | Youtube Music Matching                   | optional; YT will be skipped if absent                                                   |
| `FONT_PATH`                                     | the font for `/fromsoft`                 | completely optional, only needed if agmena.ttf is anywhere that is NOT next to `main.py` |

**Apple Music** and **Deezer** need no keys. As listed, if a music platform does not have credentials, it is skipped. `/search` is disabled if spotify keys are absent.
- **Files**:
	- **Font:** `agmena.ttf` must be in the folder you run the bot from, or `FONT_PATH` must point to it. In it's absence, `/fromsoft` is non-functional.
	- **Database:** `bot.db` is created automatically on the first run.
- **Discord Developer Portal**
	- **Message Content Intent:** Turn on this privileged intent. Music links, embed fixer and soopinator all require it.
	- **Invite scopes:** `bot` and `applications.commands`
	- **Bot Permissions:** `View Channels`, `Send Messages`, `Embed Links`, `Read Message History`, `Send Messages in Threads`, `Manage Server`
		- `Manage Server` is required for soopinator
### Quick Start Steps
- Install [uv](https://github.com/astral-sh/uv)
	- For linux/mac users:
		- curl -LsSf https://astral.sh/uv/install.sh | sh``
	- For Windows users:
		- powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
- Set up the project
	```sh
	git clone https://github.com/OneAdrian/evil-and-intimidating-horse-bot.git
	cd evil-and-intimidating-horse-bot
	uv add cachetools pillow
	uv sync
	```
	 Create an `.env` file next to `main.py` (THIS HOLDS SENSITIVE DATA; **DO NOT SHARE IT.**)
	```python
	DISCORD_TOKEN='your-bot-token'
	KLIPY_KEY='your-klipy-key'
	SPOTIFY_CLIENT_ID='your-spotify-client-id'
	SPOTIFY_CLIENT_SECRET='your-spotify-client-secret'
	TIDAL_CLIENT_ID='your-tidal-client-id'
	TIDAL_CLIENT_SECRET='your-tidal-client-secret'
	YOUTUBE_API_KEY='your-youtube-data-api-key'
	```
	 Put `agmena.ttf` in the project folder
 - When having created the bot on discord, with the permissions above listed, **make sure to upload the following emoji to the 'app emojis' slot (case sensitive):**
	 - `spotify`
	 - `deezer`
	 - `applemusic`
	 - `youtubemusic`
	 - `tidal`
 - After having done this, replace the placeholder emoji IDs with the ones from your bot dashboard. Example Below:
	```python
	class Spotify(Provider):
	    key = "spotify"
	    label = "Spotify"
	    emoji = "<:spotify:YOUR-SPOTIFY-EMOJI-ID>" # Simply replace the caps text.
	    api = "https://api.spotify.com/v1"
	```
 - Run the project!
	```sh
	uv run --env-file .env main.py
	```
- After inviting the bot:
	- A user with `Manage Server` permissions must run `/sealect` , `/birthday channel` and `/soopinator` to manage whether those features are enabled or disabled.
	- Enjoy!

## Command List

| COMMAND                | USAGE                                                                                                                                                                                                                                                                     | ARGS                                                                                                                                                                                                                                                                                                                                                            | SOURCE                                                           |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------- |
| `/birthday channel`    | Used by Server Managers to set the auto birthday message channel. `/birthday channel channel:general`                                                                                                                                                                     | N/A (only required args)                                                                                                                                                                                                                                                                                                                                        | `birthday.py`                                                    |
| `/birthday set`        | Used to set one's birthday. `/birthday set day:2 month:2`                                                                                                                                                                                                                 | N/A (only required args)                                                                                                                                                                                                                                                                                                                                        | `birthday.py`                                                    |
| `/fromsoft`            | Used to Verb a noun, fromsoft style!<br>`/fromsoft text: NOUN VERBED`                                                                                                                                                                                                     | - `color:` Hex code(s), or: gay trans bi lesbian nb pan mlm ace aro aroace intersex<br>- `hidden:` Only you see the output<br>- `all_caps:` Capitalises everything                                                                                                                                                                                              | [Credit: Yunruse](https://github.com/yunruse/fromsoft-generator) |
| `/remind add`          | Used to set a reminder. Time defaults to 1h, but can be changed with args.<br>`/remind add reminder: do laundry time: 2h`<br><br>**You may also add a reminder by right clicking a message, and selecting the bot under 'apps'**                                          | - `time:` Specifies time, such as 30m, 2d, 3600s, or a unix timestamp. Defaults to 1h.<br>- `recurring:` Mutually exclusive with `time:`, specify an interval, such as 15m, 2d, or a day and time, such as Saturdays 16:00. Uses your local time set with `/time` command, else defaults to UTC.<br>- `who:` target user. Default is sender. May be turned off. | `remind.py`                                                      |
| `/remind list`         | Used to list active reminders.<br>`/remind list`                                                                                                                                                                                                                          | - `who:` target user. Default is sender.                                                                                                                                                                                                                                                                                                                        | `remind.py`                                                      |
| `/remind others`       | Used to allow or block other people from reminding you. Allowed by default.<br>`/remind others allowed False`                                                                                                                                                             | N/A (only required args)                                                                                                                                                                                                                                                                                                                                        | `remind.py`                                                      |
| `/remind remove`       | Used to remove one reminder by its number, or all of them if a number is not specified. Admins may remove someone else's reminders.<br>`/remind remove number:2`                                                                                                          | - `who:` Admin-Only, allows removing reminders for someone else.<br>- `number:` Allows removing a specific reminder, using a number taken from `/remind list`                                                                                                                                                                                                   | `remind.py`                                                      |
| `/remind serverothers` | Used to disable third-party reminders server wide. Admin only.<br>`/remind serverothers allowed False`                                                                                                                                                                    | N/A (only required args)                                                                                                                                                                                                                                                                                                                                        | `remind.py`                                                      |
| `/sealect`             | Used by individuals with `Manage Channels` to select the daily seal channel.<br>`/sealect channel general`                                                                                                                                                                | N/A (only required args)                                                                                                                                                                                                                                                                                                                                        | `seal.py`                                                        |
| `/search`              | Used to search for a Song, Album or Artist. It is recommended to specify a song author when searching for a song.<br>`/search query:Jigsaw Falling Into Place artist: Radiohead`                                                                                          | - `type:` Kind of result you're looking for, e.g. Album or Song. **If none, defaults to song.**<br>- `artist:` Used to specify the author of the song or album.<br>- `album:` Used to specify what album the song you're searching for is from.                                                                                                                 | `music.py`                                                       |
| `/soopinator`          | Used to enable/disable the automatic server name changer. May only be used by users with the `Manage Server` permission. Default off.<br>`/soopinator enabled True`                                                                                                       | N/A (only required args)                                                                                                                                                                                                                                                                                                                                        | `fun.py`                                                         |
| `/time`                | Used to show someone's local time. If noone is specified, sender's time is specified. Required a timezone to be specified with `/timezone`.<br>`/time user @oneadrian`                                                                                                    | - `user`: specifies the user                                                                                                                                                                                                                                                                                                                                    | `timelogic.py`                                                   |
| `/timezone`            | Used to set one's timezone. **Very important for /time and /reminder add.**<br>Takes most inputs I could think of: city names, UTC/Z/GMT offsets or a common spelling timezone (e.g. CEST, BST, EST). **Should account for daylight savings.**<br>`/timezone zone London` | N/A (only required args)                                                                                                                                                                                                                                                                                                                                        | `timelogic.py`                                                   |
## Things you should know
- **Sync and restarts**: commands only reach Discord when the bot starts and syncs, so every change needs a restart. If I make any changes, and you apply them, it may happen that you need a quick ctrl+R to refresh your client.
- **.env:** PLEASEPLEASEPLEASEPLEASE DO NOT SHARE THIS!!! This is private information that leads directly back to your accounts.
- **Timeouts:** Every request for `music.py` has an 8-second limit. Beyond that it triggers a timeout.
- `/search` **function:** It doesn't *actually* search the web, rather it just queries spotify's search API and then feeds whatever link returns to it to the existing link matching logic. If a song isn't on spotify, it won't work.
- **Platform quirks:**
	- Tidal and Apple lookups use the US storefront.
	- Tidal's API is particularly fussy, so it may happen that some things don't resolve correctly. I am only human.
	- YouTube search should cost 100 quote units per call.
- Soop Rename is off by default.
- Environment variables *are required* for the bot to function. I explained how to set them up above.


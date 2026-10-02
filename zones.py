import re
from datetime import datetime, timedelta, timezone
from functools import cache
from geonamescache import GeonamesCache
from zoneinfo import ZoneInfo, available_timezones

fixed_names = {"UTC", "GMT", "Z"}
offset_shape = re.compile(r"(?:GMT|UTC)?([+-])(\d{1,2})(?::?(\d{2}))?")


@cache
def lookup_tables():
    by_full = {name.lower(): name for name in available_timezones()}
    by_city = {}
    for name in sorted(by_full.values()):
        by_city.setdefault(name.split("/")[-1].replace("_", " ").lower(), name)
    crowd = {}
    for place in sorted(GeonamesCache().get_cities().values(), key=lambda p: -p["population"]):
        crowd[place["timezone"]] = crowd.get(place["timezone"], 0) + place["population"]
        for label in (place["name"], *place["alternatenames"]):
            by_city.setdefault(label.lower(), place["timezone"])
    by_abbrev = {}
    ranked = []
    year = datetime.now().year
    for name, people in crowd.items():
        if name not in by_full.values():
            continue
        zone = ZoneInfo(name)
        winter = datetime(year, 1, 1, tzinfo=zone)
        summer = datetime(year, 7, 1, tzinfo=zone)
        rank = (winter.utcoffset() != summer.utcoffset(), people)
        ranked.append((rank, name))
        for stamp in (winter, summer):
            abbrev = stamp.tzname()
            if abbrev.isalpha() and (abbrev not in by_abbrev or rank > by_abbrev[abbrev][0]):
                by_abbrev[abbrev] = (rank, name)
    ordered = [name for rank, name in sorted(ranked, reverse=True)]
    return by_full, by_city, {abbrev: pair[1] for abbrev, pair in by_abbrev.items()}, ordered


def region_at_offset(minutes, ordered):
    wanted = timedelta(minutes=minutes)
    now = datetime.now(timezone.utc)
    for name in ordered:
        zone = ZoneInfo(name)
        if now.astimezone(zone).utcoffset() == wanted:
            return zone, name
    return None


def zone_from_text(raw):
    cleaned = raw.strip()
    squashed = cleaned.upper().replace(" ", "")
    if squashed in fixed_names:
        squashed = "GMT"
    by_full, by_city, by_abbrev, ordered = lookup_tables()
    found = offset_shape.fullmatch(squashed)
    if found:
        sign = -1 if found.group(1) == "-" else 1
        hours = int(found.group(2))
        minutes = int(found.group(3) or 0)
        if hours > 14 or minutes > 59:
            return None
        total = sign * (hours * 60 + minutes)
        if total != 0:
            region = region_at_offset(total, ordered)
            if region:
                return region
            return timezone(timedelta(minutes=total)), f"UTC{found.group(1)}{hours:02d}:{minutes:02d}"
        squashed = "GMT"
    place = cleaned.split(",")[0].strip().lower().replace("_", " ")
    name = by_abbrev.get(squashed) or by_full.get(cleaned.lower()) or by_city.get(place)
    if name is None:
        return None
    return ZoneInfo(name), name


def zone_for(store, user_id):
    row = store.one("select zone from zones where user_id = ?", user_id)
    if row is None:
        return None
    result = zone_from_text(row["zone"])
    return result[0] if result else None


def ordinal(number):
    if 11 <= number % 100 <= 13:
        return f"{number}th"
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


def long_stamp(moment):
    return f"{moment:%A}, {moment:%B} {ordinal(moment.day)}, {moment:%H:%M}"
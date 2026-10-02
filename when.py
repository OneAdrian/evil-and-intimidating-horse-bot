import re
from datetime import datetime, time, timedelta

unit_seconds = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
span_piece = re.compile(r"(\d+)\s*([smhdw])", re.IGNORECASE)
span_whole = re.compile(r"(?:\d+\s*[smhdw]\s*)+", re.IGNORECASE)
stamp_shape = re.compile(r"<t:(\d+)(?::\w)?>|(\d{9,13})")
clock_shape = re.compile(r"(\d{1,2}):(\d{2})")
weekday_names = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
daily_words = ("daily", "everyday")


def read_span(text):
    cleaned = text.strip()
    if not span_whole.fullmatch(cleaned):
        return None
    total = sum(
        int(amount) * unit_seconds[unit.lower()]
        for amount, unit in span_piece.findall(cleaned)
    )
    return total if total > 0 else None


def read_stamp(text):
    found = stamp_shape.fullmatch(text.strip())
    if found is None:
        return None
    value = int(found.group(1) or found.group(2))
    return value / 1000 if value > 10**11 else float(value)


def read_moment(text, now):
    span = read_span(text)
    if span is not None:
        return now + span
    return read_stamp(text)


def read_weekday(word):
    stem = word.lower().rstrip("s")
    if len(stem) < 3:
        return None
    for index, name in enumerate(weekday_names):
        if name.startswith(stem):
            return index
    return None


def read_clock_rule(text):
    pieces = text.split()
    if len(pieces) != 2:
        return None
    day_word, clock_word = pieces
    clocked = clock_shape.fullmatch(clock_word)
    if clocked is None:
        return None
    hour, minute = int(clocked.group(1)), int(clocked.group(2))
    if hour > 23 or minute > 59:
        return None
    if day_word.lower() in daily_words:
        return None, hour, minute
    weekday = read_weekday(day_word)
    if weekday is None:
        return None
    return weekday, hour, minute


def next_clock_fire(weekday, hour, minute, zone, after):
    local_after = datetime.fromtimestamp(after, zone)
    for step in range(8):
        day = local_after.date() + timedelta(days=step)
        if weekday is not None and day.weekday() != weekday:
            continue
        candidate = datetime.combine(day, time(hour, minute), tzinfo=zone)
        if candidate > local_after:
            return candidate.timestamp()

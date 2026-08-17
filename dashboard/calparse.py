"""One typed line -> a title, a day, and a span of minutes.

`gcalcli quick` used to do this by handing the whole string to Google, which
had two problems: the answer only exists after the event has been written, so
nothing can be shown to you first, and a time range like "9:25-10:30" comes
back interpreted in ways that are hard to predict and impossible to test
offline. Parsing here means the preview is real -- what you see under the box
is the argv that is about to run.

The grammar is deliberately small. It covers how *this* line gets typed:

    move to ND 8am-10am wednesday
    real analysis test on wednesday from 9:25-10:30
    dinner with Anna friday 6pm
    pset 4 due tomorrow
    bengal bouts final saturday

Anything it cannot find a time in becomes an all-day event on the day it found,
and anything it cannot find a day in lands today. Neither is an error: an
all-day event on the wrong day is one drag in Google, whereas refusing the line
means retyping it.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import NamedTuple

WEEKDAYS = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2, "weds": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}

MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}

# Words that only ever glued the date to the title. Stripped from the edges of
# what is left over, never from the middle -- "Coffee with Fr. Bill at the Dome"
# keeps its "at".
FILLERS = ("on", "at", "from", "this", "the", "for", "of", "starting", "starts")

# A leading verb. "add" is the one the box is documented with; the others cost
# nothing and are what you type when you are not thinking about it.
LEAD = re.compile(r"^\s*(?:add|new|create|schedule|put|book)\b\s*", re.I)

# 1-2 digits, optional :minutes, optional meridiem. Bounded so a course number
# (CSE 20289) and a room number can never be read as a clock.
_T = r"(\d{1,2})(?::(\d{2}))?\s*([ap]\.?m\.?)?"
TIME_RANGE = re.compile(
    rf"\b{_T}\s*(?:-|–|—|\bto\b|\buntil\b|\bthrough\b|\btil{{1,2}}\b)\s*{_T}\b", re.I
)
# A lone time needs a marker: either a meridiem or an explicit "at". A bare
# "4" in "pset 4" is not a time, and guessing that it is would be the parser's
# most annoying possible failure.
TIME_ONE = re.compile(rf"\b(?:at\s+)?{_T}\b", re.I)

ISO_DATE = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
NUM_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b")
MONTH_DATE = re.compile(
    rf"\b({'|'.join(MONTHS)})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", re.I
)
DATE_MONTH = re.compile(
    rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({'|'.join(MONTHS)})\.?\b", re.I
)
WEEKDAY_RE = re.compile(rf"\b(next|this|coming)?\s*({'|'.join(WEEKDAYS)})\b", re.I)
RELATIVE = re.compile(r"\b(today|tonight|tomorrow|tmrw|overmorrow)\b", re.I)
IN_N_DAYS = re.compile(r"\bin\s+(\d{1,3})\s+(day|week)s?\b", re.I)
ALLDAY_HINT = re.compile(r"\ball[- ]day\b", re.I)


class Parsed(NamedTuple):
    title: str
    date: dt.date
    start: dt.time | None
    end: dt.time | None
    warning: str | None = None

    @property
    def all_day(self) -> bool:
        return self.start is None

    @property
    def duration_minutes(self) -> int:
        if self.start is None or self.end is None:
            return 60
        lo = self.start.hour * 60 + self.start.minute
        hi = self.end.hour * 60 + self.end.minute
        return max(1, hi - lo)

    def when(self) -> str:
        """The `--when` argument. ISO, so gcalcli's dateutil parse is a no-op."""
        if self.start is None:
            return self.date.isoformat()
        return f"{self.date.isoformat()} {self.start.strftime('%H:%M')}"


def _cut(text: str, span: tuple[int, int]) -> str:
    """Remove a matched span, leaving one space where it was."""
    return f"{text[: span[0]]} {text[span[1] :]}"


def _meridiem(raw: str | None) -> str | None:
    if not raw:
        return None
    return "am" if raw.replace(".", "").lower().startswith("a") else "pm"


def _to_24h(hour: int, minute: int, mer: str | None) -> int:
    """Minutes since midnight."""
    if mer == "am":
        hour = 0 if hour == 12 else hour
    elif mer == "pm":
        hour = 12 if hour == 12 else hour + 12
    return hour * 60 + minute


def _guess_meridiem(hour: int) -> str:
    """What a bare hour means on a student's day.

    7 through 11 is morning, 12 through 6 is afternoon. The boundary is at 7
    rather than 6 because "6" unqualified is dinner far more often than it is
    a 6am start, and the one case this gets wrong -- an early gym slot -- is
    the one people write "6am" for anyway.
    """
    return "am" if 7 <= hour <= 11 else "pm"


def _extract_times(text: str) -> tuple[str, dt.time | None, dt.time | None, str | None]:
    match = TIME_RANGE.search(text)
    if match:
        h1, m1, p1, h2, m2, p2 = match.groups()
        mer1, mer2 = _meridiem(p1), _meridiem(p2)
        hour1, hour2 = int(h1), int(h2)
        if hour1 > 23 or hour2 > 23:
            return text, None, None, None

        # A meridiem on either end covers both: "9 to 10:30am" is a morning,
        # and "8am-10" does not stop being one at its end.
        if mer1 is None and mer2 is None:
            mer1 = _guess_meridiem(hour1)
            mer2 = mer1
        elif mer1 is None:
            mer1 = mer2
        elif mer2 is None:
            mer2 = mer1

        lo = _to_24h(hour1, int(m1 or 0), mer1)
        hi = _to_24h(hour2, int(m2 or 0), mer2)
        # "11-1" reads as 11am to 1pm, not as an event that ends two hours
        # before it starts. Only the end moves; the start is the anchor.
        if hi <= lo and mer2 == "am":
            hi = _to_24h(hour2, int(m2 or 0), "pm")
        warning = None
        if hi <= lo:
            hi = lo + 60
            warning = "end time read as before the start; used an hour instead"
        return (
            _cut(text, match.span()),
            dt.time(lo // 60 % 24, lo % 60),
            dt.time(hi // 60 % 24, hi % 60),
            warning,
        )

    for match in TIME_ONE.finditer(text):
        hour, minute, mer_raw = match.groups()
        mer = _meridiem(mer_raw)
        anchored = match.group(0).lower().lstrip().startswith("at")
        # No meridiem and no "at" means this digit is part of the title.
        if mer is None and not anchored:
            continue
        if int(hour) > 23:
            continue
        lo = _to_24h(int(hour), int(minute or 0), mer or _guess_meridiem(int(hour)))
        hi = lo + 60
        return (
            _cut(text, match.span()),
            dt.time(lo // 60 % 24, lo % 60),
            dt.time(hi // 60 % 24, hi % 60),
            None,
        )

    return text, None, None, None


def _extract_date(text: str, today: dt.date) -> tuple[str, dt.date, str | None]:
    if match := ISO_DATE.search(text):
        y, m, d = (int(g) for g in match.groups())
        if (date := _safe_date(y, m, d)) is not None:
            return _cut(text, match.span()), date, None

    if match := NUM_DATE.search(text):
        m, d, y = match.group(1), match.group(2), match.group(3)
        year = today.year if y is None else (2000 + int(y) if len(y) == 2 else int(y))
        if (date := _safe_date(year, int(m), int(d))) is not None:
            # A bare 8/19 that has already gone is next year's.
            if y is None and date < today:
                date = _safe_date(year + 1, int(m), int(d)) or date
            return _cut(text, match.span()), date, None

    for pattern, order in ((MONTH_DATE, "md"), (DATE_MONTH, "dm")):
        if match := pattern.search(text):
            name, day = (match.group(1), match.group(2)) if order == "md" else (
                match.group(2), match.group(1)
            )
            if (date := _safe_date(today.year, MONTHS[name.lower()], int(day))) is not None:
                if date < today:
                    date = _safe_date(today.year + 1, MONTHS[name.lower()], int(day)) or date
                return _cut(text, match.span()), date, None

    if match := RELATIVE.search(text):
        word = match.group(1).lower()
        offset = {"today": 0, "tonight": 0, "tomorrow": 1, "tmrw": 1, "overmorrow": 2}[word]
        return _cut(text, match.span()), today + dt.timedelta(days=offset), None

    if match := IN_N_DAYS.search(text):
        n, unit = int(match.group(1)), match.group(2).lower()
        ahead = dt.timedelta(days=n * (7 if unit == "week" else 1))
        return _cut(text, match.span()), today + ahead, None

    if match := WEEKDAY_RE.search(text):
        qualifier = (match.group(1) or "").lower()
        target = WEEKDAYS[match.group(2).lower()]
        ahead = (target - today.weekday()) % 7
        # Naming today's own weekday means today. Saying "next" pushes a full
        # week past that, which is the reading that surprises people least even
        # though English genuinely does not agree with itself here.
        if qualifier == "next":
            ahead += 7
        return _cut(text, match.span()), today + dt.timedelta(days=ahead), None

    return text, today, "no day found; used today"


def _safe_date(year: int, month: int, day: int) -> dt.date | None:
    try:
        return dt.date(year, month, day)
    except ValueError:
        return None


def _clean_title(text: str) -> str:
    words = [w for w in re.split(r"\s+", text.strip()) if w]
    while words and words[0].lower().strip(",") in FILLERS:
        words.pop(0)
    while words and words[-1].lower().strip(",") in FILLERS:
        words.pop()
    title = " ".join(words).strip(" ,-–—")
    title = re.sub(r"\s{2,}", " ", title)
    # First letter only. Title-casing would turn "move to ND" into "Move To
    # Nd", and what was typed is already how it should read.
    return title[:1].upper() + title[1:] if title else ""


def parse(text: str, today: dt.date) -> Parsed:
    """Read one line. Never raises -- the worst case is an all-day event today."""
    body = LEAD.sub("", text).strip()

    forced_all_day = bool(ALLDAY_HINT.search(body))
    if forced_all_day:
        body = ALLDAY_HINT.sub(" ", body)

    # Date first. "8/19" would otherwise be eaten by the time patterns, and
    # "9:25-10:30" is not a date under any of them.
    body, date, date_warning = _extract_date(body, today)
    if forced_all_day:
        start, end, time_warning = None, None, None
    else:
        body, start, end, time_warning = _extract_times(body)

    title = _clean_title(body)
    warning = time_warning or date_warning
    if not title:
        title = "(untitled)"
        warning = "nothing left for a title"
    return Parsed(title=title, date=date, start=start, end=end, warning=warning)

"""The positions CSV, read but never committed.

A Fidelity "Portfolio Positions" export dropped at `[paths].positions`. The
file is gitignored and this module is the only thing that opens it.

Two things about the shape of this parser are deliberate.

**It is tolerant of the export, not of one hand-trimmed copy of it.** Today's
file has had the `Account Number` / `Account Name` columns and the trailing
disclaimer block removed by hand. That is a per-download edit, and a parser
that *required* the trimmed shape would turn "I forgot to delete two columns"
into a broken panel. So columns are matched by name, unknown ones are ignored,
and the footer is detected rather than assumed absent -- a raw export works
unedited.

**No parse failure ever quotes a cell.** Warnings name the row, the column and
the symbol; never the value. The whole point of the file is that its numbers
stay in one place, and an error string is a place numbers escape to -- into the
log, into a screenshot, into a bug report. `_money` and friends therefore
return `None` rather than raising with the offending text attached.

Nothing here is cached in SQLite and nothing is written back. The file is the
record; the panel is a view of it.
"""

from __future__ import annotations

import csv
import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path

# --- column names ----------------------------------------------------------

# Keyed by the header text with apostrophes dropped, everything non-alphanumeric
# turned into a space, and the result collapsed and lowercased -- so "Today's
# Gain/Loss Dollar", "todays gain loss dollar" and "Today's gain/loss dollar"
# are all the same key. Fidelity has changed the capitalisation of these
# headers before now, and matching on the exact string is how that becomes a
# silently empty column.
COLUMNS: dict[str, str] = {
    "symbol": "symbol",
    "description": "description",
    "quantity": "quantity",
    "last price": "last_price",
    "last price change": "last_change",
    "current value": "value",
    "todays gain loss dollar": "day_gain",
    "todays gain loss percent": "day_gain_pct",
    "total gain loss dollar": "total_gain",
    "total gain loss percent": "total_gain_pct",
    "percent of account": "pct_of_account",
    "cost basis total": "cost_basis",
    "average cost basis": "avg_cost",
    "type": "account_type",
}

MONEY_FIELDS = ("last_price", "last_change", "value", "day_gain", "total_gain",
                "cost_basis", "avg_cost")
PERCENT_FIELDS = ("day_gain_pct", "total_gain_pct", "pct_of_account")

# Rows that are real lines in the export but not securities. Matched on the
# normalised symbol, because Fidelity writes them into the Symbol column.
NOT_A_HOLDING = {"pending activity", "cash", "cash & cash investments"}

# What Yahoo will accept. Ordinary tickers and class shares (BRK.B, RDS-A);
# not an option symbol (` -AAPL250117C150`), not "Pending Activity", not a
# blank. Anything rejected here still shows in the table -- it is only barred
# from being sent to the quote feed, where it would come back as an error row.
TICKER = re.compile(r"^[A-Z][A-Z0-9]{0,5}(?:[.-][A-Z]{1,2})?$")


def _key(header: str) -> str:
    """Normalise a header cell to a `COLUMNS` key."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", header.replace("'", "").lower()).split())


# --- cell parsing ----------------------------------------------------------

# Fidelity's stand-ins for "this column does not apply to this row" -- a money
# market fund has no cost basis, a pending trade has no quantity.
BLANK = {"", "--", "-", "n/a", "na", "none"}


def _number(raw: str | None) -> float | None:
    """A numeric cell, whatever decoration it arrived with.

    Handles `$1,234.56`, `-$45.67`, `($45.67)`, `+0.55%` and bare `12.345`
    with one path, because the decorations combine: `Today's Gain/Loss Dollar`
    is a signed currency and `Last Price Change` is a signed currency that is
    usually negative-in-parentheses on the same file where the gain column is
    negative-with-a-minus.

    Returns None for anything it cannot read. The caller records *that* a cell
    was unreadable; it never records what was in it.
    """
    if raw is None:
        return None
    text = raw.strip()
    if text.lower() in BLANK:
        return None
    # Accounting negatives, before the parentheses are stripped as punctuation.
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()").replace("$", "").replace(",", "").replace("%", "").strip()
    if text.startswith("+"):
        text = text[1:]
    try:
        value = float(text)
    except ValueError:
        return None
    return -value if negative else value


@dataclass(frozen=True)
class Position:
    """One row of the export.

    Every numeric field is optional and independently so: a money market fund
    has a quantity and a value but no cost basis, and `Pending Activity` has a
    value and nothing else. Modelling those as zero would put them into the
    totals as real numbers.
    """

    symbol: str
    description: str = ""
    account_type: str = ""
    quantity: float | None = None
    last_price: float | None = None
    last_change: float | None = None
    value: float | None = None
    day_gain: float | None = None
    day_gain_pct: float | None = None
    total_gain: float | None = None
    total_gain_pct: float | None = None
    pct_of_account: float | None = None
    cost_basis: float | None = None
    avg_cost: float | None = None

    @property
    def ticker(self) -> str | None:
        """The symbol as a quote feed would want it, or None if it is not one.

        Fidelity marks money market funds with a trailing `**` and footnotes
        some rows with a single `*`; both are annotation, not part of the
        symbol.
        """
        bare = self.symbol.strip().rstrip("*").upper()
        if bare.lower() in NOT_A_HOLDING:
            return None
        return bare if TICKER.match(bare) else None


@dataclass(frozen=True)
class Portfolio:
    positions: list[Position] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    """Rows or cells that could not be read. Never contains a cell value."""

    as_of: dt.datetime | None = None
    """The file's mtime. The export carries no timestamp of its own, and the
    positions are a snapshot of whenever it was downloaded."""

    @property
    def total_value(self) -> float | None:
        return _total(p.value for p in self.positions)

    @property
    def total_day_gain(self) -> float | None:
        return _total(p.day_gain for p in self.positions)

    @property
    def total_gain(self) -> float | None:
        return _total(p.total_gain for p in self.positions)

    @property
    def day_gain_pct(self) -> float | None:
        """Today's move as a percentage of what the account opened at.

        Derived rather than summed: `Percent Of Account` weights would have to
        be renormalised over exactly the rows that reported both a gain and a
        value, and the denominator here (value minus today's gain) is the
        opening value directly.
        """
        value, gain = self.total_value, self.total_day_gain
        if value is None or gain is None:
            return None
        opening = value - gain
        return round(gain / opening * 100, 2) if opening else None

    @property
    def total_gain_pct(self) -> float | None:
        gain, basis = self.total_gain, _total(p.cost_basis for p in self.positions)
        if gain is None or not basis:
            return None
        return round(gain / basis * 100, 2)

    def tickers(self) -> list[str]:
        """Quotable symbols, in file order, deduplicated.

        The same holding can appear twice in an export -- once per account --
        and the movements list wants one row for it.
        """
        seen: dict[str, None] = {}
        for position in self.positions:
            if ticker := position.ticker:
                seen.setdefault(ticker, None)
        return list(seen)


def _total(values) -> float | None:
    """Sum the values that are present, or None if none of them are.

    `sum()` of an all-empty column is 0.0, which on screen is a portfolio that
    is flat rather than one whose file could not be read.
    """
    present = [v for v in values if v is not None]
    return round(sum(present), 2) if present else None


# --- reading the file ------------------------------------------------------


def load(path: Path) -> Portfolio:
    """Read the positions CSV. A missing file is an empty portfolio, not an error.

    The file is expected to be absent -- it is gitignored, so a fresh clone has
    none -- and the panel says so rather than showing a stack trace.
    """
    if not path.exists():
        return Portfolio()

    # utf-8-sig: the export is UTF-8 with a BOM, and without this the first
    # header cell reads as "﻿Symbol" and the Symbol column goes missing.
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))

    as_of = dt.datetime.fromtimestamp(path.stat().st_mtime)
    header_at = _find_header(rows)
    if header_at is None:
        return Portfolio(warnings=["no header row: expected a line with Symbol and Quantity"],
                         as_of=as_of)

    columns = [COLUMNS.get(_key(cell)) for cell in rows[header_at]]
    if "symbol" not in columns:
        return Portfolio(warnings=["the header has no Symbol column"], as_of=as_of)

    positions: list[Position] = []
    warnings: list[str] = []
    for offset, row in enumerate(rows[header_at + 1:], start=header_at + 2):
        # The disclaimer block: a blank line, then prose in a single field.
        # Stopping on it rather than trying to parse it is what lets a raw
        # export work unedited.
        if len(row) < 4 or not any(cell.strip() for cell in row):
            break
        if (position := _position(row, columns, offset, warnings)) is not None:
            positions.append(position)

    return Portfolio(positions=positions, warnings=warnings, as_of=as_of)


def _find_header(rows: list[list[str]], search: int = 10) -> int | None:
    """Index of the header row.

    Searched for rather than assumed to be row 0: exports have arrived with a
    leading blank line, and an account-level summary line above the header.
    """
    for index, row in enumerate(rows[:search]):
        keys = {_key(cell) for cell in row}
        if "symbol" in keys and "quantity" in keys:
            return index
    return None


def _position(
    row: list[str], columns: list[str | None], line: int, warnings: list[str]
) -> Position | None:
    """One data row, or None if it has no symbol.

    Unreadable *cells* do not lose the row -- a position with a broken cost
    basis is still a position, and dropping it would quietly change the totals.
    """
    cells = {
        name: row[index].strip()
        for index, name in enumerate(columns)
        if name is not None and index < len(row)
    }
    symbol = cells.get("symbol", "")
    if not symbol:
        return None

    numbers: dict[str, float | None] = {}
    for name in ("quantity", *MONEY_FIELDS, *PERCENT_FIELDS):
        raw = cells.get(name)
        numbers[name] = _number(raw)
        # Only complain about a cell that had something in it. A blank cost
        # basis on a money market row is the file being correct.
        if numbers[name] is None and raw and raw.strip().lower() not in BLANK:
            warnings.append(f"line {line} ({symbol}): could not read {name.replace('_', ' ')}")

    return Position(
        symbol=symbol,
        description=cells.get("description", ""),
        account_type=cells.get("account_type", ""),
        **numbers,
    )


def tickers(path: Path) -> list[str]:
    """Just the quotable symbols. Used by the markets feed.

    A separate entry point so the feed never holds a `Position`: the symbol
    list is what the movements row needs, and quantities have no business
    travelling to the code that talks to Yahoo.
    """
    return load(path).tickers()

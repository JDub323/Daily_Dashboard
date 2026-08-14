"""The positions CSV, and the lock in front of it.

Every fixture here is fabricated. The real file is never opened by this suite,
by the developer tooling, or by anything outside `dashboard/portfolio.py` --
which is the whole arrangement the panel exists to preserve. The numbers below
were made up to exercise a shape, and any resemblance to a real position is
because AAPL is an obvious thing to type.

The shape they exercise is Fidelity's, in both the form it arrives in and the
form it is used in here: today's file has had the two account columns and the
trailing disclaimer block removed by hand, and that hand-edit must stay
optional. `test_raw_export_parses_unedited` is the one that pins that down.
"""

from __future__ import annotations

import datetime as dt

import pytest

from dashboard import portfolio

# The trimmed export: account columns removed, disclaimers removed.
# Rows are built from lists rather than written as literal CSV lines: fourteen
# decorated columns is well past the line limit, and a wrapped string literal
# makes it impossible to see which value is in which column -- which is the one
# thing a fixture for a column-matching parser has to make obvious.
HEADER = [
    "Symbol", "Description", "Quantity", "Last price", "Last price change",
    "Current value", "Today's gain/loss dollar", "Today's gain/loss percent",
    "Total gain/loss dollar", "Total gain/loss percent", "Percent of account",
    "Cost basis total", "Average cost basis", "Type",
]


def csv_row(*cells) -> str:
    return ",".join(str(c) for c in cells) + "\n"


TRIMMED = csv_row(*HEADER) + "".join([
    csv_row("AAPL", "APPLE INC", "10.000", "$220.00", "+$1.50", "$2200.00",
            "+$15.00", "+0.69%", "+$700.00", "+46.67%", "44.00%",
            "$1500.00", "$150.00", "Cash"),
    csv_row("NVDA", "NVIDIA CORP", "5.000", "$120.00", "-$2.00", "$600.00",
            "-$10.00", "-1.64%", "-$150.00", "-20.00%", "12.00%",
            "$750.00", "$150.00", "Cash"),
    csv_row("BRK.B", "BERKSHIRE HATHAWAY INC CL B", "2.000", "$470.00", "$0.00", "$940.00",
            "$0.00", "0.00%", "+$40.00", "+4.44%", "18.80%",
            "$900.00", "$450.00", "Cash"),
    # A money market fund: quantity and value, but no cost basis and no total
    # gain. The `**` is Fidelity's footnote marker, not part of the symbol.
    csv_row("SPAXX**", "FIDELITY GOVERNMENT MONEY MARKET", "1260.00", "$1.00", "$0.00",
            "$1260.00", "$0.00", "0.00%", "--", "--", "25.20%", "--", "--", "Cash"),
])

# The same file as downloaded: two extra leading columns, and the block of
# prose Fidelity appends after a blank line.
RAW = (
    # Title Case here as well as the trimmed fixture's lower case, on purpose:
    # Fidelity has changed the capitalisation of these headers before, and
    # matching on the exact string is how that becomes a silently empty column.
    csv_row("Account Number", "Account Name", *[h.title() for h in HEADER])
    + csv_row("Z00-000000", "Individual", "AAPL", "APPLE INC", "10.000", "$220.00",
              "+$1.50", "$2200.00", "+$15.00", "+0.69%", "+$700.00", "+46.67%",
              "44.00%", "$1500.00", "$150.00", "Cash")
    # A settled-but-unposted trade: a value and nothing else, and not a security.
    + csv_row("Z00-000000", "Individual", "Pending Activity", "", "", "", "",
              "$25.00", "", "", "", "", "0.50%", "", "", "")
    # The disclaimer block, after the blank line that ends the data.
    + "\n"
    + '"The data and information in this spreadsheet is provided for your use."\n'
    + '"Brokerage services are provided by Fidelity Brokerage Services LLC."\n'
)


def write(tmp_path, text, name="positions.csv"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# --- parsing ---------------------------------------------------------------


def test_reads_the_trimmed_export(tmp_path):
    held = portfolio.load(write(tmp_path, TRIMMED))
    assert [p.symbol for p in held.positions] == ["AAPL", "NVDA", "BRK.B", "SPAXX**"]
    assert not held.warnings

    apple = held.positions[0]
    assert apple.quantity == 10.0
    assert apple.last_price == 220.0
    assert apple.last_change == 1.5
    assert apple.value == 2200.0
    assert apple.day_gain_pct == 0.69
    assert apple.total_gain == 700.0
    assert apple.description == "APPLE INC"


def test_raw_export_parses_unedited(tmp_path):
    """The two account columns and the disclaimer block are both survivable.

    This is the test that makes the hand-edit optional rather than required.
    Without it, forgetting to delete two columns after a download is a broken
    panel with no obvious cause.
    """
    held = portfolio.load(write(tmp_path, RAW))
    assert [p.symbol for p in held.positions] == ["AAPL", "Pending Activity"]
    assert held.positions[0].value == 2200.0
    # The prose after the blank line is not a position, and not a warning
    # either -- it is a known part of the format.
    assert not held.warnings


def test_negatives_in_either_notation(tmp_path):
    held = portfolio.load(write(tmp_path, TRIMMED))
    nvidia = held.positions[1]
    assert nvidia.last_change == -2.0
    assert nvidia.day_gain == -10.0
    assert nvidia.day_gain_pct == -1.64
    assert nvidia.total_gain == -150.0


def test_accounting_parentheses_are_negative(tmp_path):
    row = csv_row("XOM", "EXXON MOBIL", "1.000", "$100.00", "($1.00)", "$100.00",
                  "($1.00)", "(0.99%)", "($5.00)", "(4.76%)", "1.00%",
                  "$105.00", "$105.00", "Cash")
    held = portfolio.load(write(tmp_path, TRIMMED + row))
    exxon = held.positions[-1]
    assert exxon.last_change == -1.0
    assert exxon.day_gain == -1.0
    assert exxon.total_gain == -5.0


def test_missing_cells_are_none_not_zero(tmp_path):
    """`--` means "does not apply", and a money market fund has no cost basis.

    Reading it as 0.0 would make the totals wrong in the direction that looks
    plausible, which is the worst kind of wrong for a number on a dashboard.
    """
    held = portfolio.load(write(tmp_path, TRIMMED))
    spaxx = held.positions[3]
    assert spaxx.cost_basis is None
    assert spaxx.total_gain is None
    assert spaxx.value == 1260.0


def test_totals_skip_the_missing_cells(tmp_path):
    held = portfolio.load(write(tmp_path, TRIMMED))
    assert held.total_value == 2200.0 + 600.0 + 940.0 + 1260.0
    assert held.total_day_gain == 15.0 - 10.0
    # SPAXX contributes no total gain and no basis, so neither the numerator
    # nor the denominator sees it.
    assert held.total_gain == 700.0 - 150.0 + 40.0
    assert held.total_gain_pct == round(590.0 / (1500.0 + 750.0 + 900.0) * 100, 2)


def test_day_pct_is_measured_against_the_opening_value(tmp_path):
    held = portfolio.load(write(tmp_path, TRIMMED))
    value, gain = held.total_value, held.total_day_gain
    assert held.day_gain_pct == round(gain / (value - gain) * 100, 2)


def test_an_empty_column_totals_to_none_not_zero(tmp_path):
    """A file whose money columns are all blank is unreadable, not flat."""
    text = csv_row(*HEADER) + csv_row("AAPL", "APPLE INC", "10.000", *["--"] * 10, "Cash")
    held = portfolio.load(write(tmp_path, text))
    assert held.total_value is None
    assert held.day_gain_pct is None


def test_a_bad_cell_keeps_the_row_and_warns_without_quoting_it(tmp_path):
    """The warning names the row and the column. Never the value.

    An error string is a place numbers escape to -- the log, a screenshot, a
    bug report -- and this file's whole point is that its numbers stay in it.
    """
    text = TRIMMED.replace("$2200.00", "twenty-two hundred")
    held = portfolio.load(write(tmp_path, text))
    assert [p.symbol for p in held.positions] == ["AAPL", "NVDA", "BRK.B", "SPAXX**"]
    assert held.positions[0].value is None
    assert held.warnings == ["line 2 (AAPL): could not read value"]
    assert "twenty-two hundred" not in " ".join(held.warnings)


def test_a_bom_does_not_hide_the_symbol_column(tmp_path):
    path = tmp_path / "bom.csv"
    path.write_text(TRIMMED, encoding="utf-8-sig")
    assert portfolio.load(path).positions[0].symbol == "AAPL"


def test_a_header_below_a_preamble_is_still_found(tmp_path):
    held = portfolio.load(write(tmp_path, "\nSome Account Summary\n\n" + TRIMMED))
    assert len(held.positions) == 4


def test_a_missing_file_is_empty_not_an_error(tmp_path):
    held = portfolio.load(tmp_path / "nothing.csv")
    assert held.positions == []
    assert held.warnings == []
    assert held.as_of is None


def test_a_headerless_file_says_so(tmp_path):
    held = portfolio.load(write(tmp_path, "just some text\nand more\n"))
    assert held.positions == []
    assert held.warnings and "header" in held.warnings[0]


def test_as_of_is_the_file_mtime(tmp_path):
    path = write(tmp_path, TRIMMED)
    assert isinstance(portfolio.load(path).as_of, dt.datetime)


# --- which symbols reach the quote feed ------------------------------------


@pytest.mark.parametrize(
    "symbol,expected",
    [
        ("AAPL", "AAPL"),
        ("BRK.B", "BRK.B"),
        ("RDS-A", "RDS-A"),
        ("SPAXX**", "SPAXX"),   # the footnote is annotation, not the symbol
        ("Pending Activity", None),
        ("Cash", None),
        (" -AAPL250117C150", None),  # an option contract, not a ticker
        ("", None),
    ],
)
def test_quotable_symbols(symbol, expected):
    assert portfolio.Position(symbol=symbol).ticker == expected


def test_tickers_are_deduplicated_in_file_order(tmp_path):
    """The same holding in two accounts is one row in the movements list."""
    duplicate = csv_row("AAPL", "APPLE INC", "4.000", "$220.00", "$0.00", "$880.00",
                        "$0.00", "0.00%", "$0.00", "0.00%", "1.00%",
                        "$880.00", "$220.00", "Margin")
    held = portfolio.load(write(tmp_path, TRIMMED + duplicate))
    assert held.tickers() == ["AAPL", "NVDA", "BRK.B", "SPAXX"]


def test_tickers_helper_never_returns_positions(tmp_path):
    """`portfolio.tickers` is what the markets feed calls.

    A separate entry point so quantities never travel to the code that talks
    to Yahoo -- the symbol list is all a movements row needs.
    """
    assert portfolio.tickers(write(tmp_path, TRIMMED)) == ["AAPL", "NVDA", "BRK.B", "SPAXX"]


# --- the merge into the movements list --------------------------------------


def test_holdings_are_appended_after_the_configured_tickers(settings, tmp_path):
    from dashboard.feeds import Feeds

    positions = str(write(tmp_path, TRIMMED))
    cfg = settings.model_copy(
        update={"paths": settings.paths.model_copy(update={"positions": positions})}
    )
    tickers = Feeds(cfg)._market_tickers()
    # Config first: a growing portfolio must not push ^SPX off the top.
    assert tickers[: len(cfg.markets.tickers)] == cfg.markets.tickers
    assert "NVDA" in tickers and tickers.count("NVDA") == 1  # already configured
    assert "BRK.B" in tickers and "SPAXX" in tickers


def test_holdings_can_be_switched_off(settings, tmp_path):
    from dashboard.feeds import Feeds

    cfg = settings.model_copy(
        update={
            "paths": settings.paths.model_copy(
                update={"positions": str(write(tmp_path, TRIMMED))}
            ),
            "markets": settings.markets.model_copy(update={"include_holdings": False}),
        }
    )
    assert Feeds(cfg)._market_tickers() == cfg.markets.tickers


def test_a_missing_positions_file_leaves_the_movements_list_alone(settings, tmp_path):
    """The markets panel must not break because of a spreadsheet."""
    from dashboard.feeds import Feeds

    cfg = settings.model_copy(
        update={
            "paths": settings.paths.model_copy(
                update={"positions": str(tmp_path / "absent.csv")}
            )
        }
    )
    assert Feeds(cfg)._market_tickers() == cfg.markets.tickers


def test_the_cache_key_follows_the_ticker_set(settings, tmp_path):
    """Editing the file shows up on the next reload, not ten minutes later.

    With a bare "markets" cache key, adding a position and refreshing would
    have served the old ticker list for the rest of the TTL and looked exactly
    like the edit not having taken.
    """
    from dashboard.feeds import Feeds

    path = tmp_path / "positions.csv"
    path.write_text(TRIMMED, encoding="utf-8")
    cfg = settings.model_copy(
        update={"paths": settings.paths.model_copy(update={"positions": str(path)})}
    )
    feeds = Feeds(cfg)
    before = feeds._market_tickers()
    added = csv_row("TSLA", "TESLA INC", "1.000", "$300.00", "$0.00", "$300.00",
                    "$0.00", "0.00%", "$0.00", "0.00%", "1.00%",
                    "$300.00", "$300.00", "Cash")
    path.write_text(TRIMMED + added, encoding="utf-8")
    assert feeds._market_tickers() == [*before, "TSLA"]


# --- the file must not be publishable ---------------------------------------


def test_positions_inside_the_static_root_is_a_startup_error(settings):
    """`[paths].static` is mounted at `/`.

    Anything beneath it is served to anyone who can reach the port, with no
    token and no Hello involved -- every other guard in this feature still in
    place and completely bypassed by a URL. Caught at startup because the way
    this goes wrong is a `[paths]` edit made months later for another reason.
    """
    import pydantic

    from dashboard.config import Settings

    # Rebuilt through `model_validate` rather than `model_copy`, because
    # `model_copy` does not re-run validators -- and a check that only fires on
    # a path nobody takes is not a check.
    data = settings.model_dump()
    data["paths"]["positions"] = "web/positions.csv"
    with pytest.raises(pydantic.ValidationError, match="static"):
        Settings.model_validate(data)


def test_class_shares_are_rewritten_for_yahoo():
    """`BRK.B` from the file is `BRK-B` at the quote feed.

    Caught by running the thing: the positions file supplies the one spelling
    Yahoo will not accept, and the symptom is a 404 that says nothing about
    punctuation.
    """
    from dashboard.feeds import _yahoo_symbol

    assert _yahoo_symbol("BRK.B") == "BRK-B"
    assert _yahoo_symbol("^SPX") == "^GSPC"
    assert _yahoo_symbol("AAPL") == "AAPL"

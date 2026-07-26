"""config.toml loading.

The point of validating config is that a typo is a startup error naming the
field, rather than a KeyError at 07:00 on the one panel that reads it.
"""

from __future__ import annotations

import textwrap

import pytest
from pydantic import ValidationError

from dashboard.config import Settings, load_settings, project_root

from .conftest import ROOT

MINIMAL = """
[llm]
model = "claude-sonnet-5"

[math]
topic_of_week = "generating functions"
[math.anchors]
amc = 1100
"aime-late" = 1500
"putnam-late" = 2050

[arxiv]
query = "cat:cs.AI"
"""


def write(tmp_path, body):
    path = tmp_path / "config.toml"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


def test_the_real_config_loads(settings):
    assert settings.math.topic_of_week
    assert settings.markets.tickers
    assert settings.llm.model


def test_the_shipped_config_defines_every_tier_the_prompt_offers(settings):
    """The prompt hands the anchor table to the model verbatim, so an empty or
    partial table would let it pick a tier that has no anchor."""
    assert len(settings.math.anchors) >= 3
    assert all(100 <= v <= 4000 for v in settings.math.anchors.values())


def test_a_minimal_config_takes_defaults(tmp_path):
    cfg = load_settings(write(tmp_path, MINIMAL))
    assert cfg.server.port == 8502
    assert cfg.server.host == "127.0.0.1"
    assert cfg.paths.db == "dashboard.db"


def test_an_unknown_key_is_rejected(tmp_path):
    """`extra="forbid"` — a misspelled key must not be silently ignored."""
    with pytest.raises(ValidationError, match="tickerz"):
        load_settings(write(tmp_path, MINIMAL + '\n[markets]\ntickerz = ["AAPL"]\n'))


def test_a_bad_port_is_rejected(tmp_path):
    with pytest.raises(ValidationError):
        load_settings(write(tmp_path, MINIMAL + "\n[server]\nport = 70000\n"))


def test_an_anchor_outside_the_rating_range_is_rejected(tmp_path):
    body = MINIMAL.replace("amc = 1100", "silly = 99999")
    with pytest.raises(ValidationError):
        load_settings(write(tmp_path, body))


def test_an_empty_anchor_table_is_rejected(tmp_path):
    body = (
        MINIMAL.replace("amc = 1100", "")
        .replace('"aime-late" = 1500', "")
        .replace('"putnam-late" = 2050', "")
    )
    with pytest.raises(ValidationError, match="at least one tier"):
        load_settings(write(tmp_path, body))


def test_a_non_http_feed_url_is_rejected(tmp_path):
    with pytest.raises(ValidationError):
        load_settings(
            write(tmp_path, MINIMAL + '\n[news]\nfeeds = [{cat="x", url="file:///etc/passwd"}]\n')
        )


def test_a_missing_file_says_so(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_settings(tmp_path / "nope.toml")


def test_the_env_var_selects_the_file(tmp_path, monkeypatch):
    path = write(tmp_path, MINIMAL + "\n[server]\nport = 9999\n")
    monkeypatch.setenv("DASHBOARD_CONFIG", str(path))
    assert load_settings().server.port == 9999


def test_paths_resolve_against_the_project_root_not_the_config_file(tmp_path, monkeypatch):
    """Pointing DASHBOARD_CONFIG at a scratch copy must change settings, not
    silently relocate dashboard.db next to that copy and open an empty one."""
    path = write(tmp_path, MINIMAL)
    monkeypatch.setenv("DASHBOARD_CONFIG", str(path))
    cfg = load_settings()
    assert cfg.path("db") == (project_root() / "dashboard.db").resolve()
    assert cfg.path("db").parent != tmp_path


def test_an_absolute_path_is_honoured(tmp_path):
    cfg = load_settings(write(tmp_path, MINIMAL + f'\n[paths]\ndb = "{tmp_path / "x.db"}"\n'))
    assert cfg.path("db") == (tmp_path / "x.db").resolve()


# --- derived values --------------------------------------------------------


def test_the_default_tier_is_the_median_anchor(tmp_path):
    """Not a hardcoded name: the tiers are config now, so a name baked into
    code would dangle the moment the table is edited."""
    cfg = load_settings(write(tmp_path, MINIMAL))
    assert cfg.math.default_tier == "aime-late"


def test_an_unpriced_model_gets_the_fallback():
    from dashboard.config import LlmSettings

    cfg = LlmSettings(model="x")
    assert cfg.price("anything") is LlmSettings.FALLBACK_PRICING


def test_the_topic_library_validates_its_links(settings):
    for entry in settings.topic_library.values():
        assert str(entry.textbook.url).startswith("http")
        assert entry.theorems


def test_the_shipped_config_matches_the_repo_layout(settings):
    """`static` must point at a directory that actually has an index.html, or
    the server boots and serves nothing."""
    assert (settings.path("static") / "index.html").exists()


def test_calendar_command_must_not_be_empty(tmp_path):
    with pytest.raises(ValidationError, match="must name an executable"):
        load_settings(write(tmp_path, MINIMAL + "\n[calendar]\ncommand = []\n"))


def test_settings_can_be_built_without_a_file():
    """The model has to stand on its own — the tests build one per fixture."""
    cfg = Settings(
        llm={"model": "m"},
        math={"topic_of_week": "t", "anchors": {"a": 1500}},
        arxiv={"query": "q"},
    )
    assert cfg.server.port == 8502


def test_the_real_config_and_launch_json_agree_on_the_port():
    """The plan's complaint: the port was defined twice and they could
    disagree. config.toml is the source of truth; this is the tripwire."""
    import json

    cfg = load_settings(ROOT / "config.toml")
    launch = json.loads((ROOT / ".claude" / "launch.json").read_text(encoding="utf-8"))
    ports = {c["port"] for c in launch["configurations"] if "port" in c}
    assert ports == {cfg.server.port}

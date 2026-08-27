"""The watch, without a watch.

Nothing here touches the network, and that is not only a test-hygiene rule --
it is the shape the module was built in. `sync` takes its client as an
argument precisely so the whole path from payload to row can be exercised
against a recorded shape, because the part of this integration most likely to
break is the part that reads Garmin's JSON, and it is unofficial JSON with no
published contract behind it.

What is worth pinning down, in order:

1. **A hand entry is never overwritten.** The one rule that decides whether
   correcting a bad reading is worth doing at all.
2. **One dead endpoint costs its own metrics and nothing else.** The failure
   this integration will actually have.
3. **Missing, null, zero and wrong-typed fields yield nothing** rather than a
   zero row, an exception, or a plausible-looking wrong number.
"""

from __future__ import annotations

import datetime as dt

import pytest

from dashboard import garmin
from dashboard.models.common import EntrySource, HealthMetric

from .conftest import TODAY

# A night, as `get_sleep_data` reports one. Trimmed to the fields the module
# reads; the real payload is some hundreds of lines of per-minute movement.
SLEEP = {
    "dailySleepDTO": {
        "sleepTimeSeconds": 25_200,  # seven hours
        "sleepStartTimestampLocal": 1_756_000_000_000,
        "sleepEndTimestampLocal": 1_756_029_000_000,  # 29,000s -- 8h03 in bed
        "sleepScores": {"overall": {"value": 78}},
    }
}

HRV = {"hrvSummary": {"lastNightAvg": 62}}
RHR = {"restingHeartRate": 48}
STATS = {"totalKilocalories": 3120.4, "bodyBatteryHighestValue": 91}
HYDRATION = {"valueInML": 2100.0}


class FakeWatch:
    """Every endpoint the module knows, answering the same day forever.

    `broken` names methods that raise, which is how a client that has been
    partly killed by an API change behaves -- not by disappearing, but by
    401ing one path and serving the rest.
    """

    def __init__(self, broken: set[str] | None = None, **payloads):
        self.broken = broken or set()
        self.calls: list[tuple[str, str]] = []
        self.payloads = {
            "get_sleep_data": SLEEP,
            "get_hrv_data": HRV,
            "get_rhr_day": RHR,
            "get_stats": STATS,
            "get_hydration_data": HYDRATION,
            **payloads,
        }

    def _answer(self, name, iso):
        self.calls.append((name, iso))
        if name in self.broken:
            raise RuntimeError(f"{name} is 401ing today")
        return self.payloads[name]

    def get_sleep_data(self, iso):
        return self._answer("get_sleep_data", iso)

    def get_hrv_data(self, iso):
        return self._answer("get_hrv_data", iso)

    def get_rhr_day(self, iso):
        return self._answer("get_rhr_day", iso)

    def get_stats(self, iso):
        return self._answer("get_stats", iso)

    def get_hydration_data(self, iso):
        return self._answer("get_hydration_data", iso)


# --- reading the payloads ---------------------------------------------------


def test_sleep_reads_hours_bed_time_and_score():
    got = garmin.sleep_metrics(SLEEP)
    assert got[HealthMetric.sleep_hours] == 7.0
    # Computed from the two timestamps rather than read from a field: the
    # field that reports it has had three names across the client's history
    # and the timestamps have been stable.
    assert got[HealthMetric.time_in_bed] == pytest.approx(8.06, abs=0.01)
    assert got[HealthMetric.sleep_score] == 78


def test_an_absurd_time_in_bed_is_dropped_rather_than_stored():
    """A clock change or a botched timestamp produces a number that sails
    through every CHECK on the table. Thirty-eight hours in bed is not a
    reading, it is a bug wearing one."""
    payload = {
        "dailySleepDTO": {
            "sleepStartTimestampLocal": 0,
            "sleepEndTimestampLocal": 38 * 3_600_000,
        }
    }
    assert HealthMetric.time_in_bed not in garmin.sleep_metrics(payload)


def test_a_night_on_the_charger_yields_nothing():
    assert garmin.sleep_metrics({}) == {}
    assert garmin.sleep_metrics({"dailySleepDTO": None}) == {}
    assert garmin.sleep_metrics({"dailySleepDTO": {"sleepTimeSeconds": 0}}) == {}


def test_resting_hr_reads_both_shapes_the_client_has_had():
    assert garmin.rhr_metrics(RHR)[HealthMetric.resting_hr] == 48
    nested = {
        "allMetrics": {"metricsMap": {"WELLNESS_RESTING_HEART_RATE": [{"value": 51}]}}
    }
    assert garmin.rhr_metrics(nested)[HealthMetric.resting_hr] == 51
    assert garmin.rhr_metrics({"restingHeartRate": None}) == {}


def test_body_battery_is_the_days_high_not_its_latest():
    """Read at 9pm, "most recent" measures how long your day was rather than
    how well you recovered from yesterday."""
    got = garmin.stats_metrics({**STATS, "bodyBatteryMostRecentValue": 12})
    assert got[HealthMetric.body_battery] == 91


def test_an_untapped_hydration_glyph_is_absent_not_zero():
    """Absent means "not logged", which `health.hydration_mean` skips. A zero
    would report thirst that was really forgetfulness -- and would fail the
    table's `value > 0` CHECK on the way in besides."""
    assert garmin.hydration_metrics({"valueInML": 0}) == {}
    assert garmin.hydration_metrics(HYDRATION)[HealthMetric.hydration_ml] == 2100.0


def test_a_boolean_is_not_a_number():
    """`isinstance(True, int)` is True in Python, so a payload with a flag
    where a value used to be would otherwise store 1.0."""
    assert garmin.stats_metrics({"totalKilocalories": True}) == {}


# --- one day, and one broken endpoint ---------------------------------------


def test_a_day_yields_every_metric():
    got = dict(garmin.read_day(FakeWatch(), TODAY))
    assert set(got) == {
        HealthMetric.sleep_hours,
        HealthMetric.time_in_bed,
        HealthMetric.sleep_score,
        HealthMetric.hrv,
        HealthMetric.resting_hr,
        HealthMetric.calories_out,
        HealthMetric.body_battery,
        HealthMetric.hydration_ml,
    }


def test_one_dead_endpoint_costs_only_its_own_metrics():
    """The failure this module actually expects. A 401 on hydration must not
    take last night's sleep with it."""
    got = dict(garmin.read_day(FakeWatch(broken={"get_hydration_data"}), TODAY))
    assert HealthMetric.hydration_ml not in got
    assert got[HealthMetric.sleep_hours] == 7.0


def test_a_payload_that_changed_shape_is_skipped_not_raised():
    watch = FakeWatch(get_stats=["a list, now, for some reason"])
    got = dict(garmin.read_day(watch, TODAY))
    assert HealthMetric.calories_out not in got
    assert got[HealthMetric.hrv] == 62


def test_a_client_missing_a_method_is_a_skip():
    """A pinned older `garminconnect` in the venv is a smaller problem than a
    crash on every boot.

    Its own class rather than an attribute deleted off `FakeWatch`: a method
    removed from the shared fake stays removed for every test after this one,
    and that is a failure which surfaces three tests away from its cause.
    """

    class OlderClient:
        def get_sleep_data(self, iso):
            return SLEEP

    got = dict(garmin.read_day(OlderClient(), TODAY))
    assert HealthMetric.hrv not in got
    assert got[HealthMetric.sleep_hours] == 7.0


# --- the sync ---------------------------------------------------------------


def test_sync_walks_the_window_backwards_from_today(settings, repo):
    watch = FakeWatch()
    garmin.sync(settings, repo, TODAY, days=3, client=watch)
    dates = {iso for _, iso in watch.calls}
    assert dates == {
        (TODAY - dt.timedelta(days=n)).isoformat() for n in range(3)
    }


def test_sync_writes_garmin_rows(settings, repo):
    wrote = garmin.sync(settings, repo, TODAY, days=1, client=FakeWatch())
    assert wrote == 8
    entry = repo.health_entry(HealthMetric.sleep_hours, TODAY)
    assert entry.value == 7.0
    assert entry.source is EntrySource.garmin


def test_a_hand_correction_survives_every_later_sync(settings, repo):
    """The rule the whole integration rests on.

    Without it: you notice the watch scored a restless night as five hours, you
    fix it, and tomorrow it is five hours again. After the third time you stop
    correcting anything and the series quietly becomes the watch's opinion.
    """
    repo.log_health(HealthMetric.sleep_hours, TODAY, 5.5, EntrySource.manual)
    garmin.sync(settings, repo, TODAY, days=1, client=FakeWatch())

    entry = repo.health_entry(HealthMetric.sleep_hours, TODAY)
    assert entry.value == 5.5
    assert entry.source is EntrySource.manual
    # …and the metrics you did *not* correct still arrived.
    assert repo.health_entry(HealthMetric.hrv, TODAY).value == 62


def test_re_running_a_sync_writes_nothing_new(settings, repo):
    garmin.sync(settings, repo, TODAY, days=1, client=FakeWatch())
    again = garmin.sync(settings, repo, TODAY, days=1, client=FakeWatch())
    # Every row is already `garmin` with the same value, so the upsert lands
    # but changes nothing -- what matters is that it is not an error and not a
    # duplicate. The count reports rows the sync owns, which is all of them.
    assert again == 8
    assert repo.health_entry(HealthMetric.hrv, TODAY).value == 62


# --- the status the panel reads ---------------------------------------------


def test_status_reports_unconfigured_without_an_email(settings, monkeypatch):
    monkeypatch.delenv("GARMIN_EMAIL", raising=False)
    assert garmin.status(settings).configured is False


def test_status_needs_both_the_switch_and_the_email(settings, monkeypatch):
    monkeypatch.setenv("GARMIN_EMAIL", "someone@example.com")
    on = settings.model_copy(
        update={"garmin": settings.garmin.model_copy(update={"enabled": True})}
    )
    assert garmin.status(on).configured is True
    assert garmin.status(settings).configured is False


def test_the_background_sync_declines_when_the_feature_is_off(settings):
    assert garmin.sync_in_background(settings, None, TODAY) is False


def test_connect_names_the_command_that_fixes_it(settings, tmp_path):
    """Never a bare failure: whichever way `connect` gives up, the message is
    an instruction.

    Two branches, and which one a given venv takes is not the point being
    tested -- the library is an optional dependency, so a machine without it
    fails on the import and a machine with it fails on the absent token store.
    What must hold in both is that the sentence reaching the panel names the
    command that ends the problem, because the alternative is a health tab
    that has silently stopped updating and says so in stack-trace.
    """
    cfg = settings.model_copy(
        update={
            "garmin": settings.garmin.model_copy(update={"token_dir": str(tmp_path / "nope")})
        }
    )
    with pytest.raises(garmin.GarminError) as caught:
        garmin.connect(cfg)
    assert ("pip install" in str(caught.value)) is not garmin.installed()
    assert ("dashboard.garmin login" in str(caught.value)) is garmin.installed()

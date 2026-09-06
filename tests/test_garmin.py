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
# Recorded off the wire, trimmed. `get_rhr_day` is a *range* query -- it asks
# the wellness-statistics URL for fromDate..untilDate with metricId=60 -- so it
# answers with a map of series and never with a flat `restingHeartRate`. This
# fixture used to carry the flat key, which is what let `rhr_metrics` read the
# wrong field first for as long as it did: the test agreed with the code and
# both disagreed with Garmin.
RHR = {
    "userProfileId": 150511205,
    "statisticsStartDate": "2026-09-01",
    "statisticsEndDate": "2026-09-01",
    "allMetrics": {
        "metricsMap": {
            "WELLNESS_RESTING_HEART_RATE": [
                {"value": 48.0, "calendarDate": "2026-09-01"}
            ]
        }
    },
    "groupedMetrics": None,
}
# `averageStressLevel` rides along in the daily summary rather than needing an
# endpoint of its own -- one call fewer against an unofficial API. So does
# `restingHeartRate`, and this is the endpoint that actually carries it. Its
# value here disagrees with RHR's on purpose: the two are read from different
# endpoints on a real morning and the test wants to see which one wins.
STATS = {
    "totalKilocalories": 3120.4,
    "bodyBatteryHighestValue": 91,
    "averageStressLevel": 34,
    "restingHeartRate": 52,
}
HYDRATION = {"valueInML": 2100.0, "sweatLossInML": 620.0}
# The one endpoint that answers with a list: the max-metrics URL takes a date
# range and returns an entry per day, and this module always asks for one day.
MAX_METRICS = [
    {
        "generic": {
            "calendarDate": "2026-07-26",
            "vo2MaxValue": 52.0,
            "vo2MaxPreciseValue": 52.4,
        }
    }
]


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
            "get_max_metrics": MAX_METRICS,
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

    def get_max_metrics(self, iso):
        return self._answer("get_max_metrics", iso)


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


def test_resting_hr_reads_the_metrics_map_this_endpoint_actually_returns():
    """The shape `get_rhr_day` has always sent, read without a fallback firing.

    Pinned because the bug this replaces was invisible from the inside: the
    flat key was tried first, was never present, and the metrics-map read that
    followed produced the right number every time. Nothing was wrong with the
    series -- only with which line of the function was load-bearing, and that
    is not something the panel could ever have shown.
    """
    assert garmin.rhr_metrics(RHR)[HealthMetric.resting_hr] == 48


def test_resting_hr_still_reads_a_flat_key_if_one_ever_appears():
    """The fallback, kept but demoted. Cheap, and these two endpoints have
    merged before."""
    assert garmin.rhr_metrics({"restingHeartRate": 51})[HealthMetric.resting_hr] == 51


def test_resting_hr_yields_nothing_rather_than_a_zero_row():
    assert garmin.rhr_metrics({"restingHeartRate": None}) == {}
    assert garmin.rhr_metrics({}) == {}
    assert garmin.rhr_metrics(
        {"allMetrics": {"metricsMap": {"WELLNESS_RESTING_HEART_RATE": []}}}
    ) == {}
    assert garmin.rhr_metrics(
        {"allMetrics": {"metricsMap": {"WELLNESS_RESTING_HEART_RATE": [{"value": 0}]}}}
    ) == {}


def test_the_daily_summary_also_offers_resting_hr():
    """`stats_metrics` reads the flat key, because this is where it lives."""
    assert garmin.stats_metrics(STATS)[HealthMetric.resting_hr] == 52


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


def test_sweat_loss_comes_off_the_hydration_payload_we_already_fetch():
    """The reading that replaced the glyph as the hydration input. Same call,
    a second field -- the FR165 computes it per activity and the daily summary
    totals it."""
    assert garmin.hydration_metrics(HYDRATION)[HealthMetric.sweat_loss_ml] == 620.0


def test_a_rest_day_sweats_nothing_and_says_nothing():
    """Absent rather than zero, and the distinction is load-bearing now: the
    hydration target is a base plus what was sweated, and a zero would be
    indistinguishable from a training day whose activity had no estimate."""
    assert HealthMetric.sweat_loss_ml not in garmin.hydration_metrics({"valueInML": 1800})


def test_stress_rides_along_in_the_daily_summary():
    """No endpoint of its own. `averageStressLevel` has been in `get_stats` as
    long as calories and Body Battery have, and one call fewer against an
    unofficial API is one fewer thing to break."""
    assert garmin.stats_metrics(STATS)[HealthMetric.stress] == 34


def test_garmins_no_reading_sentinel_is_not_a_stress_level():
    """The watch reports -1 or -2 for a day with too little wear time to
    average. Stored, those would be the calmest days on record."""
    assert HealthMetric.stress not in garmin.stats_metrics({"averageStressLevel": -1})
    assert HealthMetric.stress not in garmin.stats_metrics({"averageStressLevel": -2})


def test_vo2max_reads_a_list_and_prefers_the_precise_value():
    """The only endpoint here that answers with a list, and the only one where
    two fields carry the same number at two precisions. The rounded one would
    show a fortnight of no change followed by a step -- a plateau that is
    really a rounding."""
    assert garmin.vo2max_metrics(MAX_METRICS)[HealthMetric.vo2max] == 52.4


def test_vo2max_falls_back_to_the_whole_number_when_that_is_all_there_is():
    payload = [{"generic": {"vo2MaxValue": 51.0}}]
    assert garmin.vo2max_metrics(payload)[HealthMetric.vo2max] == 51.0


def test_a_day_without_a_run_has_no_vo2max_and_that_is_correct():
    """Garmin only recomputes this after an outdoor run with heart rate, so
    most days produce nothing. A row written anyway would be the watch
    repeating itself, and the series is more honest with gaps in it."""
    assert garmin.vo2max_metrics([]) == {}
    assert garmin.vo2max_metrics([{"generic": None}]) == {}
    assert garmin.vo2max_metrics({"generic": {"vo2MaxPreciseValue": 0}}) == {}


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
        HealthMetric.sweat_loss_ml,
        HealthMetric.stress,
        HealthMetric.vo2max,
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


def test_the_dedicated_endpoint_outranks_the_daily_summary_for_resting_hr():
    """Two endpoints answer for one metric, and the order in `ENDPOINTS` is
    what decides it -- not whichever happened to be yielded last."""
    got = dict(garmin.read_day(FakeWatch(), TODAY))
    assert got[HealthMetric.resting_hr] == 48  # RHR, not STATS's 52


def test_the_summary_supplies_resting_hr_when_the_dedicated_endpoint_dies():
    """What demoting the flat read bought: a 401 on `get_rhr_day` now costs the
    precision, not the metric."""
    got = dict(garmin.read_day(FakeWatch(broken={"get_rhr_day"}), TODAY))
    assert got[HealthMetric.resting_hr] == 52


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
    # Eleven and not twelve: `get_stats` offers a resting heart rate too, and
    # the second offer of a metric is dropped rather than written over.
    assert wrote == 11
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
    assert again == 11
    assert repo.health_entry(HealthMetric.hrv, TODAY).value == 62


# --- the status the panel reads ---------------------------------------------


def test_status_reports_unconfigured_without_an_email(settings, monkeypatch):
    monkeypatch.delenv("GARMIN_EMAIL", raising=False)
    assert garmin.status(settings).configured is False


def switched(settings, enabled: bool):
    """`settings` with `[garmin] enabled` forced either way.

    Every test below about the switch sets the switch, rather than inheriting
    whatever the real `config.toml` happens to say. It said `false` for as long
    as the watch was not on a wrist, and three tests quietly rested on that --
    so turning the feature on for real broke a test about it being off, which
    is a failure that says nothing about the code.
    """
    return settings.model_copy(
        update={"garmin": settings.garmin.model_copy(update={"enabled": enabled})}
    )


def test_status_needs_both_the_switch_and_the_email(settings, monkeypatch):
    monkeypatch.setenv("GARMIN_EMAIL", "someone@example.com")
    assert garmin.status(switched(settings, True)).configured is True
    assert garmin.status(switched(settings, False)).configured is False


def test_the_background_sync_declines_when_the_feature_is_off(settings):
    assert garmin.sync_in_background(switched(settings, False), None, TODAY) is False


def floors(settings, *, idle: int, focus: int):
    """`settings` with both sync floors pinned, rather than whatever
    config.toml happens to say. Same lesson as `switched` above."""
    return settings.model_copy(
        update={
            "garmin": settings.garmin.model_copy(
                update={"min_interval_minutes": idle, "focus_interval_minutes": focus}
            )
        }
    )


def test_the_floor_belongs_to_the_caller_not_to_the_module(settings, monkeypatch):
    """A poll nobody asked for waits an hour; a person does not wait an hour.

    This is the bug the two floors exist for. There was one interval, it was
    an hour, and it was applied to the reload you do *because* the watch's
    numbers are missing from the panel -- so that reload went and looked at
    nothing, and the readings turned up whenever an hour happened to be up.

    Ten minutes since the last run is the interesting gap: past a five-minute
    floor, nowhere near a sixty-minute one, so the two callers must disagree.
    """
    cfg = floors(switched(settings, True), idle=60, focus=5)
    monkeypatch.setattr(
        garmin,
        "_state",
        garmin._State(last_sync=dt.datetime.now() - dt.timedelta(minutes=10)),
    )

    # The unattended beat, taking the module's own floor: not yet.
    assert garmin._claim(cfg, False) is False
    assert garmin._claim(cfg, False, cfg.garmin.min_interval_minutes) is False
    # Someone reloading the page, or turning back to the window.
    assert garmin._claim(cfg, False, cfg.garmin.focus_interval_minutes) is True


def test_a_claim_in_hand_still_excludes_everyone(settings, monkeypatch):
    """The short floor shortens the wait; it does not license two syncs at
    once. `running` is checked before any interval is, so a trip already in
    flight refuses every caller however impatient -- which matters more now
    that a focus event can arrive while the boot payload's sync is still out
    at Garmin."""
    cfg = floors(switched(settings, True), idle=60, focus=5)
    monkeypatch.setattr(garmin, "_state", garmin._State(running=True))
    assert garmin._claim(cfg, False, 1) is False
    assert garmin._claim(cfg, True, 1) is False


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

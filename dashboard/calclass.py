"""Which calendar does this event belong on?

The training set is free and already written: an event's calendar *is* its
label, so `gcalcli agenda --details calendar` over the last few years hands
back a few hundred labelled titles without anyone annotating anything.

What that data is *not* is clean, and the design here follows from having read
it rather than from what the calendars are called:

    Homework  patty bday | do taxes | Career Fair | beerio kart tourney
    Tests     Data Structures Final Exam | Probability Midterm 1 | PUTNAM EXAM
    Classes   ASME Kickoff Meeting | All Souls' Day prayer service | lectures

Barely any of the writable calendars have contents that match their names -- a
since-deleted `Meals` held "Learn Python", "Read" and "Schedule Week". So
keyword rules keyed on the *name* -- "dinner" is a meal, "hw" is homework --
would be confidently wrong on this account, and the ones that survive here are
the ones the history actually supports. Everything else is learned, because the
history is the only thing that knows "bday" files under Homework.

It also means there is a ceiling. Where the same phrasing has genuinely gone to
different calendars on different days, no classifier recovers the intent, and
the honest interface is a visible guess with a one-key override -- which is
what `ResolvedEvent.ranked` is for. Overrides come back as
`calendar_correction` rows and are fitted at a heavier weight than history, so
a wrong guess is worth correcting exactly once.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Iterable, Sequence
from typing import NamedTuple

from . import logs

log = logs.get(__name__)

# A correction is worth this many history rows. High, because there are three
# orders of magnitude fewer of them and they are the only labels given on
# purpose -- but finite, so one stray correction cannot capture a calendar.
CORRECTION_WEIGHT = 12.0

# Below this the model is not saying anything useful, and the answer becomes
# the configured default with `source="fallback"` -- which the panel shows
# differently, because "I guessed" and "I had no idea" are worth telling apart.
MIN_CONFIDENCE = 0.22

# The two rules the history supports. Both are checked before the model and
# both name a calendar that must exist on the account, or they are skipped.
#
# Deliberately not a general keyword table. Every other pattern worth writing
# ("dinner", "hw", "practice") is contradicted by this account's own filing,
# and a rule that fires against the data is worse than no rule: the model can
# be corrected, a hard-coded regex just keeps being wrong.
RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    # "test" is in here and is *not* in the history, which is the point. The
    # account writes these down as "Exam", "Midterm" and "Final" -- so a model
    # fitted on the history alone sends "real analysis test" to Classes, next
    # to the lectures of the same name. The word means the same thing whether
    # or not this calendar has happened to use it before.
    (re.compile(r"\b(tests?|exams?|midterms?|finals?|quiz(?:zes)?)\b", re.I), "Tests"),
    # Kept although `Piano App Deadlines` has since been deleted: every rule is
    # guarded on its calendar existing, so this one simply never fires now, and
    # it costs nothing to have it work again if the calendar comes back.
    (re.compile(r"\bpiano\s+(app|deadline)", re.I), "Piano App Deadlines"),
)


class Sample(NamedTuple):
    text: str
    calendar: str
    weight: float


class Prediction(NamedTuple):
    calendar: str
    source: str
    ranked: list[tuple[str, float]]


def _normalise(text: str) -> str:
    """Fold the things that are spelling rather than meaning.

    The history carries "ai exam1", "ai Exam 2", "fhf exam1" and "comparch
    Exam 2" as four different strings for one habit. Digits go to a single
    token so the trailing number stops being a feature of its own.
    """
    text = text.lower()
    text = re.sub(r"\d+", " # ", text)
    return re.sub(r"[^a-z#\s]+", " ", text).strip()


class CalendarClassifier:
    """Fits on demand, holds one model, and is safe to call from any thread.

    Refitting is cheap -- a few hundred short strings, well under a second --
    so there is no persistence and no background job. The expensive half is
    reading the history out of gcalcli, and that is cached upstream in
    `Feeds`.
    """

    def __init__(self, default_calendar: str, *, min_confidence: float = MIN_CONFIDENCE) -> None:
        self.default_calendar = default_calendar
        self.min_confidence = min_confidence
        self._lock = threading.Lock()
        self._pipeline = None
        self._classes: list[str] = []
        self._targets: list[str] = []
        self.unavailable: str | None = None
        """Why there is no model, if there is not. Shown in the panel."""
        self.sample_count = 0
        """How many labelled titles the current model was fitted on.

        Reported back by the retrain button, because "it retrained" and "it
        retrained and found four more events" are different answers."""

    # -- fitting ------------------------------------------------------------

    def fit(self, samples: Sequence[Sample], writable: Iterable[str]) -> None:
        self._targets = list(writable)
        usable = [s for s in samples if s.text.strip() and s.calendar in self._targets]

        # One class is not a classification problem, and sklearn refuses to fit
        # it. Rules and the default still work, so this is a state to be in
        # rather than an error to raise.
        if len({s.calendar for s in usable}) < 2:
            with self._lock:
                self._pipeline, self._classes = None, []
            self.sample_count = len(usable)
            self.unavailable = "not enough labelled history to fit a model"
            log.info("calendar classifier: %s", self.unavailable)
            return

        try:
            pipeline = _build_pipeline()
        except ImportError as e:
            with self._lock:
                self._pipeline, self._classes = None, []
            self.unavailable = f"scikit-learn is not installed ({e}); using rules only"
            log.warning("calendar classifier: %s", self.unavailable)
            return

        pipeline.fit(
            [_normalise(s.text) for s in usable],
            [s.calendar for s in usable],
            **{"clf__sample_weight": [s.weight for s in usable]},
        )
        with self._lock:
            self._pipeline = pipeline
            self._classes = list(pipeline.named_steps["clf"].classes_)
        self.sample_count = len(usable)
        self.unavailable = None
        log.info(
            "calendar classifier fitted on %d titles across %d calendars",
            len(usable),
            len(self._classes),
        )

    @property
    def fitted(self) -> bool:
        return self._pipeline is not None

    @property
    def targets(self) -> list[str]:
        """The writable calendars this model was fitted against.

        Read by the caller to notice that the account's calendars have changed
        since the fit, which is the trigger to refit. A copy, so nobody can
        mutate the model's idea of the world by holding the list.
        """
        return list(self._targets)

    # -- predicting ---------------------------------------------------------

    def predict(self, title: str) -> Prediction:
        """Never raises, and always names a calendar."""
        ranked = self._scores(title)

        for pattern, calendar in RULES:
            if pattern.search(title) and calendar in self._targets:
                # The rule wins the label but the model still supplies the
                # order behind it, so Tab cycles to the next *plausible*
                # calendar rather than to an alphabetical list.
                rest = [(c, s) for c, s in ranked if c != calendar]
                return Prediction(calendar, "rule", [(calendar, 1.0), *rest])

        if ranked and ranked[0][1] >= self.min_confidence:
            return Prediction(ranked[0][0], "model", ranked)

        fallback = self.default_calendar if self.default_calendar in self._targets else (
            self._targets[0] if self._targets else self.default_calendar
        )
        rest = [(c, s) for c, s in ranked if c != fallback]
        return Prediction(fallback, "fallback", [(fallback, 0.0), *rest])

    def _scores(self, title: str) -> list[tuple[str, float]]:
        with self._lock:
            pipeline, classes = self._pipeline, self._classes
        text = _normalise(title)
        if pipeline is None or not text:
            return [(c, 0.0) for c in self._targets]
        try:
            probabilities = pipeline.predict_proba([text])[0]
        except Exception:
            log.exception("calendar classifier failed on %r", title[:80])
            return [(c, 0.0) for c in self._targets]
        scored = sorted(
            (
                pair
                for pair in zip(classes, (float(p) for p in probabilities), strict=True)
                # A class that is no longer a writable calendar is dropped
                # rather than ranked. The model outlives the account it was
                # fitted on by however long it takes to notice a deletion, and
                # in that window predicting a calendar that no longer exists
                # sends `--calendar` to gcalcli for something it cannot match --
                # which surfaces as a failed add rather than as a wrong guess.
                if pair[0] in self._targets
            ),
            key=lambda pair: pair[1],
            reverse=True,
        )
        # ...and writable calendars the model never saw still belong in the
        # cycle: a calendar created this morning has no history by definition,
        # and leaving it out would make it unreachable from the box.
        seen = {c for c, _ in scored}
        return scored + [(c, 0.0) for c in self._targets if c not in seen]


def _build_pipeline():
    """Word and character n-grams into one logistic regression.

    Imported here rather than at module scope, following `webauthn.py`: a venv
    built before scikit-learn joined the dependency list still boots, and the
    panel says what is missing instead of the whole app failing to import.

    Characters matter as much as words on this data. Titles are short, half of
    them are abbreviations the account invented ("fhf", "comparch", "or hw6",
    "prob hw9"), and a word-level model treats "hw6" and "hw9" as two unrelated
    tokens while `char_wb` sees the shared stem.
    """
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import FeatureUnion, Pipeline

    return Pipeline(
        [
            (
                "features",
                FeatureUnion(
                    [
                        ("word", TfidfVectorizer(
                            analyzer="word",
                            ngram_range=(1, 2),
                            sublinear_tf=True,
                            min_df=1,
                        )),
                        ("char", TfidfVectorizer(
                            analyzer="char_wb",
                            ngram_range=(3, 5),
                            sublinear_tf=True,
                            min_df=1,
                        )),
                    ]
                ),
            ),
            (
                "clf",
                # `balanced` because the classes are wildly uneven -- Classes
                # has 192 distinct titles and Meals has 9 -- and without it the
                # model answers "Classes" to very nearly everything.
                LogisticRegression(
                    max_iter=2000,
                    class_weight="balanced",
                    C=4.0,
                ),
            ),
        ]
    )


def samples_from_events(events: Iterable, writable: Iterable[str]) -> list[Sample]:
    """History -> training rows, deduplicated by (title, calendar).

    Deduplication is the difference between 1455 rows and 383. A class that
    meets three times a week for a semester is one habit written down 40
    times, and letting it vote 40 times drowns out every calendar that holds
    one-off events.
    """
    allowed = set(writable)
    seen: set[tuple[str, str]] = set()
    samples: list[Sample] = []
    for event in events:
        title = (getattr(event, "title", "") or "").strip()
        calendar = getattr(event, "calendar", "") or ""
        key = (title.lower(), calendar)
        if not title or calendar not in allowed or key in seen:
            continue
        seen.add(key)
        samples.append(Sample(title, calendar, 1.0))
    return samples

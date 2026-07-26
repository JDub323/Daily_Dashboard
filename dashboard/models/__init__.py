"""Typed contracts for the dashboard: persisted models and API bodies.

`domain` defines the SQLite tables. `api` defines what crosses HTTP. They are
kept separate on purpose -- state.json's storage format *was* the API payload,
which is what allowed a stale browser tab to full-replace the whole file.
"""

from . import api, common, domain
from .common import (
    Base,
    ChessOutcome,
    ChessStatus,
    Game,
    HealthMetric,
    MathOutcome,
    MathStatus,
    PaperStatus,
    Provenance,
    Rating,
    ReviewQuality,
    Score,
    Track,
    Url,
)
from .domain import (
    ChessAttempt,
    ChessPuzzle,
    DayCompletion,
    DayLog,
    HealthEntry,
    Link,
    MathAttempt,
    MathProblem,
    Paper,
    PokedokuResult,
    RatingEvent,
    SentenceGrade,
    TheoremCard,
    TheoremReview,
    TopicInfo,
    WordGrade,
    WordleResult,
    WordOfDay,
)

__all__ = [
    # scalars + enums
    "Base",
    # tables
    "ChessAttempt",
    "ChessOutcome",
    "ChessPuzzle",
    "ChessStatus",
    "DayCompletion",
    "DayLog",
    "Game",
    "HealthEntry",
    "HealthMetric",
    "Link",
    "MathAttempt",
    "MathOutcome",
    "MathProblem",
    "MathStatus",
    "Paper",
    "PaperStatus",
    "PokedokuResult",
    "Provenance",
    "Rating",
    "RatingEvent",
    "ReviewQuality",
    "Score",
    "SentenceGrade",
    "TheoremCard",
    "TheoremReview",
    "TopicInfo",
    "Track",
    "Url",
    "WordGrade",
    "WordOfDay",
    "WordleResult",
    "api",
    "common",
    "domain",
]

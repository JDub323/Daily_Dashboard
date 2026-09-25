// The feel log's questions, and what a day pays for answering them.
//
// The failure these guard against is the one every log here guards against: a
// body the server refuses after the answers were given. `FeelRequest` refuses
// a kind of tired beside an energy of 3, so the phone must take the kinds away
// the moment the scale moves — and that is `prunedDraft`'s job, not the
// screen's, which is why it is testable without one.

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { dayTasks, optional, score } from "../../web/app/day.js";
import {
  applicableSteps,
  FEEL_STEPS,
  feelLabel,
  feelWords,
  missingSteps,
  prunedDraft,
} from "../../web/app/logs.js";

const names = (steps) => steps.map((s) => s.name);

describe("the feel log's steps", () => {
  it("asks what kind of tired only when energy is 2 or under", () => {
    assert.deepEqual(names(applicableSteps(FEEL_STEPS, { energy: 3, mood: 3 })),
      ["energy", "mood", "symptoms"]);
    assert.deepEqual(names(applicableSteps(FEEL_STEPS, { energy: 2, mood: 1 })),
      ["energy", "mood", "tired", "low", "symptoms"]);
  });

  it("asks nothing extra before a scale is answered", () => {
    assert.deepEqual(names(applicableSteps(FEEL_STEPS, {})), ["energy", "mood", "symptoms"]);
  });

  it("drops the kinds when the scale moves back up", () => {
    // The server refuses this body, so the draft must never be able to carry it.
    const draft = { energy: 4, mood: 3, tired: ["malaise"] };
    assert.equal(prunedDraft(FEEL_STEPS, draft).tired, undefined);
  });

  it("keeps the note while filtering the graded symptoms", () => {
    const draft = { energy: 3, mood: 3, symptoms: { graded: { cough: 2, gone: 1 }, note: "odd" } };
    assert.deepEqual(prunedDraft(FEEL_STEPS, draft).symptoms, { graded: { cough: 2 }, note: "odd" });
  });

  it("holds the button for the two scales and nothing else", () => {
    assert.deepEqual(names(missingSteps(FEEL_STEPS, {})), ["energy", "mood"]);
    // Nothing off is the ordinary answer, and the kinds are a multi-pick.
    assert.deepEqual(missingSteps(FEEL_STEPS, { energy: 1, mood: 1 }), []);
  });
});

describe("a feel, in words", () => {
  it("names the kinds, then the symptoms with any grade above mild", () => {
    const words = feelWords({
      tired: ["malaise", "drained"], low: ["on_edge"],
      symptoms: { headache: 1, sore_throat: 3 }, note: "metal taste",
    });
    assert.equal(words, "malaise, drained, on edge · headache, sore throat (severe), “metal taste”");
  });

  it("says nothing for a fine row", () => {
    assert.equal(feelWords({ tired: [], low: [], symptoms: {} }), "");
  });

  it("knows the lookback's own two words", () => {
    assert.equal(feelLabel("tired"), "tired");
    assert.equal(feelLabel("sore_throat"), "sore throat");
  });
});

/** Just enough of a `DashboardState` for `dayTasks` to walk. */
function state(pings) {
  return {
    date: "2026-09-23",
    math: { mode: "compute", problem: null, status: "open" },
    tricks: { status: "open", enabled: false },
    theorems: { recalled: null },
    chess: { status: "open" },
    word: null,
    spelling: { status: "open", enabled: false },
    investigate: { today: null, day: null },
    patterns: { recalled: null, deck_size: 0 },
    papers: { surfaced: null },
    games: {},
    daylog: null,
    piano_practiced: false,
    health: {
      series: [], readiness_today: null, meals_today: [], sessions_today: [], plan: null,
      feel: { pings },
    },
  };
}

describe("the pings on the day's list", () => {
  it("is a point a window, earned by answering it", () => {
    const tasks = dayTasks(state([
      { name: "midday", state: "answered" },
      { name: "evening", state: "missed" },
    ]));
    const feel = optional(tasks).filter((t) => t.id.startsWith("feel:"));
    assert.deepEqual(feel.map((t) => [t.label, t.done, t.points]),
      [["feel · midday", true, 1], ["feel · evening", false, 1]]);
    assert.equal(score(feel).earned, 1);
  });

  it("offers nothing when pings are off", () => {
    assert.equal(dayTasks(state([])).filter((t) => t.id.startsWith("feel:")).length, 0);
  });
});

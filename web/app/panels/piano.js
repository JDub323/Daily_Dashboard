// Scale and chord of the day.
//
// The 30 rows of scale/chord content used to sit in the middle of the logic
// file. They are content, so they live in web/data/piano.json now and this
// module only draws them.

import * as api from "../api.js";
import { today } from "../api.js";
import { el, link, mount, svg } from "../dom.js";
import { put, store } from "../store.js";
// The voice lives in its own module: it is fifty lines of panel and two
// hundred of acoustics, and the two have nothing to say to each other past
// "play these semitones".
import { play } from "../piano.js";

let data = null;

async function load() {
  data ??= await fetch("/data/piano.json").then((r) => r.json());
  return data;
}

const KEY_W = 20;
const KEY_H = 58;
const BLACK_W = 12;
const BLACK_H = 36;
const WHITE_KEYS = 14; // two octaves
const WHITE_SEMITONES = [0, 2, 4, 5, 7, 9, 11];

function keyboard(rootIndex, intervals, notes) {
  const lit = new Set(intervals.map((iv) => (rootIndex + iv) % 12));
  const whites = [];
  const blacks = [];
  const labels = [];

  for (let i = 0; i < WHITE_KEYS; i++) {
    const semitone = WHITE_SEMITONES[i % 7];
    const cls = lit.has(semitone) ? (semitone === rootIndex ? "pk-root" : "pk-hl") : "pk-white";
    whites.push(svg("rect", {
      class: `pk-white ${cls}`, x: i * KEY_W, y: 0, width: KEY_W, height: KEY_H, rx: 2,
    }));
    if (lit.has(semitone)) {
      labels.push(svg("text.pk-label", {
        x: i * KEY_W + KEY_W / 2, y: KEY_H - 8, "text-anchor": "middle",
      }, notes[semitone]));
    }
    if (semitone !== 4 && semitone !== 11) { // a black key follows
      const black = (semitone + 1) % 12;
      const bcls = lit.has(black) ? (black === rootIndex ? "pk-root" : "pk-hl") : "pk-black";
      blacks.push(svg("rect", {
        class: `pk-black ${bcls}`,
        x: (i + 1) * KEY_W - BLACK_W / 2, y: 0, width: BLACK_W, height: BLACK_H, rx: 2,
      }));
    }
  }
  return svg("svg", { viewBox: `0 0 ${WHITE_KEYS * KEY_W} ${KEY_H}` }, whites, blacks, labels);
}

/**
 * `♪ Autumn Leaves — Cannonball Adderley ↗`, deep-linked to the moment.
 *
 * The prose note has always named a song; this makes it a thing you can
 * actually go and hear, which is the difference between being told a chord
 * sounds like film noir and finding out that it does.
 *
 * Entries with no song are the handful whose note names a genre rather than a
 * recording ("theory candy, almost never home"). They get no link rather than
 * a made-up one.
 */
function songLink(song) {
  if (!song) return null;
  return el("div.piano-song", null, link(song.url, `♪ ${song.title} ↗`));
}

function item(title, subtitle, rootIndex, intervals, entry, notes, kind) {
  const spelled = intervals.map((iv) => notes[(rootIndex + iv) % 12]).join(" – ");
  return el("div.piano-item", null,
    el("div.piano-head", null,
      el("span.piano-name", { text: title }),
      el("span.faint", { text: subtitle }),
    ),
    keyboard(rootIndex, intervals, notes),
    el("div.row.piano-controls", null,
      el("button.piano-play", {
        text: `▶ play ${kind}`,
        onclick: () => play(rootIndex, intervals, kind),
      }),
      el("span.piano-notes", { text: spelled }),
    ),
    el("div.faint", { text: entry.note }),
    songLink(entry.song),
  );
}

export default {
  id: "piano",
  title: "♪ piano of the day",
  tab: "dailies",
  slot: "grid",
  // Half a row. The keyboard diagram caps at 280px anyway, so the four columns
  // it used to hold were mostly whitespace to the right of the picture.
  span: 3,

  /**
   * The tick, and the only thing this panel writes.
   *
   * It is a self-report and cannot be anything else — the app has no way of
   * hearing you play — so it asks the smallest question it can answer
   * honestly: did you sit down. No minutes, no self-graded quality, nothing
   * that turns into a rating. Pressing it again takes it back, because the
   * failure mode of a one-tap claim is tapping it on the way past.
   */
  practiceRow() {
    const done = store.state.piano_practiced;
    return el("div.row.spread.piano-tick", null,
      el(done ? "span.ok" : "span.faint", { text: done ? "practiced ✓" : "not practiced yet" }),
      el(`button${done ? "" : ".primary"}`, {
        text: done ? "undo" : "practiced it",
        onclick: async (e) => {
          e.target.disabled = true;
          const practiced = !store.state.piano_practiced;
          await api.post("/api/piano", { practiced });
          put("piano_practiced", practiced);
        },
      }),
    );
  },

  async render(node) {
    const { notes, scales, chords } = await load();
    // Noon avoids the DST edge where a midnight timestamp lands on the
    // previous day; the browser's local date is the input either way.
    const day = Math.floor(new Date(`${today()}T12:00:00`).getTime() / 864e5);

    const scale = scales[day % scales.length];
    const chord = chords[day % chords.length];
    const scaleRoot = (day * 7) % 12; // ×7 cycles all twelve keys
    const chordRoot = (day * 5 + 3) % 12;

    mount(node,
      item(`${notes[scaleRoot]} ${scale.name}`, "scale", scaleRoot, scale.intervals, scale, notes, "scale"),
      item(`${notes[chordRoot]}${chord.symbol}`, chord.name, chordRoot, chord.intervals, chord, notes, "chord"),
      // Under both, not above them: the two keyboards are what you came for,
      // and the tick is what you press on the way out.
      this.practiceRow(),
    );
  },
};

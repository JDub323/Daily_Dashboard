// Scale and chord of the day.
//
// The 30 rows of scale/chord content used to sit in the middle of the logic
// file. They are content, so they live in web/data/piano.json now and this
// module only draws them.

import { today } from "../api.js";
import { el, svg } from "../dom.js";

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

function item(title, subtitle, rootIndex, intervals, note, notes) {
  const spelled = intervals.map((iv) => notes[(rootIndex + iv) % 12]).join(" – ");
  return el("div.piano-item", null,
    el("div.piano-head", null,
      el("span.piano-name", { text: title }),
      el("span.faint", { text: subtitle }),
    ),
    keyboard(rootIndex, intervals, notes),
    el("div.piano-notes", { text: spelled }),
    el("div.faint", { text: note }),
  );
}

export default {
  id: "piano",
  title: "♪ piano of the day",
  col: "right",

  async render(node) {
    const { notes, scales, chords } = await load();
    // Noon avoids the DST edge where a midnight timestamp lands on the
    // previous day; the browser's local date is the input either way.
    const day = Math.floor(new Date(`${today()}T12:00:00`).getTime() / 864e5);

    const scale = scales[day % scales.length];
    const chord = chords[day % chords.length];
    const scaleRoot = (day * 7) % 12; // ×7 cycles all twelve keys
    const chordRoot = (day * 5 + 3) % 12;

    node.replaceChildren(
      item(`${notes[scaleRoot]} ${scale.name}`, "scale", scaleRoot, scale.intervals, scale.note, notes),
      item(`${notes[chordRoot]}${chord.symbol}`, chord.name, chordRoot, chord.intervals, chord.note, notes),
    );
  },
};

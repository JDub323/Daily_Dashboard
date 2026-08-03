// Scale and chord of the day.
//
// The 30 rows of scale/chord content used to sit in the middle of the logic
// file. They are content, so they live in web/data/piano.json now and this
// module only draws them.

import { today } from "../api.js";
import { el, link, svg } from "../dom.js";

let data = null;

async function load() {
  data ??= await fetch("/data/piano.json").then((r) => r.json());
  return data;
}

// --- sound -----------------------------------------------------------------
//
// Synthesised, not sampled. A set of note samples is a few hundred KB of audio
// files to serve, cache and keep in the repo, for a panel whose entire job is
// to tell you which seven keys are lit — and an oscillator is exact, which
// samples pitched by playback rate are not.
//
// The context is created on the first click rather than at module load: an
// AudioContext constructed without a user gesture starts suspended in every
// browser, and one built at import time would be permanently silent.

const MIDDLE_C = 261.63;
let ctx = null;

function audio() {
  ctx ??= new (window.AudioContext ?? window.webkitAudioContext)();
  // Resumed every time, not once: the context is suspended again whenever the
  // tab goes to the background, and a panel that plays until you switch away
  // and then never again is the harder bug to notice.
  if (ctx.state === "suspended") ctx.resume();
  return ctx;
}

/** Semitones above middle C -> Hz. Equal temperament, so intervals above an
 *  octave (add9's 14, the 13 chord's 21) need no special case. */
const hz = (semitones) => MIDDLE_C * 2 ** (semitones / 12);

/**
 * One note, with an envelope.
 *
 * Triangle rather than sine: a sine is so pure that a stack of four is hard to
 * hear as separate notes, which defeats the point of playing a chord. The
 * 10ms attack is there to stop the click a hard gain step makes, and the decay
 * is exponential because a linear one sounds like a note being switched off.
 */
function tone(at, semitones, duration, peak) {
  const c = audio();
  const osc = c.createOscillator();
  const env = c.createGain();
  osc.type = "triangle";
  osc.frequency.value = hz(semitones);
  env.gain.setValueAtTime(0.0001, at);
  env.gain.linearRampToValueAtTime(peak, at + 0.01);
  env.gain.exponentialRampToValueAtTime(0.0001, at + duration);
  osc.connect(env).connect(c.destination);
  osc.start(at);
  osc.stop(at + duration + 0.02);
}

const STEP = 0.17;   // seconds between notes of a scale
const ROLL = 0.035;  // between notes of a chord — a roll, not an arpeggio

/**
 * Play a scale up and back down, or a chord as a rolled block.
 *
 * The scale gets its octave on top and then descends, because the octave is
 * what makes it land as a scale rather than as seven notes that stop. The
 * chord is rolled rather than struck flat for the same reason a pianist rolls
 * one: it is how you hear which notes are in it.
 *
 * Peak gain is divided across the voices so a six-note chord is not twice as
 * loud as a triad — summed oscillators clip, and clipping is what "the piano
 * button sounds bad" turns out to mean.
 */
function play(rootIndex, intervals, kind) {
  const c = audio();
  const start = c.currentTime + 0.05;

  if (kind === "chord") {
    const peak = Math.min(0.22, 0.5 / intervals.length);
    intervals.forEach((iv, i) => tone(start + i * ROLL, rootIndex + iv, 1.6, peak));
    return;
  }

  const up = [...intervals, 12];
  const notes = [...up, ...up.slice(0, -1).reverse()];
  notes.forEach((iv, i) => tone(start + i * STEP, rootIndex + iv, STEP * 1.8, 0.2));
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
  tab: "hobbies",
  slot: "grid",

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
      item(`${notes[scaleRoot]} ${scale.name}`, "scale", scaleRoot, scale.intervals, scale, notes, "scale"),
      item(`${notes[chordRoot]}${chord.symbol}`, chord.name, chordRoot, chord.intervals, chord, notes, "chord"),
    );
  },
};

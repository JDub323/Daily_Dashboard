// A piano, from recordings of one.
//
// This started as a triangle oscillator, then became an additive synth with
// inharmonic partials, a hammer transient and two detuned strings per note.
// That was a real improvement and it was still obviously not a piano, because
// the thing it was modelling has a soundboard, and a soundboard is not a
// handful of sine waves — it is a resonant body whose response is different at
// every frequency and which is still ringing from the last note when the next
// one lands. You do not get there by adding partials. You get there by
// recording it.
//
// So: `web/vendor/piano`, one mp3 per semitone from middle C to Ab6, off the
// FluidR3 GM acoustic grand. 800 KB, served from the same origin as everything
// else, cached immutable like the vendored KaTeX.
//
// --- one file per semitone, which is not how this is usually done -----------
//
// Sampled sets are normally thinned: keep one recording every third or fourth
// key and resample for the gaps, which is what `playbackRate` does. It is the
// obvious economy and it is the one thing that makes a sampled piano sound
// worse than a synthesised one, because resampling moves the *whole* spectrum
// — the formants of the body along with the pitch of the string. A note shifted
// up three semitones is not a piano playing higher, it is a smaller piano, and
// the seam between a sampled note and its resampled neighbour is most audible
// inside a chord, which is the only thing this panel plays.
//
// At 25 KB a note the entire range costs less than one photograph. There is
// nothing to resample and so nothing to hear.
//
// --- what is left to do in code ---------------------------------------------
//
// Three things, and only three:
//
//   **An envelope.** A sample is a whole note, decay and all, and a scale of
//   fourteen of them left to ring is mud. Each voice gets a release ramp so it
//   is out of the way by the time the next two have landed.
//
//   **Velocity, as brightness and not only level.** One velocity layer is all
//   the set has, so a quiet note is a loud one turned down — which sounds like
//   a volume knob rather than a softer touch. A lowpass that opens with
//   velocity is the cheap approximation of the thing a real hammer does, and
//   it is most of the difference.
//
//   **A bus with a limiter on it.** Six samples struck inside 35ms sum well
//   past full scale, and the loudest moment of the loudest chord is exactly
//   the wrong place to distort.

const MIDDLE_C = 261.63;

/** Semitones above middle C -> Hz. Only used for the velocity filter's corner
 *  now that nothing is being synthesised, but that use is real: how far the
 *  lowpass opens has to be relative to the note's own pitch. */
export const hz = (semitones) => MIDDLE_C * 2 ** (semitones / 12);

/**
 * Semitone above middle C -> filename in `web/vendor/piano`.
 *
 * Flats rather than sharps because that is how the source set spells them, and
 * the spelling is the whole contract with the directory: this array is the only
 * place in the app that knows what those files are called.
 */
const SAMPLES = [
  "C4", "Db4", "D4", "Eb4", "E4", "F4", "Gb4", "G4", "Ab4", "A4", "Bb4", "B4",
  "C5", "Db5", "D5", "Eb5", "E5", "F5", "Gb5", "G5", "Ab5", "A5", "Bb5", "B5",
  "C6", "Db6", "D6", "Eb6", "E6", "F6", "Gb6", "G6", "Ab6",
];

const url = (semitone) => `/vendor/piano/${SAMPLES[semitone]}.mp3`;

/** Into the range the set covers. Nothing the panel plays falls outside it —
  * the widest is a thirteenth on the highest root, which lands on 32 — but a
  * new chord shape in piano.json should be a note at the edge of the keyboard
  * rather than an exception. */
const clamp = (semitone) =>
  Math.max(0, Math.min(SAMPLES.length - 1, Math.round(semitone)));

// --- the graph --------------------------------------------------------------

let ctx = null;
let bus = null;

/**
 * The AudioContext, created on the first play rather than at module load.
 *
 * A context constructed without a user gesture starts suspended in every
 * browser, and one built at import time would be permanently silent. It is
 * also resumed on *every* play rather than once: the context is suspended
 * again whenever the tab goes to the background, and a panel that plays until
 * you switch away and then never again is the harder bug to notice.
 */
export function audio() {
  ctx ??= new (window.AudioContext ?? window.webkitAudioContext)();
  if (ctx.state === "suspended") ctx.resume();
  bus ??= masterBus(ctx);
  return ctx;
}

/**
 * Everything every note goes through, built once.
 *
 * The compressor is doing real work and is not a mastering affectation: a
 * thirteenth is six samples struck inside 35ms and their sum is well past full
 * scale. Without it the loudest moment of the loudest chord is the one that
 * distorts, which is exactly backwards.
 *
 * No reverb. The additive version needed a room because a stack of sine waves
 * has none; these recordings were made in one.
 */
function masterBus(c) {
  const comp = c.createDynamicsCompressor();
  comp.threshold.value = -16;
  comp.knee.value = 22;
  comp.ratio.value = 3.5;
  comp.attack.value = 0.005;
  comp.release.value = 0.2;

  const out = c.createGain();
  out.gain.value = 0.9;

  comp.connect(out).connect(c.destination);
  return comp;
}

// --- the samples ------------------------------------------------------------

/** semitone -> AudioBuffer, once decoded. */
const buffers = new Map();
/** semitone -> the in-flight decode, so a chord does not fetch C5 six times. */
const pending = new Map();

async function buffer(semitone) {
  if (buffers.has(semitone)) return buffers.get(semitone);
  if (pending.has(semitone)) return pending.get(semitone);

  const job = (async () => {
    const c = audio();
    const bytes = await fetch(url(semitone)).then((r) => {
      if (!r.ok) throw new Error(`${r.status}`);
      return r.arrayBuffer();
    });
    // The callback form, not the promise form: Safari has never implemented
    // the promise overload of decodeAudioData, and this file is the one place
    // it would matter.
    const decoded = await new Promise((resolve, reject) =>
      c.decodeAudioData(bytes, resolve, reject),
    );
    buffers.set(semitone, decoded);
    pending.delete(semitone);
    return decoded;
  })();

  pending.set(semitone, job);
  return job;
}

/**
 * Pull the whole range into memory.
 *
 * Called on the first play and never awaited by it — the note you asked for is
 * fetched on its own path and does not queue behind the other thirty-two. This
 * is only so the *second* thing you press is instant, which on this panel is
 * always the case: nobody plays the scale and then leaves.
 */
let warmed = false;
function warm() {
  if (warmed) return;
  warmed = true;
  for (let i = 0; i < SAMPLES.length; i++) buffer(i).catch(() => {});
}

// --- one note ---------------------------------------------------------------

/**
 * How far above the note's own fundamental the lowpass sits at full velocity.
 *
 * Relative to pitch rather than absolute, because "bright" means a different
 * number of Hz at the top of the keyboard than in the middle. At the bottom of
 * the velocity range this closes to about a third of the way up, which is
 * audibly softer without sounding like a blanket over the strings.
 */
const OPEN = 26;

/**
 * Play one sample.
 *
 * `velocity` is 0–1 and does two things, both of which a real piano does: it
 * scales the level and it opens the note's lowpass. A piano played harder is
 * not the same sound turned up — it is a brighter sound, because a faster
 * hammer excites more of the upper partials — and level alone is the giveaway.
 *
 * `sustain` is how long the note is allowed to ring before it is faded out.
 * The recordings run for several seconds each; a scale of fourteen left to
 * their own devices is fourteen notes sounding at once by the end of it.
 */
export async function note(semitones, { at = 0, velocity = 0.7, sustain = 2.4 } = {}) {
  const c = audio();
  warm();
  const index = clamp(semitones);

  let sample;
  try {
    sample = await buffer(index);
  } catch {
    // A missing file is silence for that note rather than a broken panel. The
    // set is vendored and served from this origin, so this is close to
    // unreachable — but "the piano button does nothing" is a better failure
    // than "the piano button throws".
    return;
  }

  // Recomputed after the await rather than captured before it: the first note
  // of a session waits on a fetch and a decode, and a start time worked out
  // before that would already be in the past by the time it was used, which
  // the Web Audio clock reads as "now" and plays the whole phrase at once.
  const when = Math.max(at, c.currentTime + 0.02);

  const src = c.createBufferSource();
  src.buffer = sample;

  const colour = c.createBiquadFilter();
  colour.type = "lowpass";
  colour.frequency.value = Math.min(16000, hz(index) * (OPEN * (0.35 + velocity * 0.65)));
  colour.Q.value = 0.2;

  const env = c.createGain();
  env.gain.setValueAtTime(velocity * 0.9, when);
  // Held flat and then faded, rather than decayed from the start: the sample
  // already has the piano's own decay in it, and a second one on top of it is
  // a note that dies twice.
  env.gain.setValueAtTime(velocity * 0.9, when + sustain * 0.7);
  env.gain.exponentialRampToValueAtTime(0.0001, when + sustain);

  src.connect(colour).connect(env).connect(bus);
  src.start(when);
  src.stop(when + sustain + 0.05);
}

// --- phrases ----------------------------------------------------------------

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
 * Velocity is shaped rather than flat in both. A scale played at one velocity
 * is a scale played by a machine, and the small lift towards the top is what a
 * hand does without being asked. In the chord the root is struck hardest and
 * the extensions lightest, which is voicing rather than dynamics — it is what
 * keeps a ♭13 an extension instead of an argument with the fifth.
 *
 * Every note is scheduled against one `start`, taken before any of them are
 * awaited, so the phrase keeps its rhythm across the first play's fetch.
 */
export async function play(rootIndex, intervals, kind) {
  const c = audio();
  warm();

  const voices = kind === "chord"
    ? intervals.map((iv, i) => ({
        semitone: rootIndex + iv,
        at: i * ROLL,
        velocity: i === 0 ? 0.85 : Math.max(0.5, 0.76 - i * 0.05),
        // Long enough to still be ringing under the last note of the roll,
        // which is the whole reason to roll it.
        sustain: 3.2,
      }))
    : (() => {
        const up = [...intervals, 12];
        const notes = [...up, ...up.slice(0, -1).reverse()];
        const top = (notes.length - 1) / 2;
        return notes.map((iv, i) => ({
          semitone: rootIndex + iv,
          at: i * STEP,
          // A shallow arc peaking at the octave: up into it, back off on the
          // way down. ±0.09 is under a decibel and a half — audible as
          // phrasing rather than as an effect.
          velocity: 0.68 + 0.09 * (1 - Math.abs(i - top) / top),
          sustain: STEP * 4.5,
        }));
      })();

  // Every buffer before any of them is scheduled, and this is the whole reason
  // `play` is async.
  //
  // Scheduling first and letting each note wait on its own decode looks
  // equivalent and is not: the Web Audio clock has already passed a start time
  // worked out before a 200ms fetch, and a start time in the past means *now*.
  // On the first press the roll therefore arrived as a chord — the first note
  // on time and the rest piled on top of it the moment their files landed. It
  // came right on the second press, which is the worst way for a bug like this
  // to behave.
  await Promise.all(voices.map((v) => buffer(clamp(v.semitone)).catch(() => null)));

  // Taken after the await, so it is a lead on the schedule rather than a
  // budget for the download.
  const start = c.currentTime + 0.06;
  for (const v of voices) {
    note(v.semitone, { at: start + v.at, velocity: v.velocity, sustain: v.sustain });
  }
}

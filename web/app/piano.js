// A piano voice, built rather than fetched.
//
// The panel used to play one triangle oscillator per note into the
// destination. That is a tone at the right pitch and it is not a piano, and
// the gap between the two is not subtlety — a triangle wave has no attack, its
// partials sit at exact multiples of the fundamental, and every one of them
// decays at the same rate. Those three facts are, between them, most of what
// the ear uses to decide it is hearing a string that was struck.
//
// --- why this is not a dependency ------------------------------------------
//
// The obvious answer is a sampled library: Tone.js over the Salamander set, or
// a soundfont player. Both are ruled out by the same invariant the vendored
// KaTeX exists to keep — this app has no external runtime dependency and works
// with the network unplugged — and vendoring the samples is not the way round
// it. The Salamander set is tens of megabytes; the trimmed sets that are not
// are trimmed by pitching a handful of recordings up and down by playback
// rate, which drags the formants along with the pitch and sounds worse than
// synthesis rather than better.
//
// So the improvement is in the voice, and the voice is additive, with the four
// things a struck string actually does:
//
//   **Inharmonicity.** A real string is stiff, so its nth partial does not sit
//   at n·f0 but at n·f0·sqrt(1 + B·n²). B is small (~2e-4 in the middle of the
//   keyboard) and rises towards the bass. This is the single most piano-ish
//   thing in here: it is why the upper partials of a low C sound sharp against
//   its own octave, and why a perfectly harmonic stack sounds like an organ.
//
//   **Partials that decay at different rates.** High partials die first, which
//   is why a piano note is bright at the attack and mellow a second later. One
//   shared envelope cannot do that, so every partial gets its own.
//
//   **A hammer.** About six milliseconds of filtered noise at the onset. It
//   carries no pitch and removing it changes not one frequency in the
//   spectrum — and without it the note begins rather than being struck.
//
//   **Two strings, slightly apart.** Every note above the bass has two or
//   three strings tuned within a cent or so of each other. The beating between
//   them is the slow shimmer in a held chord; a single oscillator per partial
//   is dead still by comparison.
//
// Everything then goes through one master bus with a gentle lowpass, a
// soft-knee compressor and a small synthesised room, so a six-note chord is
// not the sum of six things each mixed as though it were alone. Clipping is
// what "the piano button sounds bad" usually turns out to mean.

const MIDDLE_C = 261.63;

/** Semitones above middle C -> Hz. Equal temperament, so intervals above an
 *  octave (add9's 14, the 13 chord's 21) need no special case. */
export const hz = (semitones) => MIDDLE_C * 2 ** (semitones / 12);

/**
 * How many partials to synthesise for a note at this pitch.
 *
 * Bounded by hearing rather than by taste: partials above ~16kHz are inaudible
 * and cost exactly as much as the ones you can hear, and near the top of the
 * keyboard a fixed count would push most of them past Nyquist, where they
 * alias back down as a metallic ring that is very audible indeed.
 */
function partialCount(f0) {
  return Math.max(4, Math.min(18, Math.floor(16000 / f0)));
}

/**
 * The inharmonicity coefficient, by pitch.
 *
 * Measured values run from about 8e-4 at the bottom of a keyboard to 2e-5 at
 * the top, roughly log-linear between. The exact curve is not important — what
 * matters is that it is *neither zero nor constant*, because a fixed B either
 * makes the treble sound out of tune or does nothing at all for the bass.
 */
function inharmonicity(f0) {
  const octavesFromMiddleC = Math.log2(f0 / MIDDLE_C);
  return 4e-4 * 2 ** (-octavesFromMiddleC * 0.9);
}

/**
 * How long a note rings, by pitch.
 *
 * Bass strings are heavier and lose energy more slowly: a bottom A rings for
 * the better part of a minute where a top C is gone in about one. Capped well
 * under either, because this is a panel that names a chord, not a sustain
 * pedal.
 */
function decayFor(f0) {
  return Math.max(0.9, Math.min(7, 24 / Math.sqrt(f0)));
}

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
 * A small room, as an impulse response.
 *
 * Generated rather than shipped, for the same reason the notes are: an IR file
 * is an audio asset, and this one is two seconds of shaped noise that nobody
 * could pick out of a recorded one at this length. Stereo, with the two
 * channels generated independently — that decorrelation is the whole
 * difference between "a room" and "the same thing, slightly louder".
 */
function reverbIR(c, seconds = 1.9, decay = 2.6) {
  const length = Math.floor(c.sampleRate * seconds);
  const buffer = c.createBuffer(2, length, c.sampleRate);
  for (let channel = 0; channel < 2; channel++) {
    const data = buffer.getChannelData(channel);
    for (let i = 0; i < length; i++) {
      data[i] = (Math.random() * 2 - 1) * (1 - i / length) ** decay;
    }
  }
  return buffer;
}

/**
 * Everything every note goes through, built once.
 *
 * The compressor is doing real work and is not a mastering affectation: a
 * thirteenth chord is six notes of a dozen partials each, all struck inside
 * 35ms, and the sum of them is well past full scale. Without it the loudest
 * moment of the loudest chord is the one that distorts, which is exactly
 * backwards.
 */
function masterBus(c) {
  const tone = c.createBiquadFilter();
  tone.type = "lowpass";
  // Above the top of the keyboard, so this shapes the upper partials rather
  // than dulling the notes: it takes the fizz off the hammer noise and off
  // whatever the highest partials are doing, and leaves the pitch alone.
  tone.frequency.value = 6500;
  tone.Q.value = 0.5;

  const comp = c.createDynamicsCompressor();
  comp.threshold.value = -18;
  comp.knee.value = 24;
  comp.ratio.value = 3;
  comp.attack.value = 0.006;
  comp.release.value = 0.22;

  const dry = c.createGain();
  dry.gain.value = 0.86;

  const wet = c.createGain();
  wet.gain.value = 0.14;
  const room = c.createConvolver();
  room.buffer = reverbIR(c);

  const out = c.createGain();
  out.gain.value = 0.9;

  tone.connect(comp);
  comp.connect(dry).connect(out);
  comp.connect(room).connect(wet).connect(out);
  out.connect(c.destination);

  return tone;
}

// --- one note ---------------------------------------------------------------

/** The two strings of a unison, in cents. One of them is exactly zero, so the
 *  note is still in tune — the second is what beats against it. */
const UNISON_CENTS = [0, 2.4];

/**
 * The hammer: a few milliseconds of bandpassed noise at the onset.
 *
 * Bandpassed around the fundamental rather than left broadband, because a
 * click that sounds the same at every pitch reads as a defect in the playback
 * and not as part of the note.
 */
function hammer(c, at, f0, gain) {
  const length = Math.max(1, Math.floor(c.sampleRate * 0.006));
  const buffer = c.createBuffer(1, length, c.sampleRate);
  const data = buffer.getChannelData(0);
  for (let i = 0; i < length; i++) {
    data[i] = (Math.random() * 2 - 1) * (1 - i / length) ** 2;
  }

  const src = c.createBufferSource();
  src.buffer = buffer;

  const band = c.createBiquadFilter();
  band.type = "bandpass";
  band.frequency.value = Math.min(c.sampleRate / 2 - 1000, f0 * 3.2);
  band.Q.value = 0.7;

  const env = c.createGain();
  env.gain.value = gain * 0.35;

  src.connect(band).connect(env).connect(bus);
  src.start(at);
  src.stop(at + 0.02);
}

/**
 * One struck note.
 *
 * `velocity` is 0–1 and does two things, both of which a real piano does: it
 * scales the level, and it opens the note's own lowpass. A piano played harder
 * is not the same sound turned up — it is a brighter sound, because a faster
 * hammer excites more of the upper partials. Level alone sounds like a volume
 * knob, which is the giveaway.
 */
export function note(semitones, { at = 0, velocity = 0.7, sustain = null } = {}) {
  const c = audio();
  const when = at || c.currentTime + 0.02;
  const f0 = hz(semitones);
  const B = inharmonicity(f0);
  const count = partialCount(f0);
  const ring = sustain ?? decayFor(f0);

  hammer(c, when, f0, velocity);

  // Per note, not per partial: a filter each would be a dozen biquads for one
  // key, and the ear cannot tell them apart from one.
  const colour = c.createBiquadFilter();
  colour.type = "lowpass";
  colour.frequency.value = Math.min(12000, f0 * (6 + velocity * 16));
  colour.Q.value = 0.3;

  const body = c.createGain();
  // Normalised against the partial count, so a bass note with eighteen
  // partials is not four times the level of a treble note with four.
  body.gain.value = (velocity * 0.5) / Math.sqrt(count);
  colour.connect(body).connect(bus);

  for (let n = 1; n <= count; n++) {
    // The stiff-string series. Plain `n` here would be an organ.
    const ratio = n * Math.sqrt(1 + B * n * n);
    const freq = f0 * ratio;
    if (freq > c.sampleRate / 2 - 500) break;

    // Rolloff, with a slight tilt against the even partials. A perfectly
    // smooth 1/n^p spectrum is the sound of a synthesiser being reasonable;
    // real strings have a comb in them from wherever the hammer strikes.
    const level = (1 / n ** 1.25) * (n % 2 === 0 ? 0.82 : 1);
    // Upper partials die first. This one line is most of the difference
    // between "bright, then mellow" and "the same all the way down".
    const life = Math.max(0.12, ring / (1 + (n - 1) * 0.42));

    for (const cents of UNISON_CENTS) {
      const osc = c.createOscillator();
      // A sine per partial, because the partials *are* the timbre — anything
      // richer would stack a second, harmonic series on top of the inharmonic
      // one this function exists to compute.
      osc.type = "sine";
      osc.frequency.value = freq * 2 ** (cents / 1200);

      const env = c.createGain();
      env.gain.setValueAtTime(0.0001, when);
      // 3ms: long enough that there is no click, short enough that it is still
      // a strike. A 10ms attack on a piano note sounds like a bowed one.
      env.gain.linearRampToValueAtTime(level / UNISON_CENTS.length, when + 0.003);
      // Exponential, because a linear fade sounds like a note being switched
      // off rather than one dying away.
      env.gain.exponentialRampToValueAtTime(0.0001, when + life);

      osc.connect(env).connect(colour);
      osc.start(when);
      osc.stop(when + life + 0.05);
    }
  }
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
 */
export function play(rootIndex, intervals, kind) {
  const c = audio();
  const start = c.currentTime + 0.06;

  if (kind === "chord") {
    intervals.forEach((iv, i) => {
      note(rootIndex + iv, {
        at: start + i * ROLL,
        velocity: i === 0 ? 0.78 : Math.max(0.42, 0.68 - i * 0.05),
        // Long enough to still be ringing under the last note of the roll,
        // which is the whole reason to roll it.
        sustain: 2.6,
      });
    });
    return;
  }

  const up = [...intervals, 12];
  const notes = [...up, ...up.slice(0, -1).reverse()];
  const top = (notes.length - 1) / 2;
  notes.forEach((iv, i) => {
    // A shallow arc peaking at the octave: up into it, back off on the way
    // down. ±0.09 is under a decibel and a half — audible as phrasing rather
    // than as an effect.
    const arc = 0.62 + 0.09 * (1 - Math.abs(i - top) / top);
    note(rootIndex + iv, { at: start + i * STEP, velocity: arc, sustain: STEP * 3.4 });
  });
}

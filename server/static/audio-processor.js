// ── PCM16 playback worklet with a small jitter buffer ────────────────────────
// The agent's TTS audio arrives over the network in uneven chunks. Playing it
// the instant it arrives causes buffer underruns → choppy / "flickering" voice.
// This processor smooths that out:
//   • queues incoming chunks (no O(n) array recopy per message),
//   • pre-buffers a short cushion before it starts draining,
//   • on a brief underrun it zero-pads the current frame (never discards audio)
//     and resumes seamlessly the moment more audio arrives,
//   • only re-arms the pre-buffer after a real gap (end of an utterance),
//   • `clear` instantly flushes everything for zero-latency barge-in.
const SAMPLE_RATE = 24000;
const RENDER_QUANTUM = 128;                                 // fixed WebAudio frame size
const PREBUFFER_SAMPLES = Math.floor(SAMPLE_RATE * 0.08);   // ~80ms cushion before (re)starting
const REBUFFER_SILENCE_FRAMES = Math.floor((SAMPLE_RATE * 0.2) / RENDER_QUANTUM); // ~200ms dry ⇒ utterance ended

class RingBufferProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.chunks = [];       // queue of Float32Array chunks
    this.readOffset = 0;    // read cursor within chunks[0]
    this.available = 0;     // total samples queued and not yet played
    this.playing = false;   // gate: only drain once pre-buffered
    this.silentFrames = 0;  // consecutive fully-empty frames while "playing"
    this.port.onmessage = e => {
      if (e.data.pcm) {
        this.chunks.push(e.data.pcm);
        this.available += e.data.pcm.length;
      } else if (e.data.clear) {
        this.chunks = [];
        this.readOffset = 0;
        this.available = 0;
        this.playing = false;
        this.silentFrames = 0;
      }
    };
  }

  // Fill `out` from the queued chunks; zero-pad any remainder on underrun.
  _read(out) {
    let i = 0;
    while (i < out.length && this.chunks.length) {
      const cur = this.chunks[0];
      const n = Math.min(out.length - i, cur.length - this.readOffset);
      out.set(cur.subarray(this.readOffset, this.readOffset + n), i);
      i += n;
      this.readOffset += n;
      this.available -= n;
      if (this.readOffset >= cur.length) {
        this.chunks.shift();
        this.readOffset = 0;
      }
    }
    if (i < out.length) out.fill(0, i);
  }

  process(_, outputs) {
    const out = outputs[0][0];
    if (!out) return true;

    if (!this.playing) {
      // Wait for a cushion before starting so we don't underrun immediately.
      if (this.available >= PREBUFFER_SAMPLES) {
        this.playing = true;
        this.silentFrames = 0;
      } else {
        out.fill(0);
        return true;
      }
    }

    this._read(out);

    if (this.available === 0) {
      // Brief dips mid-utterance keep playing (resume instantly on new audio);
      // a sustained gap means the utterance ended, so re-arm the pre-buffer.
      if (++this.silentFrames > REBUFFER_SILENCE_FRAMES) this.playing = false;
    } else {
      this.silentFrames = 0;
    }
    return true;
  }
}

registerProcessor('audio-processor', RingBufferProcessor);

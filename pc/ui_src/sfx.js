// Maxido sound design. Every sound is synthesized live (no audio files) and written from scratch:
// glassy bells in D Lydian over a slow sub swell, airy noise sweeps, soft ticks for scrolling.
function makeSfx(ctx, dest) {
  const master = ctx.createGain(); master.gain.value = 0.6;
  const comp = ctx.createDynamicsCompressor();
  comp.threshold.value = -16; comp.knee.value = 18; comp.ratio.value = 4; comp.attack.value = 0.003; comp.release.value = 0.25;
  const wet = ctx.createGain(); wet.gain.value = 0.3;
  const conv = ctx.createConvolver(); conv.buffer = impulse(2.4, 2.8);
  master.connect(comp); master.connect(wet); wet.connect(conv); conv.connect(comp); comp.connect(dest || ctx.destination);
  let noiseBuf = null;
  const T = () => ctx.currentTime;

  function impulse(sec, decay) {
    const n = Math.floor(ctx.sampleRate * sec), b = ctx.createBuffer(2, n, ctx.sampleRate);
    for (let c = 0; c < 2; c++) { const d = b.getChannelData(c); for (let i = 0; i < n; i++) d[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / n, decay); }
    return b;
  }
  function noiseBuffer() {
    if (!noiseBuf) { noiseBuf = ctx.createBuffer(1, ctx.sampleRate * 2, ctx.sampleRate); const d = noiseBuf.getChannelData(0); for (let i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1; }
    return noiseBuf;
  }
  function voice(freq, t, o) {
    o = o || {};
    const type = o.type || 'sine', peak = o.peak == null ? 0.15 : o.peak, attack = o.attack || 0.004, decay = o.decay || 0.25;
    const osc = ctx.createOscillator(); osc.type = type; osc.detune.value = o.detune || 0;
    osc.frequency.setValueAtTime(freq, t);
    if (o.to) osc.frequency.exponentialRampToValueAtTime(o.to, t + attack + decay);
    const g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(peak, t + attack); g.gain.exponentialRampToValueAtTime(0.0001, t + attack + decay);
    let n = osc;
    if (o.lp) { const f = ctx.createBiquadFilter(); f.type = 'lowpass'; f.frequency.value = o.lp; osc.connect(f); n = f; }
    n.connect(g); g.connect(master);
    osc.start(t); osc.stop(t + attack + decay + 0.05);
  }
  function bell(freq, t, peak, decay) {                       // glassy bell: fundamental + inharmonic partials
    peak = peak || 0.16; decay = decay || 1.4;
    voice(freq, t, { peak: peak, decay: decay });
    voice(freq * 2.01, t, { peak: peak * 0.45, decay: decay * 0.6 });
    voice(freq * 3.02, t, { peak: peak * 0.22, decay: decay * 0.35 });
    voice(freq * 5.4, t, { peak: peak * 0.1, decay: decay * 0.2 });
  }
  function whoosh(t, dur, f0, f1, peak, q) {
    const src = ctx.createBufferSource(); src.buffer = noiseBuffer(); src.loop = true;
    const f = ctx.createBiquadFilter(); f.type = 'bandpass'; f.Q.value = q || 0.9;
    f.frequency.setValueAtTime(f0, t); f.frequency.exponentialRampToValueAtTime(f1, t + dur);
    const g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(peak, t + dur * 0.65); g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    src.connect(f); f.connect(g); g.connect(master); src.start(t); src.stop(t + dur + 0.05);
  }
  function pad(t, notes, dur, peak, f0, f1) {                 // warm chord with a slowly opening filter
    const f = ctx.createBiquadFilter(); f.type = 'lowpass'; f.Q.value = 0.8;
    f.frequency.setValueAtTime(f0, t); f.frequency.exponentialRampToValueAtTime(f1, t + dur * 0.7);
    const g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(peak, t + dur * 0.55); g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    f.connect(g); g.connect(master);
    notes.forEach(function (n, i) {
      [-7, 7].forEach(function (d) {
        const o = ctx.createOscillator(); o.type = i % 2 ? 'triangle' : 'sawtooth'; o.frequency.value = n; o.detune.value = d;
        const og = ctx.createGain(); og.gain.value = 0.5 / notes.length; o.connect(og); og.connect(f);
        o.start(t); o.stop(t + dur + 0.1);
      });
    });
  }

  return {
    move: function (dir) {                                    // scrolling tick, slightly different pitch per direction
      const t = T(), base = dir === 'left' ? 1560 : dir === 'right' ? 1760 : dir === 'up' ? 1980 : 1400, j = 1 + (Math.random() - 0.5) * 0.03;
      voice(base * j, t, { peak: 0.42, attack: 0.003, decay: 0.07, to: base * 0.72 });
      voice(base * 2.01 * j, t, { peak: 0.1, attack: 0.003, decay: 0.04 });
      whoosh(t, 0.04, 5000, 9000, 0.06, 2);
    },
    ok: function () { const t = T(); bell(987.77, t, 0.15, 0.9); bell(1479.98, t + 0.075, 0.12, 1.1); voice(246.94, t, { type: 'triangle', peak: 0.08, decay: 0.3, lp: 900 }); },
    back: function () { const t = T(); voice(740, t, { peak: 0.12, decay: 0.14, to: 520 }); voice(554.37, t + 0.07, { peak: 0.09, decay: 0.2, to: 392 }); },
    open: function () { const t = T(); whoosh(t, 0.28, 500, 2600, 0.07); voice(659.25, t + 0.05, { peak: 0.07, decay: 0.22 }); },
    pick: function () { const t = T(); bell(1046.5, t, 0.14, 0.9); bell(1568, t + 0.08, 0.12, 1.2); },
    connect: function () { const t = T(); bell(1318.5, t, 0.12, 0.9); bell(1975.5, t + 0.11, 0.12, 1.3); },
    disconnect: function () { const t = T(); bell(1318.5, t, 0.1, 0.8); bell(880, t + 0.11, 0.1, 1.1); },
    error: function () { const t = T(); voice(196, t, { type: 'square', peak: 0.05, decay: 0.16, lp: 700 }); voice(164.8, t + 0.14, { type: 'square', peak: 0.05, decay: 0.22, lp: 600 }); },
    launch: function () {
      const t = T();
      whoosh(t, 1.3, 200, 8000, 0.26, 0.8);
      pad(t, [146.83, 220, 329.63, 369.99], 1.8, 0.16, 300, 3200);
      bell(587.33, t + 0.7, 0.14, 1.8); bell(880, t + 0.8, 0.12, 1.8); bell(1479.98, t + 0.9, 0.1, 2);
      voice(100, t + 0.62, { peak: 0.5, decay: 0.7, to: 38 });
    },
    boot: function (at) {                                     // ~7 s: Maxido wordmark, then the logo reveal at +3.45 s
      const t = at == null ? T() + 0.05 : at;
      voice(73.42, t, { peak: 0.38, attack: 2.4, decay: 3.2 });                               // sub swell on D
      pad(t + 0.1, [146.83, 220, 329.63, 369.99, 554.37], 4.6, 0.2, 220, 2600);               // Dmaj9 pad
      whoosh(t + 0.4, 2.6, 160, 5200, 0.2);
      bell(880, t + 1.0, 0.1, 2.8); bell(1318.51, t + 1.45, 0.07, 3.0);                       // wordmark sparkle
      const r = t + 3.45;
      voice(92, r, { peak: 0.62, attack: 0.01, decay: 0.85, to: 36 });                        // soft boom
      whoosh(r - 0.05, 1.0, 1200, 9000, 0.12, 1.4);
      [587.33, 739.99, 880, 1318.51, 1174.66].forEach(function (f, i) { bell(f, r + 0.06 + i * 0.12, 0.15 - i * 0.01, 2.4); });
      pad(r, [146.83, 220, 293.66, 369.99, 440], 3.0, 0.13, 900, 3800);
      return 7;
    },
    silence: function (sec) {                                 // used when the intro is skipped
      const t = T(); master.gain.cancelScheduledValues(t); master.gain.setTargetAtTime(0, t, 0.07);
      master.gain.setTargetAtTime(0.6, t + (sec || 0.8), 0.01);
    }
  };
}

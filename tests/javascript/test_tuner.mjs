import assert from "node:assert/strict";

const modulePath = "../../src/notewitness/presentation/"
  + "workbench_assets/pitch_estimator.mjs";
const { estimatePitch } = await import(modulePath);

const sampleRate = 48_000;
const sampleCount = 4_096;

function signal(fundamentalHz, harmonicGain = 0) {
  return Float32Array.from({ length: sampleCount }, (_, index) => {
    const phase = 2 * Math.PI * fundamentalHz * index / sampleRate;
    return 0.55 * Math.sin(phase) + harmonicGain * Math.sin(2 * phase);
  });
}

function requirePitch(expectedHz, samples, maximumCents) {
  const estimate = estimatePitch(samples, sampleRate);
  assert.ok(estimate, `expected a pitch estimate near ${expectedHz} Hz`);
  const cents = Math.abs(1200 * Math.log2(estimate.frequencyHz / expectedHz));
  assert.ok(cents <= maximumCents, `${expectedHz} Hz error was ${cents} cents`);
  assert.ok(estimate.confidence >= 0.65);
}

requirePitch(220, signal(220), 5);
requirePitch(440, signal(440), 5);
requirePitch(220, signal(220, 0.7), 5);

const silence = new Float32Array(sampleCount);
assert.equal(estimatePitch(silence, sampleRate), null);

let seed = 12_345;
const noise = Float32Array.from({ length: sampleCount }, () => {
  seed = (seed * 1_664_525 + 1_013_904_223) >>> 0;
  return ((seed / 4_294_967_296) * 2 - 1) * 0.3;
});
assert.equal(estimatePitch(noise, sampleRate), null);

console.log("tuner estimator checks passed");

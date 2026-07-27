const DEFAULTS = Object.freeze({
  minHz: 55,
  maxHz: 1600,
  rmsThreshold: 0.01,
  yinThreshold: 0.16,
});

export function estimatePitch(samples, sampleRate, options = {}) {
  if (!samples || !Number.isFinite(sampleRate) || sampleRate <= 0) return null;
  const settings = { ...DEFAULTS, ...options };
  if (samples.length < 128 || settings.minHz <= 0 || settings.maxHz <= settings.minHz) {
    return null;
  }
  const values = removeMeanAndWindow(samples);
  const rms = rootMeanSquare(values);
  if (rms < settings.rmsThreshold) return null;
  const minimumTau = Math.max(2, Math.floor(sampleRate / settings.maxHz));
  const maximumTau = Math.min(
    Math.floor(sampleRate / settings.minHz),
    Math.floor(values.length / 2),
  );
  if (minimumTau >= maximumTau) return null;
  const difference = yinDifference(values, maximumTau);
  const normalized = cumulativeMeanNormalizedDifference(difference, maximumTau);
  const tau = selectPeriod(normalized, minimumTau, maximumTau, settings.yinThreshold);
  if (!tau) return null;
  const refinedTau = parabolicMinimum(normalized, tau, maximumTau);
  const frequencyHz = sampleRate / refinedTau;
  const confidence = Math.max(0, Math.min(1, 1 - normalized[tau]));
  if (!Number.isFinite(frequencyHz) || confidence < 0.65) return null;
  return { confidence, frequencyHz, rms };
}

function removeMeanAndWindow(samples) {
  let mean = 0;
  for (const sample of samples) mean += sample;
  mean /= samples.length;
  const values = new Float32Array(samples.length);
  const denominator = Math.max(1, samples.length - 1);
  for (let index = 0; index < samples.length; index += 1) {
    const window = 0.5 - 0.5 * Math.cos(2 * Math.PI * index / denominator);
    values[index] = (samples[index] - mean) * window;
  }
  return values;
}

function rootMeanSquare(values) {
  let energy = 0;
  for (const value of values) energy += value * value;
  return Math.sqrt(energy / values.length);
}

function yinDifference(values, maximumTau) {
  const difference = new Float64Array(maximumTau + 1);
  for (let tau = 1; tau <= maximumTau; tau += 1) {
    let sum = 0;
    const limit = values.length - tau;
    for (let index = 0; index < limit; index += 1) {
      const delta = values[index] - values[index + tau];
      sum += delta * delta;
    }
    difference[tau] = sum;
  }
  return difference;
}

function cumulativeMeanNormalizedDifference(difference, maximumTau) {
  const normalized = new Float64Array(maximumTau + 1);
  normalized[0] = 1;
  let running = 0;
  for (let tau = 1; tau <= maximumTau; tau += 1) {
    running += difference[tau];
    normalized[tau] = running ? difference[tau] * tau / running : 1;
  }
  return normalized;
}

function selectPeriod(normalized, minimumTau, maximumTau, threshold) {
  for (let tau = minimumTau; tau < maximumTau; tau += 1) {
    if (normalized[tau] >= threshold) continue;
    while (tau + 1 <= maximumTau && normalized[tau + 1] < normalized[tau]) tau += 1;
    return tau;
  }
  let bestTau = 0;
  for (let tau = minimumTau; tau <= maximumTau; tau += 1) {
    if (!bestTau || normalized[tau] < normalized[bestTau]) bestTau = tau;
  }
  return bestTau && normalized[bestTau] < 0.35 ? bestTau : 0;
}

function parabolicMinimum(values, tau, maximumTau) {
  if (tau <= 1 || tau >= maximumTau) return tau;
  const left = values[tau - 1];
  const center = values[tau];
  const right = values[tau + 1];
  const denominator = left - 2 * center + right;
  if (!Number.isFinite(denominator) || Math.abs(denominator) < 1e-12) return tau;
  const correction = 0.5 * (left - right) / denominator;
  return Math.abs(correction) <= 1 ? tau + correction : tau;
}

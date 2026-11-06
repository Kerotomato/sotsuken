/*
 * GDX-RB: force-derivative phase estimator.
 * These outputs are heuristic indicators of belt motion, NOT direct airflow
 * measurements, and HOLD_CANDIDATE is not a clinical apnea diagnosis.
 * No external dependencies. Embedded verbatim inside GDX_RB_Force_Live.html.
 */
(function (root) {
  'use strict';
  const SLOPE_THRESHOLDS = Object.freeze({sensitive: 0.09, normal: 0.20, conservative: 0.40}); // N/s
  class BreathClassifier {
    constructor() { this.reset(); }
    reset() {
      this.history = [];
      this.smoothed = null;
      this.lastT = null;
      this.lastMotionT = null;
      this.candidate = null;
      this.candidateSinceT = null;
      this.state = 'WARMUP';
    }
    process(t, forceN, settings = {}) {
      if (!Number.isFinite(t) || !Number.isFinite(forceN)) return null;
      if (this.lastT !== null && (t <= this.lastT || t - this.lastT > 2.5)) this.reset();
      const holdS = Math.min(12, Math.max(3, Number(settings.holdSeconds) || 3));
      const sensitivity = settings.sensitivity in SLOPE_THRESHOLDS ? settings.sensitivity : 'normal';
      const threshold = SLOPE_THRESHOLDS[sensitivity];
      const polarity = settings.polarity === 'decrease' ? -1 : 1;
      const dt = this.lastT === null ? 0 : t - this.lastT;
      const alpha = this.lastT === null ? 1 : 1 - Math.exp(-dt / 0.30);
      this.smoothed = this.smoothed === null ? forceN : this.smoothed + alpha * (forceN - this.smoothed);
      this.lastT = t;
      this.history.push({t, f: this.smoothed});
      while (this.history.length && this.history[0].t < t - Math.max(holdS + 2, 9)) this.history.shift();
      const firstT = this.history[0].t;
      const window = this.history.filter(p => p.t >= t - 1.0);
      let slope = 0;
      if (window.length >= 3 && window[window.length - 1].t - window[0].t >= 0.65) {
        const meanT = window.reduce((s,p)=>s+p.t, 0)/window.length;
        const meanF = window.reduce((s,p)=>s+p.f, 0)/window.length;
        let cov = 0, variance = 0;
        for (const p of window) { cov += (p.t-meanT)*(p.f-meanF); variance += (p.t-meanT)**2; }
        slope = variance > 0 ? cov/variance : 0;
      } else {
        this.state = 'WARMUP';
        return {code:this.state, smooth:this.smoothed, slope:null, threshold};
      }
      const signedSlope = slope*polarity;
      const direction = signedSlope >= threshold ? 'INHALE' : signedSlope <= -threshold ? 'EXHALE' : 'QUIET';
      if (direction !== 'QUIET') {
        this.lastMotionT = t;
        if (this.candidate !== direction) { this.candidate = direction; this.candidateSinceT = t; }
        if (t - this.candidateSinceT >= 0.23) this.state = direction;
        else this.state = 'TRANSITION';
      } else {
        this.candidate = null;
        this.candidateSinceT = null;
        this.state = 'TRANSITION';
        const inactiveS = t - (this.lastMotionT === null ? firstT : this.lastMotionT);
        if (inactiveS >= holdS) {
          const tail = this.history.filter(p=>p.t >= t - holdS);
          const amplitude = Math.max(...tail.map(p=>p.f)) - Math.min(...tail.map(p=>p.f));
          // Static tension is compatible with breath holding, a pause, or insufficient sensor motion.
          // This intentionally avoids claiming clinically verified apnea.
          if (tail.length >= 3 && amplitude <= Math.max(0.20, threshold * holdS * 0.85)) {
            this.state = 'HOLD_CANDIDATE';
          }
        }
      }
      return {code:this.state, smooth:this.smoothed, slope:signedSlope, threshold};
    }
  }
  root.BreathClassifier = BreathClassifier;
  root.GDXRB_SLOPE_THRESHOLDS = SLOPE_THRESHOLDS;
  if (typeof module !== 'undefined' && module.exports) module.exports = {BreathClassifier, SLOPE_THRESHOLDS};
})(typeof window !== 'undefined' ? window : globalThis);

import { ModelRunEntry } from '../types/api';

/**
 * Presentation decisions for the model registry screen, kept out of the
 * component so they can be tested directly - the same split
 * `resultModel.ts` uses for the litigation result page.
 *
 * The rule these functions exist to enforce: this screen reports what the
 * pipeline decided, it never re-decides it. Calibration used to be
 * recomputed here as `n_train >= 200 ? 'Isotonic' : 'Sigmoid'`, which put
 * s12's ISOTONIC_MIN_ROWS in a React component where it could quietly
 * disagree with the model it was describing. s12 now records the branch it
 * actually took and this reads it.
 */

/** The calibration branch s12 took, as recorded - never re-derived. */
export function calibrationLabel(run: Pick<ModelRunEntry, 'calibration'>): string {
  if (!run.calibration) return '—';
  return run.calibration.charAt(0).toUpperCase() + run.calibration.slice(1);
}

/**
 * The HIGH cutoff, or the fact that there isn't one.
 *
 * A suppressed HIGH band is the most load-bearing honesty signal on this
 * screen: it means no cutoff cleared both the precision target and the
 * stage's own base rate, so the product declines to show a HIGH badge for
 * that stage at all. The reason is returned separately by
 * `suppressionReason`, so the component can render it as wrapping prose
 * rather than squeeze it into a table cell.
 */
export function highThresholdLabel(run: Pick<ModelRunEntry, 'thresholds'>): string {
  const { t_high: tHigh, high } = run.thresholds;
  if (tHigh != null) return tHigh.toFixed(3);
  if (high === 'suppressed') return 'Suppressed';
  return '—';
}

/** The recorded reason a stage has no HIGH band, or null when it has one. */
export function suppressionReason(run: Pick<ModelRunEntry, 'thresholds'>): string | null {
  if (run.thresholds.t_high != null) return null;
  if (run.thresholds.high !== 'suppressed') return null;
  return run.thresholds.reason ?? 'no reason recorded';
}

/**
 * The algorithm actually serving this stage.
 *
 * s12 reports `none` when a stage had nothing trainable - an empty or
 * single-class training split. That is a real outcome the registry must
 * state plainly rather than render as the literal string "none" next to
 * the word "Shipped", which reads like a model called none.
 */
export function shippedLabel(run: Pick<ModelRunEntry, 'algo'>): string {
  return run.algo === 'none' ? 'nothing shipped for this stage' : run.algo;
}

/** True when no model serves this stage, so its open stages go unscored. */
export function isUnshipped(run: Pick<ModelRunEntry, 'algo'>): boolean {
  return run.algo === 'none';
}

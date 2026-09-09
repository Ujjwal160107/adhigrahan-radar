import { describe, expect, it } from 'vitest';
import {
  calibrationLabel, highThresholdLabel, isUnshipped, shippedLabel, suppressionReason,
} from './modelRunView';

describe('calibrationLabel', () => {
  it('reports the branch s12 recorded, both ways', () => {
    expect(calibrationLabel({ calibration: 'sigmoid' })).toBe('Sigmoid');
    expect(calibrationLabel({ calibration: 'isotonic' })).toBe('Isotonic');
  });

  it('does not re-derive calibration from the row count', () => {
    // The screen used to compute `n_train >= 200 ? 'Isotonic' : 'Sigmoid'`,
    // duplicating s12's ISOTONIC_MIN_ROWS in the browser. Whatever the
    // pipeline says is what shows, even if it disagrees with that rule -
    // otherwise the registry describes a model that was never trained.
    expect(calibrationLabel({ calibration: 'sigmoid' })).toBe('Sigmoid');
  });

  it('renders an em dash when the pipeline recorded nothing', () => {
    expect(calibrationLabel({ calibration: null })).toBe('—');
  });
});

describe('highThresholdLabel', () => {
  it('shows a real cutoff at three decimals', () => {
    expect(highThresholdLabel({ thresholds: { t_high: 0.4405, t_med: 0.05 } })).toBe('0.441');
  });

  it('says Suppressed rather than inventing a cutoff', () => {
    expect(highThresholdLabel({ thresholds: { high: 'suppressed', reason: 'x' } }))
      .toBe('Suppressed');
  });

  it('never renders a suppressed band as 0.000', () => {
    expect(highThresholdLabel({ thresholds: { high: 'suppressed' } })).not.toContain('0');
  });
});

describe('suppressionReason', () => {
  it('surfaces the reason s12 recorded', () => {
    const reason = 'no cutoff at or above the stage base rate reached precision >= 0.7';
    expect(suppressionReason({ thresholds: { high: 'suppressed', reason } })).toBe(reason);
  });

  it('is null when the stage has a real HIGH band', () => {
    expect(suppressionReason({ thresholds: { t_high: 0.441 } })).toBeNull();
  });

  it('admits the gap rather than going quiet when no reason was recorded', () => {
    expect(suppressionReason({ thresholds: { high: 'suppressed' } })).toBe('no reason recorded');
  });
});

describe('shippedLabel', () => {
  it('names the algorithm that serves the stage', () => {
    expect(shippedLabel({ algo: 'hgb_calibrated' })).toBe('hgb_calibrated');
    expect(shippedLabel({ algo: 'base_rate' })).toBe('base_rate');
  });

  it('reads "none" as an outcome, not as a model called none', () => {
    // s12 reports algo='none' when a stage had an empty or single-class
    // training split and nothing could be fitted.
    expect(shippedLabel({ algo: 'none' })).toMatch(/nothing shipped/i);
    expect(isUnshipped({ algo: 'none' })).toBe(true);
    expect(isUnshipped({ algo: 'base_rate' })).toBe(false);
  });
});

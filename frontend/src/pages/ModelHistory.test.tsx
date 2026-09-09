import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ModelHistory } from './ModelHistory';
import { ModelRunEntry } from '../types/api';

const getModelHistory = vi.fn();

vi.mock('../api/client', () => ({
  api: { getModelHistory: (...args: unknown[]) => getModelHistory(...args) },
}));

function run(over: Partial<ModelRunEntry> = {}): ModelRunEntry {
  return {
    model_version: 'mv-20260821-01',
    stage: 'award_3g_23',
    algo: 'hgb_calibrated',
    shipped: 1,
    n_train: 108,
    n_test: 40,
    n_test_real: 0,
    n_test_synthetic: 40,
    cutoff_date: '2025-08-08',
    calibration: 'sigmoid',
    metrics: {
      base_rate: { roc_auc: null, pr_auc: null, brier: 0.24, train_brier: 0.24,
        train_positive_rate: 0.25 },
      hgb_calibrated: { roc_auc: 0.61, pr_auc: 0.4, brier: 0.2, train_brier: 0.18,
        train_positive_rate: 0.25 },
    },
    thresholds: { t_high: 0.256, t_med: 0.127 },
    notes: 'n_test_real=0: synthetic-holdout diagnostic',
    trained_at: '2026-08-21T00:00:00+00:00',
    ...over,
  };
}

describe('ModelHistory', () => {
  beforeEach(() => getModelHistory.mockReset());

  it('reports the calibration the pipeline recorded, not one it recomputes', async () => {
    // n_train is deliberately over the 200 the screen used to branch on
    // while the pipeline recorded sigmoid. The registry must describe the
    // model that was actually trained.
    getModelHistory.mockResolvedValue({
      runs: [run({ n_train: 222, calibration: 'sigmoid' })],
    });
    render(<ModelHistory />);
    expect(await screen.findByText('Sigmoid')).toBeInTheDocument();
    expect(screen.queryByText('Isotonic')).not.toBeInTheDocument();
  });

  it('shows the HIGH cutoff when the stage earned one', async () => {
    getModelHistory.mockResolvedValue({ runs: [run()] });
    render(<ModelHistory />);
    expect(await screen.findByText('0.256')).toBeInTheDocument();
    expect(screen.queryByText(/No HIGH band for this stage/i)).not.toBeInTheDocument();
  });

  it('says why a stage has no HIGH band instead of just "Suppressed"', async () => {
    getModelHistory.mockResolvedValue({
      runs: [run({
        stage: 'possession',
        algo: 'base_rate',
        thresholds: {
          high: 'suppressed',
          reason: 'no cutoff at or above the stage base rate reached precision >= 0.7',
        },
      })],
    });
    render(<ModelHistory />);
    expect(await screen.findByText('Suppressed')).toBeInTheDocument();
    expect(screen.getByText(/No HIGH band for this stage/i)).toBeInTheDocument();
    expect(screen.getByText(/at or above the stage base rate/i)).toBeInTheDocument();
  });

  it('reads an unshipped stage as an outcome, not as a model called none', async () => {
    getModelHistory.mockResolvedValue({
      runs: [run({
        stage: 'possession',
        algo: 'none',
        metrics: {},
        thresholds: { high: 'suppressed', reason: 'no scoreable model' },
      })],
    });
    render(<ModelHistory />);
    expect(await screen.findByText(/nothing shipped for this stage/i)).toBeInTheDocument();
    expect(screen.getByText(/No algorithm could be fitted/i)).toBeInTheDocument();
  });

  it('discloses a wholly synthetic holdout when no stage carries real rows', async () => {
    getModelHistory.mockResolvedValue({ runs: [run()] });
    render(<ModelHistory />);
    expect(await screen.findByText(/No real acquisition rows reached/i)).toBeInTheDocument();
    expect(screen.getByText(/n_test_real = 0 for every stage/i)).toBeInTheDocument();
  });

  it('names the one stage real gazette rows back, and says the rest are synthetic', async () => {
    // Which stages are real is read from the registry, never assumed:
    // only the 3A->3D interval is ever gazetted, and s12 counts it per stage.
    getModelHistory.mockResolvedValue({
      runs: [
        run({ stage: 'notification_3a_11', n_test_real: 52, n_test_real_stages: 40,
          n_test_synthetic: 60, n_test: 112 }),
        run(),
      ],
    });
    render(<ModelHistory />);
    expect(await screen.findByText(/Real data reaches one stage only/i)).toBeInTheDocument();
    expect(screen.getByText(/52 real holdout rows/i)).toBeInTheDocument();
    expect(screen.getByText(/40 gazette intervals/i)).toBeInTheDocument();
    expect(screen.queryByText(/No real acquisition rows reached/i)).not.toBeInTheDocument();
  });
});

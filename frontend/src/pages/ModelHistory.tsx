import React, { useEffect, useState } from 'react';
import { api } from '../api/client';
import { ModelRunEntry } from '../types/api';
import {
  calibrationLabel, highThresholdLabel, isUnshipped, shippedLabel, suppressionReason,
} from './modelRunView';

const STAGE_LABELS: Record<string, string> = {
  notification_3a_11: 'Notification (3A / S.11)',
  declaration_3d_19: 'Declaration (3D / S.19)',
  award_3g_23: 'Award (3G / S.23)',
  compensation_disbursed: 'Compensation disbursed',
  possession: 'Possession',
};

function fmt(n: number | null | undefined): string {
  return n == null ? '—' : n.toFixed(3);
}

export const ModelHistory: React.FC = () => {
  const [runs, setRuns] = useState<ModelRunEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.getModelHistory()
      .then((res) => { if (!cancelled) setRuns(res.runs); })
      .catch(() => { if (!cancelled) setError('Could not load the model registry.'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  if (loading) {
    return <div className="p-16 font-mono text-sm text-ink-muted">Reading the model registry…</div>;
  }

  return (
    <div className="w-full px-8 sm:px-16 md:px-20 pb-16 max-w-6xl mx-auto">
      <div className="pt-10 pb-8">
        <h1 className="font-serif italic font-bold text-3xl sm:text-4xl text-black tracking-tight select-none">
          Model registry
        </h1>
        <p className="font-mono text-xs text-ink-muted mt-2 max-w-3xl">
          One calibrated classifier per lifecycle stage, trained offline. Every score the
          product shows was computed here, at build time - nothing is retrained or re-scored
          on request.
        </p>
      </div>

      {error ? (
        <div className="border-2 border-radar-red bg-[#FDE8E8] p-4 font-mono text-sm text-black mb-6">
          {error}
        </div>
      ) : null}

      <div className="border-2 border-radar-amber bg-[#FEF3C7] p-4 font-mono text-xs text-black mb-6">
        <strong>No real acquisition dataset was available in this environment.</strong>{' '}
        Every metric below is measured on a synthetic, deterministically generated corpus
        (n_test_real = 0 for every stage). These numbers demonstrate the mechanism - a real
        calibrated per-stage classifier with a genuine holdout, threshold selection and
        baseline comparison - not a validated real-world performance claim.
      </div>

      {runs.map((run) => {
        return (
          <div key={run.stage} className="border-2 border-black bg-white mb-6">
            <div className="p-5 border-b-2 border-black flex flex-wrap items-baseline justify-between gap-3">
              <h2 className="font-mono text-sm font-bold uppercase tracking-wider">
                {STAGE_LABELS[run.stage] || run.stage}
              </h2>
              <span className="font-mono text-xs text-ink-muted">
                Shipped:{' '}
                <strong className={isUnshipped(run) ? 'text-radar-amber' : 'text-black'}>
                  {shippedLabel(run)}
                </strong>{' '}
                · {run.model_version}
              </span>
            </div>
            <div className="p-5 grid grid-cols-2 sm:grid-cols-4 gap-4 font-mono text-xs border-b-2 border-black">
              <div>
                <div className="text-[10px] uppercase text-ink-muted">Train / test rows</div>
                <div className="font-bold mt-1">{run.n_train} / {run.n_test}</div>
              </div>
              <div>
                <div className="text-[10px] uppercase text-ink-muted">Real test rows</div>
                <div className="font-bold mt-1 text-radar-amber">{run.n_test_real}</div>
              </div>
              <div>
                <div className="text-[10px] uppercase text-ink-muted">Calibration</div>
                <div className="font-bold mt-1">{calibrationLabel(run)}</div>
              </div>
              <div>
                <div className="text-[10px] uppercase text-ink-muted">HIGH threshold</div>
                <div className="font-bold mt-1">{highThresholdLabel(run)}</div>
              </div>
            </div>

            {suppressionReason(run) ? (
              <div className="px-5 py-3 border-b-2 border-black bg-[#FEF3C7] font-mono text-xs text-black">
                <strong>No HIGH band for this stage.</strong>{' '}
                {suppressionReason(run)}. A HIGH cutoff has to clear both the
                precision target and the rate at which this stage overruns anyway -
                below that it would fire on the ordinary project, so the product
                shows no HIGH badge here rather than one it did not earn.
              </div>
            ) : null}
            <div className="p-5 overflow-x-auto">
              <table className="w-full text-left font-mono text-xs border-collapse">
                <thead>
                  <tr className="border-b-2 border-black">
                    <th className="pb-2 pr-4">Algorithm</th>
                    <th className="pb-2 pr-4">ROC-AUC</th>
                    <th className="pb-2 pr-4">PR-AUC</th>
                    <th className="pb-2 pr-4">Brier (holdout)</th>
                    <th className="pb-2">Shipped</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-black/10">
                  {Object.keys(run.metrics).length === 0 ? (
                    <tr>
                      <td colSpan={5} className="py-2 text-ink-muted">
                        No algorithm could be fitted for this stage - see the note below.
                      </td>
                    </tr>
                  ) : null}
                  {Object.entries(run.metrics).map(([algo, m]) => (
                    <tr key={algo} className={algo === run.algo ? 'font-bold' : ''}>
                      <td className="py-2 pr-4">{algo}</td>
                      <td className="py-2 pr-4">{fmt(m.roc_auc)}</td>
                      <td className="py-2 pr-4">{fmt(m.pr_auc)}</td>
                      <td className="py-2 pr-4">{fmt(m.brier)}</td>
                      <td className="py-2">{algo === run.algo ? '✓' : ''}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="px-5 pb-5 font-mono text-xs text-ink-muted">{run.notes}</div>
          </div>
        );
      })}
    </div>
  );
};

import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api/client';
import { DashboardRisk } from '../types/api';
import { RiskBadge } from '../components/RiskBadge';

export const RiskDashboard: React.FC = () => {
  const [data, setData] = useState<DashboardRisk | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.getDashboardRisk()
      .then((d) => { if (!cancelled) setData(d); })
      .catch(() => { if (!cancelled) setError('Could not load the risk dashboard.'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  if (loading) {
    return <div className="p-16 font-mono text-sm text-ink-muted">Reading the risk model…</div>;
  }
  if (error || !data) {
    return (
      <div className="w-full px-8 sm:px-16 md:px-20 pt-16 max-w-6xl mx-auto">
        <div className="border-2 border-radar-red bg-[#FDE8E8] p-8 font-mono text-sm text-black">
          {error || 'No data available.'}
        </div>
      </div>
    );
  }

  const total = data.bands.HIGH + data.bands.MEDIUM + data.bands.LOW;

  return (
    <div className="w-full px-8 sm:px-16 md:px-20 pb-16 max-w-7xl mx-auto">
      <div className="pt-10 pb-8">
        <h1 className="font-serif italic font-bold text-3xl sm:text-4xl md:text-5xl text-black tracking-tight select-none mb-3">
          Which projects are about to miss a deadline?
        </h1>
        <p className="font-mono text-xs sm:text-sm text-ink-muted">
          {data.model_version ? (
            <>Model {data.model_version}{data.trained_at ? ` · trained ${data.trained_at.slice(0, 10)}` : ''}</>
          ) : (
            'No model has been trained yet.'
          )}
        </p>
      </div>

      <div className="border-2 border-black bg-white mb-8 font-mono text-xs sm:text-sm">
        <div className="grid grid-cols-2 sm:grid-cols-4 divide-y sm:divide-y-0 sm:divide-x divide-black">
          <div className="p-4">
            <div className="text-[10px] uppercase tracking-wider text-ink-muted">Scored stages</div>
            <div className="font-bold text-lg mt-1">{total}</div>
          </div>
          <div className="p-4">
            <div className="text-[10px] uppercase tracking-wider text-ink-muted">HIGH risk</div>
            <div className="font-bold text-lg mt-1 text-radar-red">{data.bands.HIGH}</div>
          </div>
          <div className="p-4">
            <div className="text-[10px] uppercase tracking-wider text-ink-muted">Deadline within 90 days</div>
            <div className="font-bold text-lg mt-1">{data.deadlines.d90}</div>
          </div>
          <div className="p-4">
            <div className="text-[10px] uppercase tracking-wider text-ink-muted">Median lead time</div>
            <div className="font-bold text-lg mt-1">
              {data.median_lead_time_days != null ? `${data.median_lead_time_days}d` : '—'}
            </div>
          </div>
        </div>
      </div>

      <p className="font-mono text-xs text-ink-muted mb-8 max-w-3xl">
        Lead time is measured against each project's own statutory or administrative-target
        deadline, not asserted. A negative value means the stage is already past its deadline.
        This build's model is trained on synthetic acquisition data - see{' '}
        <Link to="/models" className="underline">Model history</Link> for the full disclosure.
      </p>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-0 border-2 border-black bg-white mb-8">
        <div className="lg:col-span-7 lg:border-r-2 border-black">
          <div className="p-5 sm:p-6 border-b-2 border-black flex items-baseline justify-between gap-4">
            <h2 className="font-mono text-sm font-bold uppercase tracking-wider">
              Top at-risk projects
            </h2>
            <Link to="/projects" className="font-mono text-xs underline">View all →</Link>
          </div>
          {data.top_at_risk.length === 0 ? (
            <div className="p-8 font-mono text-sm text-ink-muted">No scored projects yet.</div>
          ) : (
            data.top_at_risk.map((p, idx) => (
              <Link
                key={`${p.project_id}-${p.stage}`}
                to={`/projects/${p.project_id}`}
                className={`block p-4 sm:p-5 font-mono text-xs sm:text-sm hover:bg-paper-light ${
                  idx < data.top_at_risk.length - 1 ? 'border-b border-black/20' : ''
                }`}
              >
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0">
                    <div className="font-bold truncate">{p.name}</div>
                    <div className="text-ink-muted mt-0.5">
                      {p.district} · {p.stage.replace(/_/g, ' ')} · deadline {p.deadline_on}
                    </div>
                  </div>
                  <RiskBadge band={p.risk_band} probability={p.delay_probability} size="sm" />
                </div>
              </Link>
            ))
          )}
        </div>

        <div className="lg:col-span-5">
          <div className="p-5 sm:p-6 border-b-2 border-black">
            <h2 className="font-mono text-sm font-bold uppercase tracking-wider">
              Risk by district
            </h2>
          </div>
          <div className="max-h-[60vh] overflow-y-auto">
            {data.districts.map((d, idx) => {
              const dtotal = d.high + d.medium + d.low || 1;
              return (
                <div
                  key={d.district}
                  className={`p-4 font-mono text-xs sm:text-sm ${
                    idx < data.districts.length - 1 ? 'border-b border-black/20' : ''
                  }`}
                >
                  <div className="flex items-baseline justify-between mb-2">
                    <span className="font-bold">{d.district}</span>
                    <span className="text-ink-muted">{d.projects} projects</span>
                  </div>
                  <div className="h-2.5 w-full border border-black flex">
                    <div className="bg-radar-red h-full" style={{ width: `${(d.high / dtotal) * 100}%` }} />
                    <div className="bg-radar-amber h-full" style={{ width: `${(d.medium / dtotal) * 100}%` }} />
                    <div className="bg-radar-green h-full" style={{ width: `${(d.low / dtotal) * 100}%` }} />
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
};

import React, { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../api/client';
import { ProjectListItem } from '../types/api';
import { RiskBadge } from '../components/RiskBadge';

const DISTRICTS = ['Sultanpur', 'Amethi', 'Pratapgarh', 'Raebareli', 'Ayodhya',
  'Barabanki', 'Gonda', 'Basti'];
const RISK_BANDS = ['HIGH', 'MEDIUM', 'LOW'];
const PAGE_SIZE = 20;

export const ProjectPortfolio: React.FC = () => {
  const [params, setParams] = useSearchParams();
  const [items, setItems] = useState<ProjectListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const district = params.get('district') || '';
  const riskBand = params.get('risk_band') || '';
  const status = params.get('status') || '';
  const page = Math.max(0, parseInt(params.get('page') || '0', 10));

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api.searchProjects({
      district, riskBand, status, sort: 'delay_probability',
      limit: PAGE_SIZE, offset: page * PAGE_SIZE,
    })
      .then((res) => {
        if (cancelled) return;
        setItems(res.projects);
        setTotal(res.total);
        setError(null);
      })
      .catch(() => { if (!cancelled) setError('Could not load the project list.'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [district, riskBand, status, page]);

  const updateFilter = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value); else next.delete(key);
    next.delete('page');
    setParams(next);
  };

  const goToPage = (nextPage: number) => {
    const next = new URLSearchParams(params);
    next.set('page', String(nextPage));
    setParams(next);
  };

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="w-full px-8 sm:px-16 md:px-20 pb-16 max-w-7xl mx-auto">
      <div className="pt-10 pb-8">
        <h1 className="font-serif italic font-bold text-3xl sm:text-4xl md:text-5xl text-black tracking-tight select-none">
          Project portfolio
        </h1>
      </div>

      <div className="border-2 border-black bg-white mb-6 p-4 sm:p-5 flex flex-wrap gap-4 font-mono text-xs">
        <label className="flex items-center gap-2">
          <span className="text-ink-muted uppercase">District</span>
          <select
            value={district}
            onChange={(e) => updateFilter('district', e.target.value)}
            className="border-2 border-black px-2 py-1 bg-white"
          >
            <option value="">All</option>
            {DISTRICTS.map((d) => <option key={d} value={d}>{d}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-2">
          <span className="text-ink-muted uppercase">Risk band</span>
          <select
            value={riskBand}
            onChange={(e) => updateFilter('risk_band', e.target.value)}
            className="border-2 border-black px-2 py-1 bg-white"
          >
            <option value="">All</option>
            {RISK_BANDS.map((b) => <option key={b} value={b}>{b}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-2">
          <span className="text-ink-muted uppercase">Status</span>
          <select
            value={status}
            onChange={(e) => updateFilter('status', e.target.value)}
            className="border-2 border-black px-2 py-1 bg-white"
          >
            <option value="">All</option>
            <option value="open">Open</option>
            <option value="completed">Completed</option>
            <option value="lapsed">Lapsed</option>
          </select>
        </label>
        <span className="ml-auto text-ink-muted self-center">{total} projects</span>
      </div>

      {error ? (
        <div className="border-2 border-radar-red bg-[#FDE8E8] p-4 font-mono text-sm text-black mb-6">
          {error}
        </div>
      ) : null}

      <div className="border-2 border-black bg-white">
        {loading ? (
          <div className="p-8 font-mono text-sm text-ink-muted">Loading projects…</div>
        ) : items.length === 0 ? (
          <div className="p-8 font-mono text-sm text-ink-muted">No projects match these filters.</div>
        ) : (
          items.map((p, idx) => (
            <Link
              key={p.id}
              to={`/projects/${p.id}`}
              className={`block p-4 sm:p-5 font-mono text-xs sm:text-sm hover:bg-paper-light ${
                idx < items.length - 1 ? 'border-b-2 border-black' : ''
              }`}
            >
              <div className="flex items-center justify-between gap-4">
                <div className="min-w-0 flex-1">
                  <div className="font-bold truncate">{p.name}</div>
                  <div className="text-ink-muted mt-1">
                    {p.district} · {p.act === 'NH_1956' ? 'NH Act 1956' : 'RFCTLARR 2013'} ·{' '}
                    {p.current_stage?.replace(/_/g, ' ') || p.status} · {p.status}
                  </div>
                </div>
                <div className="flex-shrink-0 text-right">
                  <RiskBadge band={p.risk_band} probability={p.delay_probability} size="sm" />
                  {p.days_remaining != null && (
                    <div className="text-ink-muted mt-1">
                      {p.days_remaining < 0
                        ? `${Math.abs(p.days_remaining)}d overdue`
                        : `${p.days_remaining}d remaining`}
                    </div>
                  )}
                </div>
              </div>
            </Link>
          ))
        )}
      </div>

      {totalPages > 1 && (
        <div className="flex items-center justify-between mt-6 font-mono text-xs">
          <button
            type="button"
            disabled={page === 0}
            onClick={() => goToPage(page - 1)}
            className="border-2 border-black px-4 py-2 disabled:opacity-30 cursor-pointer disabled:cursor-not-allowed"
          >
            ← Previous
          </button>
          <span>Page {page + 1} of {totalPages}</span>
          <button
            type="button"
            disabled={page >= totalPages - 1}
            onClick={() => goToPage(page + 1)}
            className="border-2 border-black px-4 py-2 disabled:opacity-30 cursor-pointer disabled:cursor-not-allowed"
          >
            Next →
          </button>
        </div>
      )}
    </div>
  );
};

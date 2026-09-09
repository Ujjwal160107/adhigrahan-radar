import React, { useEffect, useState } from 'react';
import { AppFooter } from '../components/AppFooter';
import { api } from '../api/client';
import { StatusBand } from '../types/api';

interface SearchProps {
  onSearch: (surveyNo: string, village: string) => void;
  onOpenDashboard?: () => void;
  onOpenWatchlist?: () => void;
}

interface QuickPick {
  survey_no: string;
  village: string;
  status: StatusBand;
  confidence: number | null;
}

const BAND_LABEL: Record<StatusBand, string> = {
  RED: 'Active litigation',
  AMBER: 'Possible connection',
  GREEN: 'Clean record',
};

const BAND_DOT: Record<StatusBand, string> = {
  RED: 'bg-radar-red',
  AMBER: 'bg-radar-amber',
  GREEN: 'bg-radar-green',
};

export const Search: React.FC<SearchProps> = ({ onSearch, onOpenDashboard, onOpenWatchlist }) => {
  const [surveyNo, setSurveyNo] = useState('');
  const [village, setVillage] = useState('');
  const [validationError, setValidationError] = useState<string | null>(null);
  // Quick-select shortcuts are derived from the live index, not hand-typed:
  // one real example per status band, picked by confidence.
  const [quickPicks, setQuickPicks] = useState<QuickPick[]>([]);

  useEffect(() => {
    let cancelled = false;
    api.getMap()
      .then((map) => {
        if (cancelled) return;
        const byBand: Partial<Record<StatusBand, QuickPick>> = {};
        for (const f of map.features) {
          const band = f.properties.status;
          const candidate: QuickPick = {
            survey_no: f.properties.survey_no,
            village: f.properties.village,
            status: band,
            confidence: f.properties.confidence,
          };
          const existing = byBand[band];
          if (!existing || (candidate.confidence ?? 0) > (existing.confidence ?? 0)) {
            byBand[band] = candidate;
          }
        }
        setQuickPicks((['RED', 'AMBER', 'GREEN'] as StatusBand[])
          .map((b) => byBand[b])
          .filter((p): p is QuickPick => Boolean(p)));
      })
      .catch(() => { if (!cancelled) setQuickPicks([]); });
    return () => { cancelled = true; };
  }, []);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!surveyNo.trim() && !village.trim()) {
      setValidationError('Enter a survey number or a village to search.');
      return;
    }
    setValidationError(null);
    onSearch(surveyNo.trim(), village.trim());
  };

  const handleQuickPick = (quickSurvey: string, quickVillage: string) => {
    setValidationError(null);
    setSurveyNo(quickSurvey);
    setVillage(quickVillage);
    onSearch(quickSurvey, quickVillage);
  };

  return (
    <div className="min-h-[calc(100vh-120px)] flex flex-col justify-between px-12 sm:px-20 pb-10">
      {/* Centered Main Stage */}
      <div className="w-full flex-1 flex flex-col justify-center items-start max-w-6xl mx-auto my-auto">
        {/* Main Headline */}
        <h1 className="font-serif italic font-bold text-4xl sm:text-5xl md:text-6xl text-black tracking-tight mb-8 select-none">
          Is this land in court?
        </h1>

        {/* 70% Width 2px Solid Crisp Search Bar */}
        <form onSubmit={handleSubmit} className="w-full sm:w-[70vw] max-w-5xl">
          <div className="flex flex-col sm:flex-row items-stretch border-2 border-black bg-white shadow-none w-full">
            {/* Survey / Gata Input */}
            <input
              type="text"
              aria-label="Survey or gata number"
              value={surveyNo}
              onChange={(e) => setSurveyNo(e.target.value)}
              placeholder="Survey / Gata Number..."
              className="font-mono text-base px-6 py-4 flex-1 bg-white placeholder:text-ink-subtle text-black border-b-2 sm:border-b-0 sm:border-r-2 border-black rounded-none focus:bg-[#FFFDF9]"
            />

            {/* Village Input */}
            <input
              type="text"
              aria-label="Village"
              value={village}
              onChange={(e) => setVillage(e.target.value)}
              placeholder="Village..."
              className="font-mono text-base px-6 py-4 w-full sm:w-72 md:w-80 bg-white placeholder:text-ink-subtle text-black border-b-2 sm:border-b-0 sm:border-r-2 border-black rounded-none focus:bg-[#FFFDF9]"
            />

            {/* Sharp Pointy Arrow Button */}
            <button
              type="submit"
              title="Search litigation records"
              className="bg-black hover:bg-neutral-800 active:bg-neutral-950 text-white px-8 py-4 flex items-center justify-center transition-colors cursor-pointer rounded-none select-none group min-w-[76px]"
            >
              <svg
                viewBox="0 0 24 24"
                width="22"
                height="22"
                stroke="currentColor"
                strokeWidth="2.5"
                fill="none"
                strokeLinecap="square"
                strokeLinejoin="miter"
                className="transform group-hover:translate-x-1.5 transition-transform"
              >
                <line x1="4" y1="12" x2="20" y2="12" />
                <polyline points="14 6 20 12 14 18" />
              </svg>
            </button>
          </div>
        </form>

        {validationError ? (
          <p role="alert" className="mt-3 font-mono text-xs text-radar-red">{validationError}</p>
        ) : null}

        {/* Quick-select shortcuts, one real example per status band from the live index */}
        {quickPicks.length > 0 && (
          <div className="mt-8 flex flex-nowrap items-center gap-3 text-xs font-mono text-ink-muted w-full sm:w-[70vw] max-w-5xl overflow-x-auto whitespace-nowrap py-1">
            <span className="uppercase text-ink-subtle tracking-wider flex-shrink-0">Try an example:</span>
            {quickPicks.map((p) => (
              <button
                key={`${p.survey_no}-${p.village}`}
                type="button"
                onClick={() => handleQuickPick(p.survey_no, p.village)}
                className="border border-black/40 hover:border-black bg-paper-light hover:bg-white px-3 py-1.5 text-black transition-colors flex items-center gap-2 flex-shrink-0"
              >
                <span className={`w-2 h-2 rounded-full ${BAND_DOT[p.status]}`}></span>
                <span>{p.survey_no} ({p.village})</span>
                <span className="text-[10px] text-ink-muted uppercase font-semibold">[{BAND_LABEL[p.status]}]</span>
              </button>
            ))}
          </div>
        )}
      </div>

      <AppFooter
        onOpenDashboard={onOpenDashboard}
        onOpenWatchlist={onOpenWatchlist}
        active="search"
      />
    </div>
  );
};

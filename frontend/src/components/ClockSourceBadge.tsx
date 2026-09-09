import React from 'react';
import { ClockSource } from '../types/api';

interface ClockSourceBadgeProps {
  source: ClockSource;
  authority?: string | null;
}

// Two of five stage clocks are administrative targets we chose, not
// statute - every deadline must render this distinction, the same
// discipline already enforced for next_hearing_source='derived' on the
// litigation side.
export const ClockSourceBadge: React.FC<ClockSourceBadgeProps> = ({ source, authority }) => {
  const isStatute = source === 'statute';
  return (
    <span
      title={authority || (isStatute ? 'Statutory deadline' : 'Administrative target, not statute')}
      className={`inline-flex items-center gap-1 font-mono text-[10px] uppercase tracking-wider border px-1.5 py-0.5 ${
        isStatute ? 'border-black text-black' : 'border-ink-muted text-ink-muted'
      }`}
    >
      {isStatute ? 'Statute' : 'Admin target'}
    </span>
  );
};

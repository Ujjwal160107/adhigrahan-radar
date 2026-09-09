import React from 'react';

interface ProvenanceBadgeProps {
  source: string;
}

// Every acquisition row is either a Gazette of India record or a generated
// one, and the product must never let the two read alike (honesty rule 1:
// provenance on every row). Rendered wherever a project is named, the same
// discipline ClockSourceBadge applies to statutory vs administrative clocks.
export const ProvenanceBadge: React.FC<ProvenanceBadgeProps> = ({ source }) => {
  const isReal = source === 'real';
  return (
    <span
      title={isReal
        ? 'Real record: Gazette of India §3A / §3D notification, traceable by document id'
        : 'Generated for the demo corpus - not a record of any acquisition'}
      className={`inline-flex items-center flex-shrink-0 font-mono text-[10px] uppercase tracking-wider border px-1.5 py-0.5 ${
        isReal ? 'border-black bg-[#E6F4EA] text-black' : 'border-ink-muted text-ink-muted'
      }`}
    >
      {isReal ? 'Gazette' : 'Synthetic'}
    </span>
  );
};

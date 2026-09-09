import React from 'react';
import { RiskBand } from '../types/api';

interface RiskBadgeProps {
  band: RiskBand | null;
  probability?: number | null;
  size?: 'sm' | 'md';
}

const TONE: Record<RiskBand, string> = {
  HIGH: 'bg-[#FDE8E8] text-[#DC2626] border-[#DC2626]',
  MEDIUM: 'bg-[#FEF3C7] text-[#D97706] border-[#D97706]',
  LOW: 'bg-[#DCFCE7] text-[#16A34A] border-[#16A34A]',
};

// A different scale from the parcel litigation RED/AMBER/GREEN badge on
// purpose: this is a *predicted delay risk* for a project stage, not a
// litigation-status finding. Never share visual tokens with the parcel
// status badge, and never drop the disclaimer.
export const RiskBadge: React.FC<RiskBadgeProps> = ({ band, probability, size = 'md' }) => {
  if (!band) {
    return (
      <span className="font-mono text-xs uppercase text-ink-muted border-2 border-black/30 px-2 py-1">
        Not yet scored
      </span>
    );
  }
  const px = size === 'sm' ? 'px-2 py-0.5 text-[10px]' : 'px-3 py-1.5 text-xs';
  return (
    <span
      title="Predicted risk of missing a statutory deadline - not an administrative finding."
      className={`inline-flex items-center gap-1.5 font-mono font-bold uppercase tracking-wider border-2 ${px} ${TONE[band]}`}
    >
      {band}
      {probability != null && <span className="font-normal">{Math.round(probability * 100)}%</span>}
    </span>
  );
};

import { describe, expect, it } from 'vitest';
import { buildTimeline, partyCaption, pct, surveyMatchLabel } from './resultModel';
import { CaseDetail, LinkedCase, ParcelDetail } from '../types/api';

describe('pct', () => {
  it('renders a null/undefined confidence as an em dash, never 0%', () => {
    expect(pct(null)).toBe('—');
    expect(pct(undefined)).toBe('—');
  });

  it('rounds to the nearest whole percent', () => {
    expect(pct(0.9105)).toBe('91%');
    expect(pct(0)).toBe('0%');
    expect(pct(1)).toBe('100%');
  });
});

describe('surveyMatchLabel', () => {
  it('labels every known resolver outcome distinctly', () => {
    expect(surveyMatchLabel('exact')).toMatch(/exact/i);
    expect(surveyMatchLabel('subdivision')).toMatch(/sub-division/i);
    expect(surveyMatchLabel('normalized')).toMatch(/normalized/i);
    expect(surveyMatchLabel('none')).toMatch(/no identifier match/i);
  });

  it('falls back to the raw value for an unrecognised match type, never silently to "exact"', () => {
    expect(surveyMatchLabel('mystery')).toBe('mystery');
  });
});

describe('partyCaption', () => {
  it('formats petitioner v. respondent when both roles are present', () => {
    const caption = partyCaption([
      { role: 'petitioner', name_as_written: 'Shyam Dhar Dubey' },
      { role: 'respondent', name_as_written: 'State of UP' },
    ]);
    expect(caption).toBe('Shyam Dhar Dubey v. State of UP');
  });

  it('returns an empty string for no parties, never a fabricated caption', () => {
    expect(partyCaption(undefined)).toBe('');
    expect(partyCaption([])).toBe('');
  });
});

describe('buildTimeline', () => {
  const baseLink: LinkedCase = {
    case_id: 'C-1', case_no: 'WRIB/1/2025', court: 'Allahabad High Court',
    case_type: 'partition', case_status: 'active', confidence: 0.9, band: 'HIGH',
    link_status: 'RED', evidence: {}, filing_date: '2025-01-10', order_date: '2025-02-01',
    next_hearing: '2026-01-01', next_hearing_source: 'derived', raw_text_ref: null,
  };

  it('never fabricates a next-hearing card for a disposed case', () => {
    const disposed = { ...baseLink, case_status: 'disposed' };
    const timeline = buildTimeline(disposed, null, null);
    expect(timeline.some((c) => c.kind === 'hearing')).toBe(false);
  });

  it('includes a derived-hearing card only for an active case with a hearing date', () => {
    const timeline = buildTimeline(baseLink, null, null);
    const hearing = timeline.find((c) => c.kind === 'hearing');
    expect(hearing).toBeDefined();
    expect(hearing?.date).toBe('2026-01-01');
  });

  it('sorts cards chronologically', () => {
    const timeline = buildTimeline(baseLink, null, null);
    const dates = timeline.map((c) => c.date);
    expect(dates).toEqual([...dates].sort());
  });

  it('flags a sale event inside the litigation pendency window', () => {
    const parcel = {
      land_events: [
        { type: 'sale', date: '2025-06-01', note: 'Sale deed registered' },
      ],
    } as unknown as ParcelDetail;
    const timeline = buildTimeline(baseLink, parcel, null);
    const sale = timeline.find((c) => c.kind === 'sale');
    expect(sale?.insideSuit).toBe(true);
  });

  it('does not flag a sale event outside the pendency window', () => {
    const parcel = {
      land_events: [
        { type: 'sale', date: '2020-01-01', note: 'Sale deed registered' },
      ],
    } as unknown as ParcelDetail;
    const timeline = buildTimeline(baseLink, parcel, null);
    const sale = timeline.find((c) => c.kind === 'sale');
    expect(sale?.insideSuit).toBeFalsy();
  });

  it('falls back to caseDetail fields when no link is provided', () => {
    const caseDetail = {
      filing_date: '2024-03-01', order_date: null, next_hearing_date: null,
      next_hearing_source: null, court: 'District Court', case_no: 'X/1/2024',
      status: 'disposed',
    } as unknown as CaseDetail;
    const timeline = buildTimeline(undefined, null, caseDetail);
    expect(timeline.some((c) => c.date === '2024-03-01')).toBe(true);
  });
});

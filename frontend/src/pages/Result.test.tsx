import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Result } from './Result';
import { ParcelDetail, LitigationResponse } from '../types/api';

vi.mock('../api/client', () => ({
  api: { getCase: vi.fn(), subscribeWatchlist: vi.fn() },
}));

const baseParcel: ParcelDetail = {
  id: 'P-B01', survey_no: '1365-1', khasra_no: null, khata_no: '153',
  village: 'Madanpur Panyar', village_canon: 'madanpur paniyar', taluk: 'Sultanpur',
  district: 'Sultanpur', area: '1.0', geometry: null, land_events: [],
  owner: { name: 'Shyam Dhar Dubey', father_name: 'Neemar' },
  status: 'RED', confidence: 0.9105, note: 'High-confidence litigation connection found',
  closed_history: false, source_label: 'synthetic', projects: [],
};

const redLitigation: LitigationResponse = {
  parcel_id: 'P-B01', status: 'RED', confidence: 0.9105,
  note: 'High-confidence litigation connection found', closed_history: false,
  links: [{
    case_id: 'C-1', case_no: 'WRIB/784/2025', court: 'Allahabad High Court',
    case_type: 'succession_inheritance', case_status: 'active', confidence: 0.9105,
    band: 'HIGH', link_status: 'RED', reason: 'high confidence', evidence: {},
    filing_date: '2025-08-11', order_date: '2025-08-22', next_hearing: null,
    next_hearing_source: null, raw_text_ref: null,
  }],
};

describe('Result - honesty regressions', () => {
  it('shows the RED-specific legal notice on a RED result, never the GREEN wording', () => {
    render(
      <Result
        parcel={baseParcel} litigation={redLitigation} caseDetail={null}
        notFound={false} searchQuery={{ surveyNo: '1365/1', village: 'Madanpur Panyar' }}
        onBack={() => {}}
      />,
    );
    expect(screen.getByText(/not a legal adjudication/i)).toBeInTheDocument();
    expect(screen.queryByText(/GREEN means no matching active litigation/i)).not.toBeInTheDocument();
  });

  it('never claims a government land registry for a not-found parcel', () => {
    render(
      <Result
        parcel={null} litigation={null} caseDetail={null}
        notFound searchQuery={{ surveyNo: '99999999', village: '' }}
        onBack={() => {}}
      />,
    );
    expect(screen.getByText('No land record on file')).toBeInTheDocument();
    expect(screen.queryByText('State Revenue Land Registry')).not.toBeInTheDocument();
  });

  it('reports 0% confidence for a not-found parcel, never a fabricated 97%', () => {
    render(
      <Result
        parcel={null} litigation={null} caseDetail={null}
        notFound searchQuery={{ surveyNo: '99999999', village: '' }}
        onBack={() => {}}
      />,
    );
    expect(screen.getByText('0%')).toBeInTheDocument();
  });
});

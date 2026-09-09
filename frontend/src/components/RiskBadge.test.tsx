import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { RiskBadge } from './RiskBadge';

describe('RiskBadge', () => {
  it('renders "Not yet scored" for a null band, never a fabricated LOW', () => {
    render(<RiskBadge band={null} />);
    expect(screen.getByText('Not yet scored')).toBeInTheDocument();
    expect(screen.queryByText('LOW')).not.toBeInTheDocument();
  });

  it('renders the band and rounded percentage', () => {
    render(<RiskBadge band="HIGH" probability={0.5697} />);
    expect(screen.getByText('HIGH')).toBeInTheDocument();
    expect(screen.getByText('57%')).toBeInTheDocument();
  });

  it('always carries the mandatory disclaimer as a title attribute', () => {
    render(<RiskBadge band="MEDIUM" />);
    const badge = screen.getByText('MEDIUM');
    expect(badge.closest('span')).toHaveAttribute(
      'title',
      expect.stringContaining('not an administrative finding'),
    );
  });

  it('omits the probability when not supplied, never renders 0%', () => {
    render(<RiskBadge band="LOW" />);
    expect(screen.queryByText('0%')).not.toBeInTheDocument();
  });
});

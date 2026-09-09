import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ClockSourceBadge } from './ClockSourceBadge';

describe('ClockSourceBadge', () => {
  it('labels a statutory clock distinctly from an administrative target', () => {
    render(<ClockSourceBadge source="statute" />);
    expect(screen.getByText('Statute')).toBeInTheDocument();
  });

  it('never calls an administrative target a statute', () => {
    render(<ClockSourceBadge source="administrative_target" />);
    expect(screen.getByText('Admin target')).toBeInTheDocument();
    expect(screen.queryByText('Statute')).not.toBeInTheDocument();
  });

  it('surfaces the cited authority in the title when provided', () => {
    render(<ClockSourceBadge source="statute" authority="RFCTLARR 2013 s.19(7)" />);
    expect(screen.getByText('Statute')).toHaveAttribute('title', 'RFCTLARR 2013 s.19(7)');
  });
});

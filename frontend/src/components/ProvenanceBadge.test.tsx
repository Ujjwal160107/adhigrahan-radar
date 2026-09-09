import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ProvenanceBadge } from './ProvenanceBadge';

describe('ProvenanceBadge', () => {
  it('labels a gazette-sourced project as a real record', () => {
    render(<ProvenanceBadge source="real" />);
    expect(screen.getByText('Gazette')).toBeInTheDocument();
    expect(screen.getByText('Gazette')).toHaveAttribute('title', expect.stringMatching(/Gazette of India/));
  });

  it('never lets a generated row read as a record', () => {
    render(<ProvenanceBadge source="synthetic" />);
    expect(screen.getByText('Synthetic')).toBeInTheDocument();
    expect(screen.queryByText('Gazette')).not.toBeInTheDocument();
  });
});

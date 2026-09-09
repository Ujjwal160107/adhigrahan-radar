import React from 'react';
import { NavLink } from 'react-router-dom';

interface HeaderProps {
  activeDistrict?: string;
  isDemo?: boolean;
  role?: string;
}

const NAV_LINKS = [
  { to: '/', label: 'Risk dashboard', end: true },
  { to: '/projects', label: 'Projects' },
  { to: '/models', label: 'Model registry' },
  { to: '/lookup', label: 'Litigation lookup' },
];

export const Header: React.FC<HeaderProps> = ({ activeDistrict, isDemo = false, role }) => {
  return (
    <header className="w-full px-12 sm:px-20 pt-10 pb-4 z-20 max-w-7xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <NavLink to="/" className="cursor-pointer group flex items-baseline gap-3 select-none">
          <span className="font-serif text-2xl tracking-normal text-black font-normal">
            adhigrahan radar
          </span>
        </NavLink>

        <div className="flex items-center gap-4 text-xs font-mono">
          <div className="hidden sm:flex items-center gap-2 border border-black/40 px-2.5 py-1 bg-paper-light">
            <span className="inline-block w-2 h-2 rounded-full bg-radar-green"></span>
            <span className="text-ink-muted uppercase">District:</span>
            <span className="font-medium text-black">{activeDistrict || 'Loading…'}</span>
          </div>
          {role && (
            <span
              title="Demo-grade access control: this role is claimed via a header, not verified by a login."
              className="hidden sm:inline border border-black/40 px-2.5 py-1 bg-paper-light uppercase text-ink-muted"
            >
              Role: <span className="font-medium text-black">{role}</span>
            </span>
          )}
          {isDemo && (
            <span className="border border-radar-amber text-radar-amber bg-radar-amber/10 px-2 py-0.5 uppercase tracking-wider font-semibold">
              Tier-3 Offline
            </span>
          )}
        </div>
      </div>

      <nav className="flex flex-wrap gap-1 border-b-2 border-black">
        {NAV_LINKS.map((link) => (
          <NavLink
            key={link.to}
            to={link.to}
            end={link.end}
            className={({ isActive }) =>
              `font-mono text-xs uppercase tracking-wider px-4 py-2.5 border-2 border-b-0 -mb-0.5 ${
                isActive
                  ? 'border-black bg-black text-white font-bold'
                  : 'border-transparent text-ink-muted hover:text-black'
              }`
            }
          >
            {link.label}
          </NavLink>
        ))}
      </nav>
    </header>
  );
};

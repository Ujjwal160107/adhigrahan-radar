import React, { useEffect, useState } from 'react';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { Header } from './components/Header';
import { RiskDashboard } from './pages/RiskDashboard';
import { ProjectPortfolio } from './pages/ProjectPortfolio';
import { ProjectDetail } from './pages/ProjectDetail';
import { ModelHistory } from './pages/ModelHistory';
import { LookupApp } from './pages/LookupApp';
import { api, isDemoMode } from './api/client';

export const App: React.FC = () => {
  const [district, setDistrict] = useState<string | null>(null);
  const [role, setRole] = useState<string | undefined>(undefined);

  useEffect(() => {
    let cancelled = false;
    api.getOverview()
      .then((o) => { if (!cancelled) setDistrict(o.district); })
      .catch(() => { if (!cancelled) setDistrict(null); });
    api.getSession()
      .then((s) => { if (!cancelled) setRole(s.role); })
      .catch(() => { if (!cancelled) setRole(undefined); });
    return () => { cancelled = true; };
  }, []);

  return (
    <BrowserRouter>
      <div className="min-h-screen bg-grid-100 flex flex-col font-sans selection:bg-black selection:text-white">
        <Header activeDistrict={district || undefined} isDemo={isDemoMode()} role={role} />
        <main className="flex-1">
          <Routes>
            <Route path="/" element={<RiskDashboard />} />
            <Route path="/projects" element={<ProjectPortfolio />} />
            <Route path="/projects/:id" element={<ProjectDetail />} />
            <Route path="/models" element={<ModelHistory />} />
            <Route path="/lookup" element={<LookupApp />} />
            <Route path="/lookup/parcel/:parcelId" element={<LookupApp />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
};

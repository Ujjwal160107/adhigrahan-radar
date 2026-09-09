import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Search } from './Search';
import { Processing } from './Processing';
import { ParcelPicker } from './ParcelPicker';
import { Result } from './Result';
import { OfficerDashboard } from './OfficerDashboard';
import { Watchlist } from './Watchlist';
import { api } from '../api/client';
import { ParcelDetail, LitigationResponse, CaseDetail, SearchResultParcel } from '../types/api';

const NOT_FOUND_LITIGATION: LitigationResponse = {
  parcel_id: '',
  status: 'GREEN',
  confidence: 0,
  note: 'This survey number was not found in the court-linked parcel index.',
  closed_history: false,
  links: [],
};

type View = 'search' | 'processing' | 'pick' | 'result' | 'dashboard' | 'watchlist';

/**
 * The original citizen/officer litigation-lookup flow (search -> result,
 * officer heatmap, watchlist), demoted from the app's home screen to
 * /lookup/* - the risk engine is now the primary product surface, this is
 * the drill-down evidence layer it links into.
 */
export const LookupApp: React.FC = () => {
  const navigate = useNavigate();
  const { parcelId: directParcelId } = useParams<{ parcelId?: string }>();
  const [view, setView] = useState<View>('search');
  const [returnView, setReturnView] = useState<'search' | 'dashboard' | 'watchlist'>('search');
  const [searchTarget, setSearchTarget] = useState({ surveyNo: '', village: '' });
  const [searchedParcel, setSearchedParcel] = useState<ParcelDetail | null>(null);
  const [litigationData, setLitigationData] = useState<LitigationResponse | null>(null);
  const [caseDetail, setCaseDetail] = useState<CaseDetail | null>(null);
  const [candidates, setCandidates] = useState<SearchResultParcel[]>([]);
  const [notFound, setNotFound] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [searchReady, setSearchReady] = useState(false);
  const candidatesRef = useRef<SearchResultParcel[]>([]);

  const loadParcelBundle = useCallback(async (parcelId: string) => {
    const [parcel, litigation] = await Promise.all([
      api.getParcel(parcelId),
      api.getLitigation(parcelId),
    ]);
    setSearchedParcel(parcel);
    setLitigationData(litigation);
    const caseId = litigation.links[0]?.case_id;
    if (caseId) {
      try {
        setCaseDetail(await api.getCase(caseId));
      } catch {
        setCaseDetail(null);
      }
    } else {
      setCaseDetail(null);
    }
    return parcel;
  }, []);

  // A ProjectDetail parcel link (/lookup/parcel/:parcelId) jumps straight
  // to the Result view for that parcel.
  useEffect(() => {
    if (!directParcelId) return;
    let cancelled = false;
    (async () => {
      try {
        const parcel = await loadParcelBundle(directParcelId);
        if (cancelled) return;
        setSearchTarget({ surveyNo: parcel.survey_no, village: parcel.village });
        setNotFound(false);
        setSearchError(null);
        setView('result');
      } catch {
        if (!cancelled) setSearchError('Could not load that parcel.');
      }
    })();
    return () => { cancelled = true; };
  }, [directParcelId, loadParcelBundle]);

  const handleStartSearch = async (surveyNo: string, village: string) => {
    setSearchTarget({ surveyNo, village });
    setReturnView('search');
    setView('processing');
    setNotFound(false);
    setSearchError(null);
    setSearchReady(false);
    setCaseDetail(null);
    setSearchedParcel(null);
    setLitigationData(null);
    setCandidates([]);
    candidatesRef.current = [];

    try {
      const searchRes = await api.searchParcels(surveyNo, village);
      const hits = searchRes.parcels || [];
      setCandidates(hits);
      candidatesRef.current = hits;

      if (hits.length === 0) {
        setNotFound(true);
        setLitigationData(NOT_FOUND_LITIGATION);
      } else if (hits.length === 1) {
        await loadParcelBundle(hits[0].id);
      }
    } catch (err) {
      console.error('Search execution failed:', err);
      setCandidates([]);
      candidatesRef.current = [];
      setSearchError('The search could not be completed. Check your connection and try again.');
    } finally {
      setSearchReady(true);
    }
  };

  const handleProcessingComplete = useCallback(() => {
    setView(candidatesRef.current.length > 1 ? 'pick' : 'result');
  }, []);

  const handlePick = async (parcelId: string) => {
    await loadParcelBundle(parcelId);
    setNotFound(false);
    setView('result');
  };

  const handleOpenParcel = async (parcelId: string, from: 'dashboard' | 'watchlist') => {
    setReturnView(from);
    const parcel = await loadParcelBundle(parcelId);
    setSearchTarget({ surveyNo: parcel.survey_no, village: parcel.village });
    setNotFound(false);
    setView('result');
  };

  const handleGoHome = () => {
    setView('search');
    setReturnView('search');
    setNotFound(false);
    setSearchReady(false);
    setCaseDetail(null);
    setCandidates([]);
    candidatesRef.current = [];
    navigate('/lookup');
  };

  const handleResultBack = () => {
    if (directParcelId) {
      navigate(-1);
      return;
    }
    setView(returnView);
  };

  return (
    <>
      {view === 'search' && (
        <Search
          onSearch={handleStartSearch}
          onOpenDashboard={() => setView('dashboard')}
          onOpenWatchlist={() => setView('watchlist')}
        />
      )}

      {view === 'processing' && (
        <Processing
          surveyNo={searchTarget.surveyNo}
          village={searchTarget.village}
          ready={searchReady}
          onComplete={handleProcessingComplete}
        />
      )}

      {view === 'pick' && (
        <ParcelPicker
          query={searchTarget}
          parcels={candidates}
          onPick={handlePick}
          onBack={handleGoHome}
        />
      )}

      {view === 'result' && searchError && (
        <div className="w-full px-8 sm:px-16 md:px-20 pt-16 max-w-6xl mx-auto">
          <div className="border-2 border-radar-red bg-[#FDE8E8] p-8 font-mono text-sm text-black">
            <p className="font-bold mb-2">Search failed</p>
            <p>{searchError}</p>
            <button
              onClick={handleResultBack}
              className="mt-6 bg-black text-white px-6 py-3 font-bold hover:bg-neutral-800 cursor-pointer"
            >
              Back
            </button>
          </div>
        </div>
      )}
      {view === 'result' && !searchError && (
        <Result
          parcel={searchedParcel}
          litigation={litigationData}
          caseDetail={caseDetail}
          notFound={notFound}
          searchQuery={searchTarget}
          onBack={handleResultBack}
        />
      )}

      {view === 'dashboard' && (
        <OfficerDashboard
          onBack={handleGoHome}
          onOpenWatchlist={() => setView('watchlist')}
          onOpenParcel={(id) => handleOpenParcel(id, 'dashboard')}
        />
      )}

      {view === 'watchlist' && (
        <Watchlist
          onBack={handleGoHome}
          onOpenDashboard={() => setView('dashboard')}
          onOpenParcel={(id) => handleOpenParcel(id, 'watchlist')}
        />
      )}
    </>
  );
};

import {
  SearchResultParcel,
  ParcelDetail,
  LitigationResponse,
  CaseDetail,
  DashboardOverview,
  VillageDensity,
  WatchlistItem,
  ParcelMapResponse,
  ProjectListResponse,
  ProjectDetail,
  ProjectRiskResponse,
  ProjectParcelsResponse,
  InterventionsResponse,
  Intervention,
  DashboardRisk,
  ModelHistoryResponse,
  AuthSession,
} from '../types/api';

import {
  FLAGSHIP_RED_PARCEL,
  FLAGSHIP_GREEN_PARCEL,
  FLAGSHIP_AMBER_PARCEL,
  FALLBACK_OVERVIEW,
  FALLBACK_HEATMAP,
  FALLBACK_MAP,
  PARCEL_FALLBACKS,
  LITIGATION_FALLBACKS,
  CASE_FALLBACKS,
} from './fallbackData';

const BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

export const isDemoMode = (): boolean => {
  if (typeof window !== 'undefined') {
    return new URLSearchParams(window.location.search).get('demo') === '1';
  }
  return false;
};

async function fetchJson<T>(url: string, fallback?: T): Promise<T> {
  if (isDemoMode()) {
    if (fallback !== undefined) return fallback;
    throw new Error(`No demo payload for ${url}`);
  }
  try {
    const res = await fetch(`${BASE_URL}${url}`);
    if (res.ok) return await res.json();
    if (fallback !== undefined) {
      console.warn(`API returned ${res.status} for ${url}, using fallback.`);
      return fallback;
    }
    throw new Error(`API ${res.status} for ${url}`);
  } catch (err) {
    if (fallback !== undefined) {
      console.warn(`Fetch failed for ${url}, switching to fallback tier.`, err);
      return fallback;
    }
    throw err;
  }
}

function asSearchHit(p: ParcelDetail): SearchResultParcel {
  return {
    id: p.id,
    survey_no: p.survey_no,
    khasra_no: p.khasra_no,
    khata_no: p.khata_no,
    village: p.village,
    village_canon: p.village_canon,
    taluk: p.taluk,
    status: p.status,
    confidence: p.confidence,
  };
}

function keySurvey(value: string | null | undefined): string {
  return (value || '').trim().toLowerCase().replace(/-/g, '/').replace(/\s+/g, '');
}

function keyPlace(value: string | null | undefined): string {
  return (value || '').trim().toLowerCase();
}

function matchesQuery(hit: SearchResultParcel, surveyNo: string, village: string): boolean {
  const wantSurvey = keySurvey(surveyNo);
  const wantVillage = keyPlace(village);
  const surveyOk =
    !wantSurvey ||
    [hit.survey_no, hit.khasra_no, hit.khata_no].some((value) => {
      const have = keySurvey(value);
      return have && (have === wantSurvey || have.includes(wantSurvey) || wantSurvey.includes(have));
    });
  const villageOk =
    !wantVillage ||
    keyPlace(hit.village).includes(wantVillage) ||
    keyPlace(hit.village_canon).includes(wantVillage) ||
    wantVillage.includes(keyPlace(hit.village_canon));
  return surveyOk && villageOk;
}

export const api = {
  async searchParcels(surveyNo: string, village: string): Promise<{ parcels: SearchResultParcel[] }> {
    const cleanSurvey = surveyNo.trim();
    const cleanVillage = village.trim();
    const bundled = [FLAGSHIP_RED_PARCEL, FLAGSHIP_GREEN_PARCEL, FLAGSHIP_AMBER_PARCEL]
      .map(asSearchHit)
      .filter((hit) => matchesQuery(hit, cleanSurvey, cleanVillage));

    const query = new URLSearchParams();
    if (cleanSurvey) query.append('survey_no', cleanSurvey);
    if (cleanVillage) query.append('village', cleanVillage);

    return fetchJson<{ parcels: SearchResultParcel[] }>(
      `/parcels/search?${query.toString()}`,
      { parcels: bundled },
    );
  },

  async getParcel(id: string): Promise<ParcelDetail> {
    return fetchJson<ParcelDetail>(`/parcels/${id}`, PARCEL_FALLBACKS[id]);
  },

  async getLitigation(id: string): Promise<LitigationResponse> {
    return fetchJson<LitigationResponse>(`/parcels/${id}/litigation`, LITIGATION_FALLBACKS[id]);
  },

  async getCase(id: string): Promise<CaseDetail> {
    return fetchJson<CaseDetail>(`/cases/${id}`, CASE_FALLBACKS[id]);
  },

  async getOverview(): Promise<DashboardOverview> {
    return fetchJson<DashboardOverview>('/dashboard/overview', FALLBACK_OVERVIEW);
  },

  async getHeatmap(): Promise<{ villages: VillageDensity[] }> {
    return fetchJson<{ villages: VillageDensity[] }>('/dashboard/heatmap', FALLBACK_HEATMAP);
  },

  async getMap(): Promise<ParcelMapResponse> {
    return fetchJson<ParcelMapResponse>('/dashboard/map', FALLBACK_MAP);
  },

  async getWatchlist(): Promise<{ items: WatchlistItem[] }> {
    return fetchJson<{ items: WatchlistItem[] }>('/watchlist', {
      items: [
        {
          id: 1,
          parcel_id: 'P-B01',
          project_id: null,
          survey_no: '1365-1',
          village: 'Madanpur Panyar',
          project_name: null,
          subscribed_at: '2026-08-20',
          has_update: false,
        },
      ],
    });
  },

  async subscribeWatchlist(
    target: { parcelId: string } | { projectId: string },
  ): Promise<{ id: number; parcel_id: string | null; project_id: string | null; subscribed_at: string }> {
    const body = 'parcelId' in target
      ? { parcel_id: target.parcelId }
      : { project_id: target.projectId };
    const res = await fetch(`${BASE_URL}/watchlist`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const errBody = await res.json().catch(() => ({}));
      if (errBody?.error === 'already_subscribed') {
        throw new Error('Already on your watchlist.');
      }
      throw new Error(errBody?.error === 'unknown_parcel' || errBody?.error === 'unknown_project'
        ? 'That record is not in the index.'
        : `Could not subscribe (server returned ${res.status}).`);
    }
    return res.json();
  },

  async searchProjects(params: {
    district?: string; stage?: string; riskBand?: string; act?: string;
    status?: string; sort?: string; limit?: number; offset?: number;
  } = {}): Promise<ProjectListResponse> {
    const q = new URLSearchParams();
    if (params.district) q.append('district', params.district);
    if (params.stage) q.append('stage', params.stage);
    if (params.riskBand) q.append('risk_band', params.riskBand);
    if (params.act) q.append('act', params.act);
    if (params.status) q.append('status', params.status);
    if (params.sort) q.append('sort', params.sort);
    q.append('limit', String(params.limit ?? 50));
    q.append('offset', String(params.offset ?? 0));
    return fetchJson<ProjectListResponse>(`/projects?${q.toString()}`, { total: 0, projects: [] });
  },

  async getProject(id: string): Promise<ProjectDetail> {
    return fetchJson<ProjectDetail>(`/projects/${id}`);
  },

  async getProjectRisk(id: string): Promise<ProjectRiskResponse> {
    return fetchJson<ProjectRiskResponse>(`/projects/${id}/risk`, { project_id: id, stages: [] });
  },

  async getProjectParcels(id: string): Promise<ProjectParcelsResponse> {
    return fetchJson<ProjectParcelsResponse>(
      `/projects/${id}/parcels`, { project_id: id, parcels: [] });
  },

  async getInterventions(id: string): Promise<InterventionsResponse> {
    return fetchJson<InterventionsResponse>(
      `/projects/${id}/interventions`, { project_id: id, interventions: [] });
  },

  async createIntervention(
    projectId: string,
    body: { stage?: string; ruleId?: string; action: string; note?: string },
  ): Promise<Intervention> {
    const res = await fetch(`${BASE_URL}/projects/${projectId}/interventions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        stage: body.stage, rule_id: body.ruleId, action: body.action, note: body.note,
      }),
    });
    if (!res.ok) {
      const errBody = await res.json().catch(() => ({}));
      throw new Error(errBody?.error === 'forbidden'
        ? 'Your role cannot record interventions.'
        : `Could not record the intervention (server returned ${res.status}).`);
    }
    return res.json();
  },

  async getDashboardRisk(): Promise<DashboardRisk> {
    return fetchJson<DashboardRisk>('/dashboard/risk', {
      model_version: null, trained_at: null,
      bands: { HIGH: 0, MEDIUM: 0, LOW: 0 },
      deadlines: { d30: 0, d60: 0, d90: 0 },
      median_lead_time_days: null, districts: [], top_at_risk: [],
    });
  },

  async getModelHistory(): Promise<ModelHistoryResponse> {
    return fetchJson<ModelHistoryResponse>('/models/history', { runs: [] });
  },

  async getSession(): Promise<AuthSession> {
    return fetchJson<AuthSession>('/auth/session', {
      role: 'officer', can_write: true, known_roles: ['officer'], auth_mode: 'demo_role_header',
    });
  },
};

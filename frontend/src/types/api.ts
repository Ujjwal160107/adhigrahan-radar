export type StatusBand = 'RED' | 'AMBER' | 'GREEN';
export type ConfidenceBand = 'HIGH' | 'MEDIUM' | 'LOW';

export interface SearchResultParcel {
  id: string;
  survey_no: string;
  khasra_no: string | null;
  khata_no: string | null;
  village: string;
  village_canon: string;
  taluk: string | null;
  status: StatusBand | null;
  confidence: number | null;
}

export interface LandEvent {
  event_type?: string;
  type?: string;
  date: string;
  note?: string;
}

export interface ParcelDetail {
  id: string;
  survey_no: string;
  khasra_no: string | null;
  khata_no: string | null;
  village: string;
  village_canon: string;
  taluk: string | null;
  district: string;
  area: string | null;
  geometry: any | null;
  land_events: LandEvent[];
  owner: {
    name: string;
    father_name: string | null;
  } | null;
  status: StatusBand | null;
  confidence: number | null;
  note: string | null;
  closed_history: number | boolean;
  source_label: string;
  projects: { id: string; name: string; binding_confidence: number }[];
}

export interface EvidenceDetail {
  survey_match?: 'exact' | 'normalized' | 'subdivision' | 'none';
  village_match?: boolean;
  case_village?: string | null;
  name_similarity?: number;
  father_name_similarity?: number | null;
  weights_used?: {
    identifier?: number;
    name?: number;
    father_name?: number;
    village?: number;
    case_type?: number;
  };
  case_type_relevance?: 'high' | 'medium' | 'low' | number;
  [key: string]: any;
}

export interface LinkedCase {
  case_id: string;
  case_no: string;
  court: string;
  case_type: string;
  case_status: 'active' | 'disposed' | 'closed' | string;
  confidence: number;
  band: ConfidenceBand;
  link_status: StatusBand;
  reason?: string;
  evidence: EvidenceDetail;
  filing_date: string;
  order_date: string | null;
  next_hearing: string | null;
  next_hearing_source?: 'derived' | 'real' | null;
  raw_text_ref: string | null;
}

export interface LitigationResponse {
  parcel_id: string;
  status: StatusBand;
  confidence: number | null;
  note: string | null;
  closed_history: boolean;
  links: LinkedCase[];
}

export interface CaseParty {
  role: 'petitioner' | 'respondent' | string;
  name_as_written: string;
}

export interface CourtEvent {
  event_type: 'filed' | 'interim_order' | 'judgment' | 'next_hearing' | string;
  date: string;
  note: string | null;
}

export interface CaseDetail {
  id: string;
  case_no: string;
  court: string;
  case_type: string;
  filing_date: string;
  order_date: string | null;
  status: string;
  next_hearing_date: string | null;
  next_hearing_source?: 'derived' | 'real' | null;
  raw_text_ref: string | null;
  source_label: string;
  parties: CaseParty[];
  events: CourtEvent[];
  linked_parcels: {
    parcel_id: string;
    confidence_score: number;
    status: StatusBand;
  }[];
  affected_projects: { project_id: string; name: string; district: string }[];
}

export interface DashboardOverview {
  district: string | null;
  parcels: number;
  cases: number;
  status_counts: {
    RED: number;
    AMBER: number;
    GREEN: number;
  };
  active_cases: number;
  high_confidence_links: number;
  possible_matches: number;
}

export interface VillageDensity {
  village: string;
  village_canon: string;
  parcels: number;
  RED: number;
  AMBER: number;
  GREEN: number;
  density: number;
}

export interface WatchlistItem {
  id: number;
  parcel_id: string | null;
  project_id: string | null;
  survey_no: string | null;
  village: string | null;
  project_name: string | null;
  subscribed_at: string;
  has_update: boolean;
}

export interface MapParcelProperties {
  id: string;
  survey_no: string;
  village: string;
  village_canon: string;
  status: StatusBand;
  confidence: number | null;
}

export interface ParcelMapFeature {
  type: 'Feature';
  geometry: {
    type: string;
    coordinates: number[][][] | number[][][][];
  };
  properties: MapParcelProperties;
}

export interface ParcelMapResponse {
  type: 'FeatureCollection';
  features: ParcelMapFeature[];
}

export type RiskBand = 'LOW' | 'MEDIUM' | 'HIGH';
export type ClockSource = 'statute' | 'administrative_target';
export type ActType = 'NH_1956' | 'RFCTLARR_2013';
export type ProjectStatus = 'open' | 'completed' | 'lapsed';

export interface ProjectListItem {
  id: string;
  name: string;
  district: string;
  act: ActType;
  current_stage: string | null;
  status: ProjectStatus;
  area_hectares: number;
  affected_families: number;
  risk_band: RiskBand | null;
  delay_probability: number | null;
  days_remaining: number | null;
  scored_stage: string | null;
}

export interface ProjectListResponse {
  total: number;
  projects: ProjectListItem[];
}

export interface ProjectStageRow {
  id: string;
  project_id: string;
  stage: string;
  stage_order: number;
  statutory_days: number;
  clock_source: ClockSource;
  clock_authority: string | null;
  started_on: string | null;
  completed_on: string | null;
  deadline_on: string | null;
  overdue_days: number | null;
  is_delayed: number | null;
  source_label: string;
}

export interface ParcelSummary {
  total: number;
  RED: number;
  AMBER: number;
  GREEN: number;
}

export interface ProjectDetail {
  id: string;
  name: string;
  project_type: string;
  executing_agency: string;
  act: ActType;
  state: string;
  district: string;
  block: string | null;
  nh_no: string | null;
  gazette_ref: string | null;
  area_hectares: number;
  affected_families: number;
  budget_estimate_inr: number;
  current_stage: string | null;
  stage_entered_on: string | null;
  status: ProjectStatus;
  source_label: string;
  stages: ProjectStageRow[];
  parcel_summary: ParcelSummary;
}

export interface RiskDriver {
  feature: string;
  label: string;
  shap_value: number;
  direction: 'increases_risk' | 'decreases_risk';
  value: number | null;
}

export interface RiskRecommendation {
  rule_id: string;
  driver: string;
  action: string;
}

export interface ProjectRiskStage {
  stage: string;
  delay_probability: number;
  risk_band: RiskBand;
  predicted_overrun_days: number | null;
  lead_time_days: number;
  model_version: string;
  scored_at: string;
  drivers: RiskDriver[];
  recommendations: RiskRecommendation[];
}

export interface ProjectRiskResponse {
  project_id: string;
  stages: ProjectRiskStage[];
}

export interface ProjectParcelRow {
  parcel_id: string;
  survey_no: string;
  village: string;
  status: StatusBand | null;
  confidence: number | null;
  binding_confidence: number;
}

export interface ProjectParcelsResponse {
  project_id: string;
  parcels: ProjectParcelRow[];
}

export interface Intervention {
  id: number;
  stage: string | null;
  rule_id: string | null;
  action: string;
  note: string | null;
  recorded_by: string;
  recorded_at: string;
  risk_band_at_time: RiskBand | null;
  model_version_at_time: string | null;
}

export interface InterventionsResponse {
  project_id: string;
  interventions: Intervention[];
}

export interface DashboardRiskDistrict {
  district: string;
  projects: number;
  high: number;
  medium: number;
  low: number;
}

export interface DashboardRiskTopItem {
  project_id: string;
  name: string;
  district: string;
  stage: string;
  risk_band: RiskBand;
  delay_probability: number;
  deadline_on: string;
}

export interface DashboardRisk {
  model_version: string | null;
  trained_at: string | null;
  bands: { HIGH: number; MEDIUM: number; LOW: number };
  deadlines: { d30: number; d60: number; d90: number };
  median_lead_time_days: number | null;
  districts: DashboardRiskDistrict[];
  top_at_risk: DashboardRiskTopItem[];
}

export interface ModelRunMetrics {
  roc_auc: number | null;
  pr_auc: number | null;
  brier: number | null;
  train_brier: number;
  train_positive_rate: number | null;
}

export interface ModelRunEntry {
  model_version: string;
  stage: string;
  algo: string;
  shipped: number;
  n_train: number;
  n_test: number;
  n_test_real: number;
  n_test_synthetic: number;
  cutoff_date: string;
  metrics: Record<string, ModelRunMetrics>;
  thresholds: { t_high?: number; t_med?: number; high?: string };
  notes: string;
  trained_at: string;
}

export interface ModelHistoryResponse {
  runs: ModelRunEntry[];
}

export interface AuthSession {
  role: string;
  can_write: boolean;
  known_roles: string[];
  auth_mode: string;
}

-- Adhigrahan Radar data model.
--
-- Single source of truth for the schema. Both the offline pipeline
-- (pipeline/s6_load_db.py, pipeline/s14_load_risk_db.py) and the backend
-- (backend/db.py:init_schema, used by tests and backend/seed_stub.py) load
-- this exact file, so the two can never drift out of sync again (see
-- docs/specs/2026-08-20-integration-handoff.md for the outage that
-- motivated this rule).
--
-- Every CREATE TABLE is IF NOT EXISTS: safe to run against an existing DB.
-- The pipeline stages that fully rebuild a table's contents each run
-- (Parcel, Person, CourtCase, ... and the risk-engine tables) DELETE their
-- own rows before reinserting; they never DROP a table, so a partial
-- rebuild can never leave the schema itself missing.
--
-- The one thing IF NOT EXISTS cannot do is ADD a column to a table that
-- already exists. Adding one here therefore makes an older data/output/
-- vivaad.db stale, and the load would otherwise fail with a bare
-- "no column named X". s14 preflights this and says so; the fix is always
-- `make clean && make build`, because data/output is regenerable by
-- definition and never the source of truth.

-- ===== Linkage engine (s0-s7) - unchanged contract =====

CREATE TABLE IF NOT EXISTS Parcel (
  id TEXT PRIMARY KEY, survey_no TEXT, khasra_no TEXT, khata_no TEXT,
  village TEXT, village_canon TEXT, taluk TEXT, district TEXT, area TEXT,
  geometry TEXT, land_events TEXT, owner_ref TEXT,
  status TEXT, confidence REAL, note TEXT, closed_history INTEGER,
  source_label TEXT);

CREATE TABLE IF NOT EXISTS Person (
  id TEXT PRIMARY KEY, name TEXT, name_normalized TEXT, father_name TEXT,
  address TEXT, source_label TEXT);

CREATE TABLE IF NOT EXISTS CourtCase (
  id TEXT PRIMARY KEY, case_no TEXT, court TEXT, case_type TEXT,
  filing_date TEXT, order_date TEXT, status TEXT, next_hearing_date TEXT,
  raw_text_ref TEXT, source_label TEXT,
  -- 'derived' | 'real' | NULL. No case in the corpus carries a genuine next
  -- hearing date, so the pipeline derives one for active cases and labels
  -- it. Mirrors pipeline/s6_load_db.py.
  next_hearing_source TEXT);

CREATE TABLE IF NOT EXISTS CaseParty (
  case_id TEXT, person_id TEXT, role TEXT, name_as_written TEXT);

CREATE TABLE IF NOT EXISTS CourtEvent (
  id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT, event_type TEXT,
  date TEXT, note TEXT);

CREATE TABLE IF NOT EXISTS ParcelCaseLink (
  id INTEGER PRIMARY KEY AUTOINCREMENT, parcel_id TEXT, case_id TEXT,
  confidence_score REAL, confidence_band TEXT, identifier_match TEXT,
  evidence TEXT, status TEXT, reason TEXT, created_at TEXT);

CREATE TABLE IF NOT EXISTS SourceRecord (
  id INTEGER PRIMARY KEY AUTOINCREMENT, source_type TEXT, origin TEXT,
  ingested_at TEXT, raw_ref TEXT);

-- ===== Risk engine (s8-s15) =====

-- One row per acquisition project.
CREATE TABLE IF NOT EXISTS AcquisitionProject (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  project_type TEXT,
  executing_agency TEXT,
  act TEXT NOT NULL,                 -- 'NH_1956' | 'RFCTLARR_2013'
  state TEXT NOT NULL,
  district TEXT NOT NULL,
  block TEXT,
  nh_no TEXT,                        -- only set when act='NH_1956'
  gazette_ref TEXT,
  area_hectares REAL,
  affected_families INTEGER,
  budget_estimate_inr REAL,
  current_stage TEXT,                -- denormalised for list queries
  stage_entered_on TEXT,
  status TEXT NOT NULL,              -- 'open' | 'completed' | 'lapsed'
  source_label TEXT NOT NULL);

-- One row per (project, stage). Right-censored: is_delayed is NULL, never
-- 0, while completed_on is NULL (the stage has not finished).
CREATE TABLE IF NOT EXISTS ProjectStage (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES AcquisitionProject(id),
  stage TEXT NOT NULL,
  stage_order INTEGER NOT NULL,
  statutory_days INTEGER NOT NULL,
  clock_source TEXT NOT NULL,        -- 'statute' | 'administrative_target'
  clock_authority TEXT,              -- e.g. 'RFCTLARR 2013 s.19(7)'
  started_on TEXT,
  completed_on TEXT,
  deadline_on TEXT,                  -- started_on + statutory_days
  overdue_days INTEGER,
  is_delayed INTEGER,                -- 1 | 0 | NULL (open/censored)
  source_label TEXT NOT NULL,
  UNIQUE(project_id, stage));

-- One row per (project, parcel) the project's land bank actually touches.
CREATE TABLE IF NOT EXISTS ProjectParcel (
  project_id TEXT NOT NULL REFERENCES AcquisitionProject(id),
  parcel_id TEXT NOT NULL REFERENCES Parcel(id),
  village_canon TEXT,
  binding_confidence REAL NOT NULL,
  binding_evidence TEXT NOT NULL,    -- JSON, same shape as ParcelCaseLink.evidence
  source_label TEXT NOT NULL,
  PRIMARY KEY (project_id, parcel_id));

-- One row per (project, stage) that has been scored. Precomputed at build
-- time by s13 - the API only ever SELECTs this table, never scores live.
CREATE TABLE IF NOT EXISTS ProjectRisk (
  project_id TEXT NOT NULL REFERENCES AcquisitionProject(id),
  stage TEXT NOT NULL,
  delay_probability REAL NOT NULL,
  risk_band TEXT NOT NULL,           -- 'LOW' | 'MEDIUM' | 'HIGH'
  predicted_overrun_days INTEGER,    -- empirical median, not a second model
  lead_time_days INTEGER,            -- deadline_on - scored_at
  model_version TEXT NOT NULL,
  scored_at TEXT NOT NULL,
  drivers TEXT NOT NULL,             -- JSON [{feature,shap,direction,value,label}]
  recommendations TEXT NOT NULL,     -- JSON [{rule_id,driver,action}]
  source_label TEXT NOT NULL DEFAULT 'model_generated',
  PRIMARY KEY (project_id, stage));

-- One row per (model_version, stage) training run. A model registry, so
-- "did this get better" is answerable instead of asserted.
CREATE TABLE IF NOT EXISTS ModelRun (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  model_version TEXT NOT NULL,
  stage TEXT NOT NULL,
  trained_at TEXT NOT NULL,
  algo TEXT NOT NULL,                -- 'hgb_calibrated' | 'logistic_regression' | 'base_rate'
  shipped INTEGER NOT NULL DEFAULT 0,-- 1 for the row actually serving this stage
  n_train INTEGER, n_test INTEGER,
  n_test_real INTEGER, n_test_synthetic INTEGER,
  cutoff_date TEXT,
  -- 'isotonic' | 'sigmoid'. s12 picks this from n_train against its own
  -- ISOTONIC_MIN_ROWS. It used to stop at model_runs.json, so the model
  -- history screen re-derived it in the browser from a hardcoded 200 -
  -- the calibration rule living in a React component, one edit away from
  -- disagreeing with the model it describes.
  calibration TEXT,
  metrics TEXT,                      -- JSON: roc_auc, pr_auc, brier, precision_at_t_high, ...
  feature_list TEXT,                 -- JSON, ordered
  thresholds TEXT,                   -- JSON {t_high, t_med} or {"high": "suppressed"}
  notes TEXT,
  UNIQUE(model_version, stage));

-- Officer-recorded follow-up action against a project/stage. The only
-- write path in the risk layer besides Watchlist.
CREATE TABLE IF NOT EXISTS Intervention (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id TEXT NOT NULL REFERENCES AcquisitionProject(id),
  stage TEXT,
  rule_id TEXT,                      -- the recommendation acted on, if any
  action TEXT NOT NULL,
  note TEXT,
  recorded_by TEXT NOT NULL,
  recorded_at TEXT NOT NULL,
  risk_band_at_time TEXT,            -- snapshot so "did risk change" is answerable
  model_version_at_time TEXT);

-- ===== Serving layer (backend only - never written by the pipeline) =====

CREATE TABLE IF NOT EXISTS Watchlist (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_ref TEXT, parcel_id TEXT,
  project_id TEXT REFERENCES AcquisitionProject(id),
  subscribed_at TEXT, last_notified_at TEXT, has_update INTEGER DEFAULT 0,
  CHECK (parcel_id IS NOT NULL OR project_id IS NOT NULL));

-- Append-only. Written by backend/auth.py middleware on every request when
-- role-based access is enabled; never cleared or rewritten by the pipeline.
CREATE TABLE IF NOT EXISTS AuditLog (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL, role TEXT, method TEXT, path TEXT, status INTEGER);

CREATE INDEX IF NOT EXISTS ix_parcel_lookup ON Parcel(survey_no, village_canon);
CREATE INDEX IF NOT EXISTS ix_parcel_village ON Parcel(village_canon);
CREATE INDEX IF NOT EXISTS ix_link_parcel ON ParcelCaseLink(parcel_id);
CREATE INDEX IF NOT EXISTS ix_link_case ON ParcelCaseLink(case_id);
CREATE INDEX IF NOT EXISTS ix_event_case ON CourtEvent(case_id);
CREATE INDEX IF NOT EXISTS ix_project_district ON AcquisitionProject(district, status);
CREATE INDEX IF NOT EXISTS ix_stage_project ON ProjectStage(project_id);
CREATE INDEX IF NOT EXISTS ix_stage_deadline ON ProjectStage(deadline_on) WHERE completed_on IS NULL;
CREATE INDEX IF NOT EXISTS ix_pp_project ON ProjectParcel(project_id);
CREATE INDEX IF NOT EXISTS ix_pp_parcel ON ProjectParcel(parcel_id);
CREATE INDEX IF NOT EXISTS ix_risk_band ON ProjectRisk(risk_band, delay_probability DESC);
CREATE INDEX IF NOT EXISTS ix_intervention_project ON Intervention(project_id);

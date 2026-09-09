import React, { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api } from '../api/client';
import {
  Intervention,
  ProjectDetail as ProjectDetailType,
  ProjectParcelRow,
  ProjectRiskStage,
} from '../types/api';
import { RiskBadge } from '../components/RiskBadge';
import { ClockSourceBadge } from '../components/ClockSourceBadge';
import { ProvenanceBadge } from '../components/ProvenanceBadge';

const STAGE_LABELS: Record<string, string> = {
  notification_3a_11: 'Notification (3A / S.11)',
  declaration_3d_19: 'Declaration (3D / S.19)',
  award_3g_23: 'Award (3G / S.23)',
  compensation_disbursed: 'Compensation disbursed',
  possession: 'Possession',
};

export const ProjectDetail: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [project, setProject] = useState<ProjectDetailType | null>(null);
  const [riskStages, setRiskStages] = useState<ProjectRiskStage[]>([]);
  const [parcels, setParcels] = useState<ProjectParcelRow[]>([]);
  const [interventions, setInterventions] = useState<Intervention[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [watchlisted, setWatchlisted] = useState(false);
  const [watchError, setWatchError] = useState<string | null>(null);

  const [actionText, setActionText] = useState('');
  const [actionStage, setActionStage] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const load = React.useCallback(() => {
    if (!id) return;
    setLoading(true);
    Promise.all([
      api.getProject(id),
      api.getProjectRisk(id),
      api.getProjectParcels(id),
      api.getInterventions(id),
    ])
      .then(([p, r, pc, iv]) => {
        setProject(p);
        setRiskStages(r.stages);
        setParcels(pc.parcels);
        setInterventions(iv.interventions);
        setError(null);
      })
      .catch(() => setError('Could not load this project.'))
      .finally(() => setLoading(false));
  }, [id]);

  useEffect(() => { load(); }, [load]);

  if (loading) {
    return <div className="p-16 font-mono text-sm text-ink-muted">Loading project…</div>;
  }
  if (error || !project) {
    return (
      <div className="w-full px-8 sm:px-16 md:px-20 pt-16 max-w-6xl mx-auto">
        <div className="border-2 border-radar-red bg-[#FDE8E8] p-8 font-mono text-sm text-black">
          {error || 'Project not found.'}
        </div>
      </div>
    );
  }

  const openStageRisk = riskStages.find((r) => r.stage === project.current_stage);
  const topDriver = openStageRisk?.drivers.find((d) => d.direction === 'increases_risk');

  const handleWatch = async () => {
    setWatchError(null);
    try {
      await api.subscribeWatchlist({ projectId: project.id });
      setWatchlisted(true);
    } catch (err) {
      setWatchError(err instanceof Error ? err.message : 'Could not subscribe.');
    }
  };

  const handleSubmitIntervention = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!actionText.trim()) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      await api.createIntervention(project.id, {
        stage: actionStage || undefined, action: actionText.trim(),
      });
      setActionText('');
      load();
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : 'Could not record the intervention.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="w-full px-8 sm:px-16 md:px-20 pb-24 max-w-6xl mx-auto">
      <div className="flex items-center gap-6 pt-10 pb-8">
        <button
          onClick={() => navigate('/projects')}
          title="Back to portfolio"
          className="bg-black hover:bg-neutral-800 text-white w-14 h-14 flex items-center justify-center transition-colors cursor-pointer flex-shrink-0"
        >
          <svg viewBox="0 0 24 24" width="26" height="26" stroke="currentColor" strokeWidth="2.5" fill="none" strokeLinecap="square" strokeLinejoin="miter">
            <line x1="19" y1="12" x2="5" y2="12" />
            <polyline points="12 19 5 12 12 5" />
          </svg>
        </button>
        <div>
          <h1 className="font-serif italic font-bold text-2xl sm:text-3xl md:text-4xl text-black tracking-tight">
            {project.name}
          </h1>
          <p className="font-mono text-xs text-ink-muted mt-1 flex flex-wrap items-center gap-2">
            <ProvenanceBadge source={project.source_label} />
            <span>
              {project.district} · {project.executing_agency ?? 'Executing agency not stated in the gazette'} · {project.act === 'NH_1956' ? 'NH Act 1956' : 'RFCTLARR 2013'}
            </span>
          </p>
        </div>
      </div>

      {/* Risk summary + top driver, above the fold */}
      <div className="border-2 border-black bg-white mb-8">
        <div className="grid grid-cols-1 md:grid-cols-12 border-b-2 border-black">
          <div className="md:col-span-4 p-6 border-b-2 md:border-b-0 md:border-r-2 border-black">
            <div className="text-[10px] uppercase tracking-wider text-ink-muted mb-2">
              Current stage risk
            </div>
            <RiskBadge band={openStageRisk?.risk_band ?? null} probability={openStageRisk?.delay_probability} />
            {openStageRisk && (
              <div className="font-mono text-xs text-ink-muted mt-3 space-y-1">
                <div>Lead time: {openStageRisk.lead_time_days < 0
                  ? `${Math.abs(openStageRisk.lead_time_days)} days overdue`
                  : `${openStageRisk.lead_time_days} days remaining`}</div>
                {openStageRisk.predicted_overrun_days != null && (
                  <div>Typically {openStageRisk.predicted_overrun_days} days late when this happens</div>
                )}
              </div>
            )}
          </div>
          <div className="md:col-span-8 p-6">
            <h3 className="font-mono text-xs font-bold uppercase tracking-wider text-black mb-3">
              Why is this stage at risk?
            </h3>
            {!openStageRisk ? (
              <p className="font-mono text-sm text-ink-muted">
                {project.source_label === 'real'
                  ? 'Nothing left to score from the public record: the §3D declaration is published, and the stages after it (award, compensation, possession) are never gazetted.'
                  : `No open stage to score - the project is ${project.status}.`}
              </p>
            ) : (
              <div className="space-y-2">
                {openStageRisk.drivers.slice(0, 5).map((d) => (
                  <div key={d.feature} className="flex items-center justify-between gap-3 font-mono text-xs">
                    <span className="text-black">
                      {d.label}
                      {d.outside_training_range ? (
                        <span
                          title="This value lies outside anything the model saw in training; its contribution is an extrapolation, not a learned effect."
                          className="ml-2 border border-radar-amber text-radar-amber text-[10px] uppercase tracking-wider px-1 py-0.5"
                        >
                          beyond training range
                        </span>
                      ) : null}
                    </span>
                    <span className={d.direction === 'increases_risk' ? 'text-radar-red font-bold' : 'text-radar-green'}>
                      {d.direction === 'increases_risk' ? '↑' : '↓'} {Math.abs(d.shap_value).toFixed(3)}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
        {openStageRisk && openStageRisk.recommendations.length > 0 && (
          <div className="p-6 bg-paper-light">
            <h3 className="font-mono text-xs font-bold uppercase tracking-wider text-black mb-3">
              Recommended actions
            </h3>
            <div className="space-y-3">
              {openStageRisk.recommendations.map((rec) => (
                <div key={rec.rule_id} className="border-2 border-black bg-white p-3 font-mono text-xs">
                  <div className="font-bold text-[10px] uppercase text-ink-muted mb-1">{rec.rule_id}</div>
                  {rec.action}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Stage timeline */}
      <div className="border-2 border-black bg-white mb-8">
        <div className="p-6 border-b-2 border-black">
          <h2 className="font-mono text-sm font-bold uppercase tracking-wider">Acquisition timeline</h2>
        </div>
        <div className="p-6 grid grid-cols-1 sm:grid-cols-5 gap-4">
          {project.stages.map((s) => {
            const risk = riskStages.find((r) => r.stage === s.stage);
            const isOpen = !s.completed_on;
            const overdue = isOpen && s.overdue_days && s.overdue_days > 0;
            return (
              <div
                key={s.stage}
                className={`border-2 p-3 font-mono text-xs ${
                  overdue ? 'border-radar-red bg-[#FDE8E8]' : isOpen ? 'border-black bg-white' : 'border-black/30 bg-paper-light'
                }`}
              >
                <div className="font-bold mb-1">{STAGE_LABELS[s.stage] || s.stage}</div>
                <ClockSourceBadge source={s.clock_source} authority={s.clock_authority} />
                <div className="mt-2 text-ink-muted space-y-0.5">
                  <div>Started {s.started_on || '—'}</div>
                  <div>{s.completed_on ? `Completed ${s.completed_on}` : `Deadline ${s.deadline_on}`}</div>
                  {overdue && <div className="text-radar-red font-bold">{s.overdue_days}d overdue</div>}
                </div>
                {isOpen && risk && (
                  <div className="mt-2">
                    <RiskBadge band={risk.risk_band} probability={risk.delay_probability} size="sm" />
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* Affected parcels */}
      <div className="border-2 border-black bg-white mb-8">
        <div className="p-6 border-b-2 border-black flex items-baseline justify-between">
          <h2 className="font-mono text-sm font-bold uppercase tracking-wider">
            Affected parcels
          </h2>
          <span className="font-mono text-xs text-ink-muted">
            {project.parcel_summary.total} total ·{' '}
            <span className="text-radar-red">{project.parcel_summary.RED} RED</span> ·{' '}
            <span className="text-radar-amber">{project.parcel_summary.AMBER} AMBER</span>
          </span>
        </div>
        {parcels.length === 0 ? (
          <div className="p-8 font-mono text-sm text-ink-muted">
            No parcel-level land records available for this district in the current corpus.
          </div>
        ) : (
          parcels.map((p, idx) => (
            <Link
              key={p.parcel_id}
              to={`/lookup/parcel/${p.parcel_id}`}
              className={`flex items-center justify-between p-4 font-mono text-xs sm:text-sm hover:bg-paper-light ${
                idx < parcels.length - 1 ? 'border-b border-black/20' : ''
              }`}
            >
              <div className="flex items-center gap-3">
                <span className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${
                  p.status === 'RED' ? 'bg-radar-red' : p.status === 'AMBER' ? 'bg-radar-amber' : 'bg-radar-green'
                }`} />
                <span className="font-bold">Survey {p.survey_no}</span>
                <span className="text-ink-muted">{p.village}</span>
              </div>
              <span className="uppercase font-bold">{p.status || 'GREEN'}</span>
            </Link>
          ))
        )}
      </div>

      {/* Interventions */}
      <div className="border-2 border-black bg-white">
        <div className="p-6 border-b-2 border-black flex items-baseline justify-between">
          <h2 className="font-mono text-sm font-bold uppercase tracking-wider">
            Interventions
          </h2>
          {watchError ? <span className="font-mono text-xs text-radar-red">{watchError}</span> : null}
          <button
            onClick={handleWatch}
            disabled={watchlisted}
            className={`font-mono text-xs px-4 py-2 border-2 border-black font-bold cursor-pointer ${
              watchlisted ? 'bg-radar-green text-white border-radar-green' : 'bg-white hover:bg-black hover:text-white'
            }`}
          >
            {watchlisted ? '✓ Monitoring' : '+ Monitor this project'}
          </button>
        </div>
        <form onSubmit={handleSubmitIntervention} className="p-6 border-b-2 border-black space-y-3">
          <div className="flex flex-wrap gap-3">
            <select
              value={actionStage}
              onChange={(e) => setActionStage(e.target.value)}
              className="border-2 border-black px-2 py-2 font-mono text-xs bg-white"
            >
              <option value="">No specific stage</option>
              {project.stages.map((s) => (
                <option key={s.stage} value={s.stage}>{STAGE_LABELS[s.stage] || s.stage}</option>
              ))}
            </select>
          </div>
          <textarea
            value={actionText}
            onChange={(e) => setActionText(e.target.value)}
            placeholder="Record the action taken (e.g. escalated to district legal cell)…"
            rows={2}
            className="w-full border-2 border-black px-3 py-2 font-mono text-xs"
          />
          {submitError ? <p className="font-mono text-xs text-radar-red">{submitError}</p> : null}
          <button
            type="submit"
            disabled={submitting || !actionText.trim()}
            className="bg-black text-white px-6 py-2.5 font-mono text-xs font-bold hover:bg-neutral-800 disabled:opacity-40 cursor-pointer disabled:cursor-not-allowed"
          >
            {submitting ? 'Recording…' : 'Record intervention'}
          </button>
        </form>
        {interventions.length === 0 ? (
          <div className="p-8 font-mono text-sm text-ink-muted">No interventions recorded yet.</div>
        ) : (
          interventions.map((iv, idx) => (
            <div
              key={iv.id}
              className={`p-4 font-mono text-xs sm:text-sm ${
                idx < interventions.length - 1 ? 'border-b border-black/20' : ''
              }`}
            >
              <div className="flex items-baseline justify-between gap-3">
                <span className="font-bold">{iv.action}</span>
                <span className="text-ink-muted flex-shrink-0">{iv.recorded_at.slice(0, 10)}</span>
              </div>
              <div className="text-ink-muted mt-1">
                By {iv.recorded_by}
                {iv.stage ? ` · ${STAGE_LABELS[iv.stage] || iv.stage}` : ''}
                {iv.risk_band_at_time ? ` · risk was ${iv.risk_band_at_time} at the time` : ''}
              </div>
            </div>
          ))
        )}
      </div>
      {topDriver && (
        <p className="font-mono text-xs text-ink-muted mt-6">
          Predicted risk of missing a statutory deadline, not an administrative finding.
        </p>
      )}
    </div>
  );
};

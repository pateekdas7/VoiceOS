'use strict';

const PENDING_CALLS_PREFIX = 'voiceos:pending_calls:';

function pendingCallsKey(tenantId) {
  if (typeof tenantId !== 'string' || tenantId.trim() === '') {
    throw new TypeError('tenant_id is required for a dial job queue');
  }
  return `${PENDING_CALLS_PREFIX}${tenantId}`;
}

function buildPendingCallJob(lead) {
  if (!lead || !lead.lead_id || !lead.campaign_id || !lead.tenant_id || !lead.phone) {
    throw new TypeError('lead_id, campaign_id, tenant_id and phone are required for a dial job');
  }
  return {
    lead_id: lead.lead_id,
    campaign_id: lead.campaign_id,
    pipeline_id: lead.pipeline_id || null,
    tenant_id: lead.tenant_id,
    phone: lead.phone,
    name: lead.name || null,
    language: lead.language || null,
    score: lead.score ?? null,
    queued_at: new Date().toISOString(),
  };
}

function parsePendingCallJob(serialized, queueKey) {
  let job;
  try {
    job = JSON.parse(serialized);
  } catch (error) {
    throw new Error('invalid_dial_job_json', { cause: error });
  }
  if (!job || typeof job !== 'object' || Array.isArray(job)) {
    throw new Error('invalid_dial_job_payload');
  }
  if (!job.lead_id || !job.campaign_id || !job.tenant_id || !job.phone) {
    throw new Error('incomplete_dial_job_identity');
  }
  if (queueKey !== pendingCallsKey(String(job.tenant_id))) {
    throw new Error('dial_job_tenant_queue_mismatch');
  }
  return job;
}

module.exports = { PENDING_CALLS_PREFIX, pendingCallsKey, buildPendingCallJob, parsePendingCallJob };

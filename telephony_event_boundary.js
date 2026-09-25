'use strict';

const crypto = require('crypto');

const CANONICAL_VERSION = '1.0';
const EVENT_TYPES = Object.freeze([
  'telephony.call.lifecycle.dialing',
  'telephony.call.lifecycle.ringing',
  'telephony.call.lifecycle.connected',
  'telephony.call.lifecycle.completed',
  'telephony.call.lifecycle.busy',
  'telephony.call.lifecycle.no_answer',
  'telephony.call.lifecycle.failed',
  'telephony.call.lifecycle.cancelled',
  'telephony.call.lifecycle.timeout',
  'telephony.call.lifecycle.voicemail',
]);

const REQUIRED_FIELDS = Object.freeze([
  'eventId',
  'eventType',
  'schemaVersion',
  'tenantId',
  'callId',
  'providerCallSid',
  'lifecycleState',
  'eventTimestamp',
  'sequenceNo',
]);

function validateCanonicalCallEvent(event) {
  if (!event || typeof event !== 'object') throw new Error('event must be an object');
  for (const field of REQUIRED_FIELDS) {
    if (event[field] === undefined || event[field] === null || event[field] === '') {
      throw new Error('missing canonical event field: ' + field);
    }
  }
  if (event.schemaVersion !== CANONICAL_VERSION) throw new Error('unsupported canonical event schema version');
  if (!EVENT_TYPES.includes(event.eventType)) throw new Error('unknown canonical event type');
  if (!/^[a-f0-9]{64}$/.test(String(event.eventId))) throw new Error('invalid canonical event id');
  if (!Number.isInteger(Number(event.sequenceNo)) || Number(event.sequenceNo) <= 0) {
    throw new Error('invalid canonical event sequence');
  }
  if (!Number.isFinite(Date.parse(event.eventTimestamp))) throw new Error('invalid canonical event timestamp');
  if (event.durationSeconds !== null && event.durationSeconds !== undefined &&
      (!Number.isInteger(Number(event.durationSeconds)) || Number(event.durationSeconds) < 0)) {
    throw new Error('invalid canonical event duration');
  }
  return true;
}

function serializeCanonicalCallEvent(event) {
  validateCanonicalCallEvent(event);
  return JSON.stringify({
    event_id: String(event.eventId),
    event_type: String(event.eventType),
    schema_version: String(event.schemaVersion),
    tenant_id: String(event.tenantId),
    campaign_id: event.campaignId || null,
    lead_id: event.leadId || null,
    call_id: String(event.callId),
    call_attempt_id: event.callAttemptId || null,
    provider: event.provider || 'twilio',
    provider_call_sid: String(event.providerCallSid),
    lifecycle_state: String(event.lifecycleState),
    outcome: event.outcome || null,
    duration_seconds: event.durationSeconds == null ? null : Number(event.durationSeconds),
    recording_reference: event.recordingReference || null,
    callback_reference: event.callbackReference || null,
    event_timestamp: new Date(event.eventTimestamp).toISOString(),
    correlation_id: event.correlationId || null,
    sequence_no: Number(event.sequenceNo),
  });
}

async function persistCanonicalCallEvent(client, event) {
  validateCanonicalCallEvent(event);
  const payload = serializeCanonicalCallEvent(event);
  const result = await client.query(
    `INSERT INTO telephony_call_events
      (event_id,tenant_id,event_type,schema_version,campaign_id,lead_id,call_id,call_attempt_id,
       provider,provider_call_sid,lifecycle_state,outcome,duration_seconds,recording_reference,
       callback_reference,event_timestamp,correlation_id,sequence_no)
     VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18)
     ON CONFLICT (event_id) DO NOTHING
     RETURNING event_id`,
    [
      event.eventId, event.tenantId, event.eventType, event.schemaVersion,
      event.campaignId || null, event.leadId || null, event.callId,
      event.callAttemptId || null, event.provider || 'twilio', event.providerCallSid,
      event.lifecycleState, event.outcome || null,
      event.durationSeconds == null ? null : Number(event.durationSeconds),
      event.recordingReference || null, event.callbackReference || null,
      event.eventTimestamp, event.correlationId || null, Number(event.sequenceNo),
    ]
  );

  if (result.rows.length) {
    await client.query(
      `INSERT INTO telephony_event_outbox
        (event_id,tenant_id,event_type,schema_version,payload,status,attempts,next_attempt_at)
       VALUES ($1,$2,$3,$4,$5::jsonb,'PENDING',0,NOW())
       ON CONFLICT (event_id) DO NOTHING`,
      [event.eventId, event.tenantId, event.eventType, event.schemaVersion, payload]
    );
    return { created: true, duplicate: false, payload };
  }

  return { created: false, duplicate: true, payload };
}

function hashEventPayload(payload) {
  return crypto.createHash('sha256').update(String(payload)).digest('hex');
}

module.exports = {
  CANONICAL_VERSION,
  EVENT_TYPES,
  validateCanonicalCallEvent,
  serializeCanonicalCallEvent,
  persistCanonicalCallEvent,
  hashEventPayload,
};

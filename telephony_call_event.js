'use strict';

const crypto = require('crypto');

const CANONICAL_VERSION = '1.0';
const LIFECYCLE_ORDER = Object.freeze({
  DIALING: 10, RINGING: 20, CONNECTED: 30,
  COMPLETED: 40, BUSY: 40, NO_ANSWER: 40, FAILED: 40,
  CANCELLED: 40, TIMEOUT: 40, VOICEMAIL: 40,
});

function buildCanonicalCallEvent(input) {
  const state=String(input.lifecycleState||'').toUpperCase();
  if(!LIFECYCLE_ORDER[state]) throw new Error('invalid lifecycle state');
  const identity=[input.tenantId,input.callSid,state].join(':');
  const eventId=crypto.createHash('sha256').update(identity).digest('hex');
  return {
    eventId, eventType:'telephony.call.lifecycle.'+state.toLowerCase(),
    schemaVersion:CANONICAL_VERSION, tenantId:String(input.tenantId),
    campaignId:input.campaignId||null, leadId:input.leadId||null,
    callId:input.callId||input.callSid, callAttemptId:input.callAttemptId||null,
    providerCallSid:input.callSid, lifecycleState:state,
    outcome:input.outcome||null, durationSeconds:input.durationSeconds==null?null:Number(input.durationSeconds),
    recordingReference:input.recordingReference||null, callbackReference:input.callbackReference||null,
    eventTimestamp:input.eventTimestamp||new Date().toISOString(),
    correlationId:input.correlationId||null, sequenceNo:LIFECYCLE_ORDER[state],
  };
}

module.exports={CANONICAL_VERSION,LIFECYCLE_ORDER,buildCanonicalCallEvent};

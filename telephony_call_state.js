'use strict';

const PROVIDER_TO_STATE = Object.freeze({
  queued: 'DIALING',
  ringing: 'RINGING',
  'in-progress': 'CONNECTED',
  completed: 'COMPLETED',
  busy: 'BUSY',
  'no-answer': 'NO_ANSWER',
  failed: 'FAILED',
  canceled: 'CANCELLED',
});

const TERMINAL_STATES = new Set(['COMPLETED','BUSY','NO_ANSWER','FAILED','CANCELLED','TIMEOUT','VOICEMAIL']);

const TRANSITIONS = Object.freeze({
  CREATED: new Set(['DIALING','FAILED','CANCELLED','TIMEOUT']),
  DIALING: new Set(['RINGING','CONNECTED','BUSY','NO_ANSWER','FAILED','CANCELLED','TIMEOUT']),
  RINGING: new Set(['CONNECTED','BUSY','NO_ANSWER','FAILED','CANCELLED','TIMEOUT']),
  CONNECTED: new Set(['COMPLETED','FAILED','TIMEOUT']),
  COMPLETED: new Set(), BUSY: new Set(), NO_ANSWER: new Set(),
  FAILED: new Set(), CANCELLED: new Set(), TIMEOUT: new Set(), VOICEMAIL: new Set(),
});

function normalizeProviderStatus(status, answeredBy) {
  const raw = String(status || '').toLowerCase();
  if (raw === 'completed' && answeredBy && /machine|fax/i.test(String(answeredBy))) return 'VOICEMAIL';
  return PROVIDER_TO_STATE[raw] || null;
}

function canTransition(from, to) {
  if (!from || !to) return false;
  if (from === to) return true;
  if (TERMINAL_STATES.has(from)) return false;
  return Boolean(TRANSITIONS[from]?.has(to));
}

module.exports = { PROVIDER_TO_STATE, TERMINAL_STATES, TRANSITIONS, normalizeProviderStatus, canTransition };

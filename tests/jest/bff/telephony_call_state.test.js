'use strict';
const { normalizeProviderStatus, canTransition } = require('../../../telephony_call_state');

describe('telephony call state machine', () => {
  test('normalizes provider statuses', () => {
    expect(normalizeProviderStatus('queued')).toBe('DIALING');
    expect(normalizeProviderStatus('ringing')).toBe('RINGING');
    expect(normalizeProviderStatus('in-progress')).toBe('CONNECTED');
    expect(normalizeProviderStatus('no-answer')).toBe('NO_ANSWER');
    expect(normalizeProviderStatus('busy')).toBe('BUSY');
    expect(normalizeProviderStatus('failed')).toBe('FAILED');
    expect(normalizeProviderStatus('canceled')).toBe('CANCELLED');
  });
  test('rejects backward transitions', () => {
    expect(canTransition('CONNECTED','RINGING')).toBe(false);
    expect(canTransition('COMPLETED','CONNECTED')).toBe(false);
    expect(canTransition('BUSY','COMPLETED')).toBe(false);
  });
  test('accepts forward lifecycle', () => {
    expect(canTransition('CREATED','DIALING')).toBe(true);
    expect(canTransition('DIALING','RINGING')).toBe(true);
    expect(canTransition('RINGING','CONNECTED')).toBe(true);
    expect(canTransition('CONNECTED','COMPLETED')).toBe(true);
  });
  test('duplicate terminal event is idempotent', () => {
    expect(canTransition('COMPLETED','COMPLETED')).toBe(true);
  });
});

'use strict';

const { normalizeProviderStatus, canTransition } = require('../../../telephony_call_state');

describe('W2 telephony provider boundary contracts', () => {
  test('provider lifecycle is monotonic', () => {
    const sequence = ['queued','ringing','in-progress','completed'];
    const states = sequence.map(s => normalizeProviderStatus(s));
    expect(states).toEqual(['DIALING','RINGING','CONNECTED','COMPLETED']);
    for (let i=1;i<states.length;i++) expect(canTransition(states[i-1], states[i])).toBe(true);
  });

  test('replayed terminal status cannot reopen a call', () => {
    expect(canTransition('COMPLETED','RINGING')).toBe(false);
    expect(canTransition('COMPLETED','CONNECTED')).toBe(false);
  });

  test('machine-detected completed call is classified as voicemail', () => {
    expect(normalizeProviderStatus('completed','machine_start')).toBe('VOICEMAIL');
  });
});

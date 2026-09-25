'use strict';

const { FAILURE_CLASSES, classifyProviderFailure } = require('../../../telephony_provider_failure');

describe('W2 provider failure contract', () => {
  test.each([
    [{message:'ECONNRESET'}, FAILURE_CLASSES.TRANSIENT_PROVIDER, true],
    [{code:'TWILIO_RATE_LIMIT'}, FAILURE_CLASSES.RATE_LIMIT, true],
    [{message:'request timed out'}, FAILURE_CLASSES.TIMEOUT, true],
    [{}, FAILURE_CLASSES.BUSY, true],
    [{}, FAILURE_CLASSES.NO_ANSWER, true],
    [{}, FAILURE_CLASSES.VOICEMAIL, false],
    [{message:'invalid destination number'}, FAILURE_CLASSES.INVALID_DESTINATION, false],
    [{code:'20003',message:'authentication failed'}, FAILURE_CLASSES.AUTH_CONFIGURATION, false],
    [{message:'call failed'}, FAILURE_CLASSES.PROVIDER_FAILED, true],
    [{message:'permanent provider rejection'}, FAILURE_CLASSES.PERMANENT_PROVIDER, false],
    [{message:'something unexpected'}, FAILURE_CLASSES.UNKNOWN, false],
  ])('classifies %p', (error, expected, retryable) => {
    const ctx = expected === FAILURE_CLASSES.BUSY ? {callStatus:'busy'} :
      expected === FAILURE_CLASSES.NO_ANSWER ? {callStatus:'no-answer'} :
      expected === FAILURE_CLASSES.VOICEMAIL ? {callStatus:'completed',answeredBy:'machine_start'} : {};
    const got = classifyProviderFailure(error, ctx);
    expect(got.failureClass).toBe(expected);
    expect(got.retryable).toBe(retryable);
  });
});

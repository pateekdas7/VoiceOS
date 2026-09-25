'use strict';

const FAILURE_CLASSES = Object.freeze({
  TRANSIENT_PROVIDER: 'TRANSIENT_PROVIDER',
  RATE_LIMIT: 'RATE_LIMIT',
  TIMEOUT: 'TIMEOUT',
  BUSY: 'BUSY',
  NO_ANSWER: 'NO_ANSWER',
  VOICEMAIL: 'VOICEMAIL',
  PROVIDER_FAILED: 'PROVIDER_FAILED',
  INVALID_DESTINATION: 'INVALID_DESTINATION',
  AUTH_CONFIGURATION: 'AUTH_CONFIGURATION',
  PERMANENT_PROVIDER: 'PERMANENT_PROVIDER',
  UNKNOWN: 'UNKNOWN',
});

const CONTRACT = Object.freeze({
  TRANSIENT_PROVIDER: { terminal: false, retryable: true, action: 'retry_with_backoff' },
  RATE_LIMIT: { terminal: false, retryable: true, action: 'retry_after_provider_window' },
  TIMEOUT: { terminal: true, retryable: true, action: 'bounded_retry' },
  BUSY: { terminal: true, retryable: true, action: 'bounded_retry' },
  NO_ANSWER: { terminal: true, retryable: true, action: 'bounded_retry' },
  VOICEMAIL: { terminal: true, retryable: false, action: 'record_outcome' },
  PROVIDER_FAILED: { terminal: true, retryable: true, action: 'bounded_retry' },
  INVALID_DESTINATION: { terminal: true, retryable: false, action: 'quarantine_destination' },
  AUTH_CONFIGURATION: { terminal: true, retryable: false, action: 'operator_intervention' },
  PERMANENT_PROVIDER: { terminal: true, retryable: false, action: 'operator_intervention' },
  UNKNOWN: { terminal: true, retryable: false, action: 'manual_review' },
});

function classifyProviderFailure(error = {}, context = {}) {
  const status = String(context.callStatus || error.callStatus || '').toLowerCase();
  const code = String(error.code || context.code || '').toLowerCase();
  const message = String(error.message || context.message || '').toLowerCase();
  if (status === 'busy') return result(FAILURE_CLASSES.BUSY, code || 'busy');
  if (status === 'no-answer') return result(FAILURE_CLASSES.NO_ANSWER, code || 'no-answer');
  if (context.answeredBy && /machine|fax/i.test(String(context.answeredBy)) && status === 'completed')
    return result(FAILURE_CLASSES.VOICEMAIL, code || 'machine');
  if (status === 'timeout' || /timed? ?out|timeout|etimedout/.test(message) || ['econnreset','econnrefused','enotfound'].includes(code))
    return result(FAILURE_CLASSES.TIMEOUT, code || 'timeout');
  if (code === 'twilio_rate_limit' || code === '429' || status === '429' || /rate.?limit|too many requests|throttl/.test(message))
    return result(FAILURE_CLASSES.RATE_LIMIT, code || 'rate_limit');
  if (code === '21211' || /invalid.*(phone|number|destination)|not a valid phone|unreachable destination/.test(message))
    return result(FAILURE_CLASSES.INVALID_DESTINATION, code || 'invalid_destination');
  if (code === '20003' || /authentication|authenticate|api key|account.*sid|credential/.test(message))
    return result(FAILURE_CLASSES.AUTH_CONFIGURATION, code || 'auth_configuration');
  if (/network|socket|dns|connection reset|connection refused|temporar(y|ily) unavailable/.test(message))
    return result(FAILURE_CLASSES.TRANSIENT_PROVIDER, code || 'provider_network');
  if (status === 'failed' || status === 'canceled' || /provider.*failed|call failed|rejected/.test(message))
    return result(FAILURE_CLASSES.PROVIDER_FAILED, code || status || 'provider_failed');
  if (/permanent|disabled|unverified|forbidden/.test(message))
    return result(FAILURE_CLASSES.PERMANENT_PROVIDER, code || 'permanent_provider');
  return result(FAILURE_CLASSES.UNKNOWN, code || 'unknown');
}

function result(failureClass, reasonCode) {
  const c = CONTRACT[failureClass];
  return { failureClass, reasonCode, terminal:c.terminal, retryable:c.retryable, nextAction:c.action };
}

module.exports = { FAILURE_CLASSES, CONTRACT, classifyProviderFailure };

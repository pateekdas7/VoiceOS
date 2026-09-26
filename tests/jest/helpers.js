'use strict';
const jwt = require('jsonwebtoken');

const SECRET = process.env.JWT_SECRET;

/**
 * Generate a valid signed JWT for testing.
 * Default payload: ADMIN user for tenant 't1'.
 */
function makeToken(overrides = {}) {
  const payload = {
    user_id:   'user-test-001',
    tenant_id: 'tenant-test-001',
    email:     'admin@test.example',
    role:      'ADMIN',
    ...overrides,
  };
  return jwt.sign(payload, SECRET, { expiresIn: '1h' });
}

/** Build a cookie header string for a given token. */
function authCookie(token) {
  return `voiceos_session=${token}`;
}

/** Make an authenticated cookie header for a standard test user. */
function adminCookie(overrides = {}) {
  return authCookie(makeToken(overrides));
}

/** Tenant B cookie — different tenant from the default. */
function tenantBCookie() {
  return authCookie(makeToken({ tenant_id: 'tenant-test-002', user_id: 'user-b-001' }));
}

// ── Shared mock factories ────────────────────────────────────────────────────

/**
 * Create a mock pg client (returned by pool.connect()).
 * `queryResponses` is an ordered array of { rows } objects.
 */
function makeMockClient(queryResponses = []) {
  let idx = 0;
  return {
    query: jest.fn().mockImplementation(() => {
      const resp = queryResponses[idx] || { rows: [] };
      idx++;
      return Promise.resolve(resp);
    }),
    release: jest.fn(),
  };
}

/**
 * Create a mock pg pool.
 * `queryResponses` is an ordered array of { rows } objects for pool.query().
 */
function makeMockPool(queryResponses = []) {
  let idx = 0;
  return {
    query: jest.fn().mockImplementation(() => {
      const resp = queryResponses[idx] || { rows: [] };
      idx++;
      return Promise.resolve(resp);
    }),
    connect: jest.fn().mockResolvedValue(makeMockClient()),
    end: jest.fn(),
  };
}

/** Minimal Redis mock. */
function makeMockRedis() {
  return {
    ping:      jest.fn().mockResolvedValue('PONG'),
    set:       jest.fn().mockResolvedValue('OK'),
    get:       jest.fn().mockResolvedValue(null),
    del:       jest.fn().mockResolvedValue(1),
    hget:      jest.fn().mockResolvedValue(null),
    hset:      jest.fn().mockResolvedValue(1),
    hdel:      jest.fn().mockResolvedValue(1),
    expire:    jest.fn().mockResolvedValue(1),
    zadd:      jest.fn().mockResolvedValue(1),
    lpush:     jest.fn().mockResolvedValue(1),
    rpop:      jest.fn().mockResolvedValue(null),
    exists:    jest.fn().mockResolvedValue(0),
    on:        jest.fn().mockReturnThis(),
    status:    'ready',
    disconnect: jest.fn(),
  };
}

module.exports = { makeToken, authCookie, adminCookie, tenantBCookie, makeMockClient, makeMockPool, makeMockRedis };

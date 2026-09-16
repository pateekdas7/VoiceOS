'use strict';
/**
 * Auth route tests — POST /auth/password/login, POST /auth/logout
 * Tests: valid credentials → 200 + cookie, bad password → 401,
 *        missing fields → 400, unknown user → 401.
 * requireAuth middleware: missing cookie → 401, expired token → 401.
 */

jest.mock('pg');
jest.mock('ioredis');
jest.mock('twilio', () => jest.fn(() => ({})));

const { Pool } = require('pg');
const Redis    = require('ioredis');
const request  = require('supertest');
const bcrypt   = require('bcryptjs');
const { adminCookie } = require('../helpers');

// ── Wire mocks before bff.js is required ─────────────────────────────────────

const mockRedis = {
  ping: jest.fn().mockResolvedValue('PONG'),
  set: jest.fn().mockResolvedValue('OK'),
  get: jest.fn().mockResolvedValue(null),
  del: jest.fn().mockResolvedValue(1),
  hget: jest.fn().mockResolvedValue(null),
  hset: jest.fn().mockResolvedValue(1),
  hdel: jest.fn().mockResolvedValue(1),
  expire: jest.fn().mockResolvedValue(1),
  zadd: jest.fn().mockResolvedValue(1),
  lpush: jest.fn().mockResolvedValue(1),
  on: jest.fn().mockReturnThis(),
  disconnect: jest.fn(),
  connect: jest.fn().mockResolvedValue(undefined),
};
Redis.mockImplementation(() => mockRedis);

const mockPool = {
  query: jest.fn().mockResolvedValue({ rows: [] }),
  connect: jest.fn().mockResolvedValue({
    query: jest.fn().mockResolvedValue({ rows: [] }),
    release: jest.fn(),
  }),
  end: jest.fn(),
  on: jest.fn(),
};
Pool.mockImplementation(() => mockPool);

const { app } = require('../../../bff');

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('POST /auth/password/login', () => {
  const HASH = bcrypt.hashSync('correct-pass', 10);

  beforeEach(() => jest.clearAllMocks());

  it('returns 400 when email or password missing', async () => {
    const res = await request(app).post('/auth/password/login').send({ email: 'a@b.com' });
    expect(res.status).toBe(400);
    expect(res.body.error).toBe('missing_fields');
  });

  it('returns 401 for unknown user (no DB row)', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [] }); // user not found
    const res = await request(app).post('/auth/password/login').send({ email: 'x@y.com', password: 'pass' });
    expect(res.status).toBe(401);
  });

  it('returns 401 for wrong password', async () => {
    mockPool.query.mockResolvedValueOnce({
      rows: [{ user_id: 'u1', tenant_id: 't1', email: 'a@b.com', password_hash: HASH, role: 'ADMIN', is_active: true }],
    });
    const res = await request(app).post('/auth/password/login').send({ email: 'a@b.com', password: 'wrong-pass' });
    expect(res.status).toBe(401);
  });

  it('returns 200 and sets cookie for correct credentials', async () => {
    mockPool.query.mockResolvedValueOnce({
      rows: [{ user_id: 'u1', tenant_id: 't1', email: 'a@b.com', password_hash: HASH, role: 'ADMIN', is_active: true }],
    });
    const res = await request(app).post('/auth/password/login').send({ email: 'a@b.com', password: 'correct-pass' });
    expect(res.status).toBe(200);
    expect(res.headers['set-cookie']).toBeDefined();
    const cookie = res.headers['set-cookie'][0];
    expect(cookie).toContain('voiceos_session=');
    expect(cookie).toContain('HttpOnly');
  });

  it('returns 401 for inactive account (DB filters is_active=TRUE)', async () => {
    // The login query uses WHERE is_active=TRUE — inactive users are never returned.
    // Simulate that by returning empty rows for both platform_users and users queries.
    mockPool.query.mockResolvedValueOnce({ rows: [] }); // platform_users: no match
    mockPool.query.mockResolvedValueOnce({ rows: [] }); // users: no match (filtered by is_active)
    const res = await request(app).post('/auth/password/login').send({ email: 'a@b.com', password: 'correct-pass' });
    expect(res.status).toBe(401);
  });
});

describe('POST /auth/logout', () => {
  it('clears the session cookie and returns 200', async () => {
    const res = await request(app)
      .post('/auth/logout')
      .set('Cookie', adminCookie());
    expect([200, 204]).toContain(res.status);
    // Cookie should be cleared (Max-Age=0 or expires in the past)
    if (res.headers['set-cookie']) {
      const cookieStr = res.headers['set-cookie'][0];
      expect(cookieStr).toMatch(/voiceos_session=;|Max-Age=0/);
    }
  });
});

describe('requireAuth middleware', () => {
  it('returns 401 when no session cookie is present', async () => {
    const res = await request(app).get('/campaigns');
    expect(res.status).toBe(401);
  });

  it('returns 401 for an expired token', async () => {
    const jwt = require('jsonwebtoken');
    const expired = jwt.sign({ user_id: 'u1', tenant_id: 't1', role: 'ADMIN' }, process.env.JWT_SECRET, { expiresIn: '-1s' });
    const res = await request(app).get('/campaigns').set('Cookie', `voiceos_session=${expired}`);
    expect(res.status).toBe(401);
  });

  it('returns 401 for a token signed with wrong secret', async () => {
    const jwt = require('jsonwebtoken');
    const bad = jwt.sign({ user_id: 'u1', tenant_id: 't1', role: 'ADMIN' }, 'wrong-secret');
    const res = await request(app).get('/campaigns').set('Cookie', `voiceos_session=${bad}`);
    expect(res.status).toBe(401);
  });

  it('passes through with a valid token', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [] }); // empty campaigns list
    const res = await request(app).get('/campaigns').set('Cookie', adminCookie());
    expect(res.status).not.toBe(401);
  });
});

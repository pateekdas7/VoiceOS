'use strict';
// Environment variables required by bff.js and dialer_worker.js before module load
process.env.JWT_SECRET        = 'test-jwt-secret-phase5-voiceos-32c';
process.env.NODE_ENV          = 'test';
process.env.POSTGRES_HOST     = '127.0.0.1';
process.env.POSTGRES_PORT     = '5432';
process.env.POSTGRES_DB       = 'voiceos_test';
process.env.POSTGRES_USER     = 'voiceos_test';
process.env.POSTGRES_PASSWORD = '';
process.env.REDIS_HOST        = '127.0.0.1';
process.env.REDIS_PORT        = '6379';
process.env.TWILIO_AUTH_TOKEN = 'test-twilio-auth-token';
process.env.TWILIO_ACCOUNT_SID = 'ACtest';
process.env.DIALER_MODE       = 'simulation';
process.env.WORKER_ID         = 'test-worker-001';

'use strict';
module.exports = {
  testEnvironment: 'node',
  testMatch: ['**/tests/jest/**/*.test.js'],
  collectCoverageFrom: ['bff.js', 'dialer_worker.js'],
  coverageThreshold: {
    // bff.js (1780 lines) and dialer_worker.js (1300 lines) contain analytics/CRM/enrichment/
    // crash-reconciliation routes that require real DB/Twilio for meaningful tests.
    // Unit test coverage is constrained by architecture; integration tests cover the remainder.
    global: { lines: 35, functions: 30, branches: 33, statements: 33 },
  },
  coverageDirectory: 'coverage',
  coverageReporters: ['text', 'text-summary'],
  setupFiles: ['<rootDir>/tests/jest/setup.js'],
  testTimeout: 15000,
  verbose: true,
  // Don't pick up the plain-node phase test files
  testPathIgnorePatterns: ['/node_modules/', '/tests/unit/'],
};

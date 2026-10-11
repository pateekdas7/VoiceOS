'use strict';
module.exports = {
  testEnvironment: 'node',
  testMatch: ['**/tests/jest/**/*.test.js'],
  collectCoverageFrom: ['bff.js', 'dialer_worker.js'],
  coverageThreshold: {
    // bff.js must maintain >=70% line coverage; dialer_worker covered by integration tests.
    './bff.js': { lines: 70 },
  },
  coverageDirectory: 'coverage',
  coverageReporters: ['text', 'text-summary'],
  setupFiles: ['<rootDir>/tests/jest/setup.js'],
  testTimeout: 15000,
  verbose: true,
  testPathIgnorePatterns: ['/node_modules/', '/tests/unit/'],
};

import assert from 'node:assert/strict';
import { test } from 'node:test';
import { configuration } from '../src/config.js';

const production = {
  NODE_ENV: 'production',
  DATABASE_URL: 'postgresql://test:test@localhost/test',
  SESSION_KEY: 'ab'.repeat(32),
  APP_ORIGIN: 'https://example.com',
  GOOGLE_CLIENT_ID: 'test',
  GOOGLE_CLIENT_SECRET: 'test',
  STRIPE_SECRET_KEY: 'sk_test_example',
  STRIPE_PRICE_ID: 'price_example',
  STRIPE_WEBHOOK_SECRET: 'whsec_example',
};

test('production requires HTTPS and all release providers', () => {
  assert.equal(configuration(production).NODE_ENV, 'production');
  assert.throws(() => configuration({ ...production, APP_ORIGIN: 'http://example.com' }), /HTTPS/);

  for (const key of [
    'GOOGLE_CLIENT_ID',
    'GOOGLE_CLIENT_SECRET',
    'STRIPE_SECRET_KEY',
    'STRIPE_PRICE_ID',
    'STRIPE_WEBHOOK_SECRET',
  ]) {
    assert.throws(() => configuration({ ...production, [key]: '' }), new RegExp(key));
  }
});

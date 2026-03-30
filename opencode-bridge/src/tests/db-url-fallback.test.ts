import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildDatabaseUrlCandidates } from '../db.js';

test('buildDatabaseUrlCandidates keeps original first', () => {
  const original = 'postgresql://letta:letta@192.168.50.90:5432/matrix_letta';
  const candidates = buildDatabaseUrlCandidates(original);

  assert.equal(candidates[0], original);
});

test('buildDatabaseUrlCandidates adds localhost fallbacks for remote host', () => {
  const original = 'postgresql://letta:letta@192.168.50.90:5432/matrix_letta';
  const candidates = buildDatabaseUrlCandidates(original);

  assert.ok(candidates.some((url) => url.includes('@127.0.0.1:5432')));
  assert.ok(candidates.some((url) => url.includes('@localhost:5432')));
  assert.ok(candidates.some((url) => url.includes('@host.docker.internal:5432')));
});

test('buildDatabaseUrlCandidates does not duplicate local hosts', () => {
  const original = 'postgresql://letta:letta@127.0.0.1:5432/matrix_letta';
  const candidates = buildDatabaseUrlCandidates(original);

  assert.equal(candidates.length, 1);
  assert.equal(candidates[0], original);
});

test('buildDatabaseUrlCandidates returns original when url cannot be parsed', () => {
  const original = 'not-a-url';
  const candidates = buildDatabaseUrlCandidates(original);

  assert.deepEqual(candidates, [original]);
});

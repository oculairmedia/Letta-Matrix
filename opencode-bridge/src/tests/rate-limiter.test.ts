import { test } from "node:test";
import assert from "node:assert/strict";
import { createBucket, tryConsume } from "../rate-limiter.js";

const CONFIG = { capacity: 20, refillPerSec: 5 };

test("fresh bucket starts full and admits up to capacity in one instant", () => {
  const b = createBucket(CONFIG, 0);
  let admitted = 0;
  for (let i = 0; i < 100; i++) {
    if (tryConsume(b, CONFIG, 0)) admitted++;
  }
  assert.equal(admitted, CONFIG.capacity);
});

test("refills at configured rate over time", () => {
  const b = createBucket(CONFIG, 0);
  for (let i = 0; i < CONFIG.capacity; i++) tryConsume(b, CONFIG, 0);
  assert.equal(tryConsume(b, CONFIG, 0), false);

  // After 1 second we should have refillPerSec tokens available.
  let admitted = 0;
  for (let i = 0; i < 100; i++) {
    if (tryConsume(b, CONFIG, 1000)) admitted++;
  }
  assert.equal(admitted, CONFIG.refillPerSec);
});

test("caps at capacity no matter how long we idle", () => {
  const b = createBucket(CONFIG, 0);
  for (let i = 0; i < CONFIG.capacity; i++) tryConsume(b, CONFIG, 0);
  let admitted = 0;
  for (let i = 0; i < 100; i++) {
    if (tryConsume(b, CONFIG, 60 * 60 * 1000)) admitted++;
  }
  assert.equal(admitted, CONFIG.capacity);
});

test("sustained 1/s request rate is fully admitted under 5/s cap", () => {
  const b = createBucket(CONFIG, 0);
  let admitted = 0;
  for (let sec = 0; sec < 10; sec++) {
    if (tryConsume(b, CONFIG, sec * 1000)) admitted++;
  }
  assert.equal(admitted, 10);
});

test("runaway 100/s request rate is capped around burst + sustained refill", () => {
  const b = createBucket(CONFIG, 0);
  let admitted = 0;
  // Simulate 2 seconds of 100 requests/sec = 200 attempts, 10ms apart.
  for (let i = 0; i < 200; i++) {
    if (tryConsume(b, CONFIG, i * 10)) admitted++;
  }
  // Upper bound: capacity (20 burst) + refillPerSec * elapsed (5 * 2 = 10) = 30
  // Allow 1 slack for floating point edges at boundaries.
  assert.ok(admitted <= CONFIG.capacity + CONFIG.refillPerSec * 2 + 1,
    `admitted ${admitted} exceeded cap`);
  assert.ok(admitted >= CONFIG.capacity, `admitted ${admitted} below initial burst`);
});

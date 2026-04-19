export interface TokenBucket {
  tokens: number;
  lastRefillMs: number;
}

export interface TokenBucketConfig {
  capacity: number;
  refillPerSec: number;
}

export function createBucket(config: TokenBucketConfig, nowMs: number): TokenBucket {
  return { tokens: config.capacity, lastRefillMs: nowMs };
}

export function refill(bucket: TokenBucket, config: TokenBucketConfig, nowMs: number): void {
  const elapsedMs = nowMs - bucket.lastRefillMs;
  if (elapsedMs <= 0) return;
  const add = (elapsedMs / 1000) * config.refillPerSec;
  bucket.tokens = Math.min(config.capacity, bucket.tokens + add);
  bucket.lastRefillMs = nowMs;
}

export function tryConsume(
  bucket: TokenBucket,
  config: TokenBucketConfig,
  nowMs: number,
  cost = 1,
): boolean {
  refill(bucket, config, nowMs);
  if (bucket.tokens >= cost) {
    bucket.tokens -= cost;
    return true;
  }
  return false;
}

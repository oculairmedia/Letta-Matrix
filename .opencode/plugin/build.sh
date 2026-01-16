#!/bin/bash
cd "$(dirname "$0")"
# Temporarily rename wrapper
mv matrix-context-injector.ts matrix-context-injector.ts.wrapper 2>/dev/null || true
# Rename source to .ts for build
mv matrix-context-injector.ts.src matrix-context-injector.ts
# Build
bun build matrix-context-injector.ts --outdir dist --target bun
# Restore names
mv matrix-context-injector.ts matrix-context-injector.ts.src
mv matrix-context-injector.ts.wrapper matrix-context-injector.ts 2>/dev/null || true

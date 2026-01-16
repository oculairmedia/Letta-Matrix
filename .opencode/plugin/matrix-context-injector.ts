// This file re-exports from the built JS bundle
// Source is in matrix-context-injector.ts.src
// Rebuild with: bun build plugin/matrix-context-injector.ts.src --outdir plugin/dist --target bun
export { MatrixContextInjector } from "./dist/matrix-context-injector.js";
export { MatrixContextInjector as default } from "./dist/matrix-context-injector.js";

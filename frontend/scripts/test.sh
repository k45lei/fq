#!/usr/bin/env bash
#
# The frontend's tests.
#
# There is no test framework installed, and there isn't a need for one: the assertions here
# are about URL construction, request payloads and error handling, and Node's built-in test
# runner covers those. What Node's runner *can't* do is read TypeScript, and this Node build
# has no `--experimental-strip-types` support, so the sources are transpiled first.
#
# `rolldown` does that, and it's already here — it's the bundler Vite 8 uses — so the test
# run adds no dependency. Vite is used for the production build; this is for the tests.
#
# The transpiled output goes to a temporary directory and is removed afterwards, so nothing
# derived from the sources is left in the tree.

set -euo pipefail

cd "$(dirname "$0")/.."

BUILD_DIR="$(mktemp -d)"
trap 'rm -rf "$BUILD_DIR"' EXIT

echo "Type-checking the tests…"
pnpm exec tsc -p tsconfig.test.json --noEmit

echo "Transpiling the test sources…"
./node_modules/.bin/rolldown src/api/client.test.ts src/lib/checkout.test.ts -d "$BUILD_DIR" --format esm --platform node

echo "Running the tests…"
node --test "$BUILD_DIR"/*.test.js

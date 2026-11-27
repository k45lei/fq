#!/bin/bash
set -euo pipefail

docker compose --env-file backend/.env.production -f compose.production.yml build --no-cache
#builds frontend and backend from scratch

docker compose --env-file backend/.env.production -f compose.production.yml run --rm api npm run db:migrate
#run database migrations

docker compose --env-file backend/.env.production -f compose.production.yml run --rm api npm run db:seed:production
#seed the fixed reference instrument

docker compose --env-file backend/.env.production -f compose.production.yml up -d --wait --force-recreate

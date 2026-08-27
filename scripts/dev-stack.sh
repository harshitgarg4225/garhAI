#!/usr/bin/env bash
# Bring the local dev datastores back up (this sandbox OOM-kills them).
# Idempotent: safe to run before any test batch.
set -u
(sudo pg_ctlcluster 16 main start 2>/dev/null || pg_ctlcluster 16 main start 2>/dev/null) || true
redis-cli ping >/dev/null 2>&1 || redis-server --daemonize yes
curl -s -m 2 -o /dev/null http://localhost:9000/ || \
  (nohup /home/user/garhAI/.venv/bin/python -m moto.server -p 9000 >/tmp/moto.log 2>&1 & sleep 3)
curl -s -X PUT http://localhost:9000/garh-dev -o /dev/null
printf 'pg:%s redis:%s moto:%s\n' \
  "$(pg_isready -q && echo up || echo DOWN)" \
  "$(redis-cli ping 2>/dev/null || echo DOWN)" \
  "$(curl -s -m 2 -o /dev/null -w '%{http_code}' http://localhost:9000/)"

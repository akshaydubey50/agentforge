#!/bin/sh
# Combined api+worker entrypoint, used only by Railway's "agentsys" service
# start-command override -- docker-compose.yml keeps api and worker as
# separate services/containers locally, unchanged, and does not use this
# script at all.
#
# Why combined: api and worker share a filesystem in docker-compose (the
# ./data bind mount on both), which is how file_io and POST /v1/tasks/upload
# work -- api writes an uploaded file into the task's workspace, worker reads
# it back later. Separate Railway services don't share a filesystem by
# default, so running both processes in one container (one Railway Volume)
# is what preserves that assumption without reaching for object storage.
#
# Known limitation, stated not hidden: this is a plain background+foreground
# pattern, not a real process supervisor -- if the celery worker crashes,
# nothing here restarts it independently; only Railway restarting the whole
# service recovers it (Railway's healthcheck only watches the uvicorn port).
# Fine for this deployment's scale; a real supervisor (tini + a proper
# process manager) would be the next step if that ever matters.
set -e

celery -A agentsys.worker.celery_app worker --loglevel=info --pool=solo &

exec uvicorn agentsys.main:app --host 0.0.0.0 --port "${PORT:-8000}"

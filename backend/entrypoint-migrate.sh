#!/bin/sh
# One-shot release task: apply migrations (idempotent; includes role/permission and provider seed data).
set -e
python manage.py migrate --noinput

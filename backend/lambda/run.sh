#!/bin/sh
# Started by the Lambda Web Adapter (AWS_LAMBDA_EXEC_WRAPPER=/opt/bootstrap).
export PYTHONPATH="/var/task:${PYTHONPATH:-}"
exec python -m uvicorn nettriage.entrypoints.api.main:app \
  --host 127.0.0.1 --port "${AWS_LWA_PORT:-8080}" --no-access-log

#!/usr/bin/env bash
# Proves the layer runs where the `ops` function will (Plan 7a §8): inside AWS's Lambda image,
# as a non-root user and without /dev/shm, like Lambda. Using only what the layer holds, it
# checks every program and library finds what it links to, dumps the database PG* points at,
# restores the dump into a throwaway server as a non-superuser owner, and compares the restored
# data with the original's.
#
# CI runs it on the arm64 runner, against its Postgres service:
#   PGHOST=localhost PGUSER=postgres PGPASSWORD=… bash tools/pg_client/verify.sh dist/pg-client.zip
set -euo pipefail

LAYER=${1:?usage: verify.sh LAYER_ZIP}
work=$(mktemp -d)
unzip -q "$LAYER" -d "$work/opt"
mkdir -m 777 "$work/out"

# Something worth restoring: rows behind forced row-level security, a policy naming a role, and
# a PL/pgSQL trigger, as NetTriage's tables have.
psql -q -d postgres -c "DROP DATABASE IF EXISTS layer_check" -c "CREATE DATABASE layer_check"
psql -q -d layer_check -c "
  DO \$\$ BEGIN CREATE ROLE layer_reader NOLOGIN; EXCEPTION WHEN duplicate_object THEN END \$\$;
  CREATE TABLE notes (id int PRIMARY KEY, org text NOT NULL, body text NOT NULL);
  INSERT INTO notes SELECT g, 'org-' || (g % 3), md5(g::text) FROM generate_series(1, 2000) g;
  ALTER TABLE notes ENABLE ROW LEVEL SECURITY;
  ALTER TABLE notes FORCE ROW LEVEL SECURITY;
  CREATE POLICY one_org ON notes TO layer_reader USING (org = 'org-1');
  CREATE FUNCTION touch() RETURNS trigger LANGUAGE plpgsql AS \$f\$ BEGIN RETURN NEW; END \$f\$;
  CREATE TRIGGER notes_touch BEFORE UPDATE ON notes FOR EACH ROW EXECUTE FUNCTION touch();"

docker run --rm --network host --ipc=none --user nobody \
  -v "$work/opt/pg:/opt/pg:ro" -v "$work/out:/out" -e PGHOST -e PGUSER -e PGPASSWORD -e PGDATABASE=layer_check \
  --entrypoint /bin/bash public.ecr.aws/lambda/python:3.14 -c '
    set -euo pipefail
    B=/opt/pg/bin
    if ldd $B/* /opt/pg/lib/libpq.so.5 /opt/pg/lib/postgresql/*.so | grep "not found"; then
      echo "a library the layer needs is missing from the Lambda image"
      exit 1
    fi
    test ! -e /dev/shm
    $B/pg_dump --version
    $B/pg_dump -Fc -f /tmp/layer.dump
    $B/pg_dump -Fp --data-only -f /out/original.sql

    # The throwaway server: set up in single-user mode, then started on a Unix socket only.
    $B/initdb -D /tmp/data -U drill --auth=trust --no-sync -E UTF8 --locale=C > /dev/null
    echo "dynamic_shared_memory_type = mmap" >> /tmp/data/postgresql.conf
    for statement in "CREATE ROLE layer_reader NOLOGIN" "CREATE ROLE restorer LOGIN" \
                     "CREATE DATABASE layer_check OWNER restorer"; do
      echo "$statement" | $B/postgres --single -D /tmp/data postgres > /dev/null
    done
    $B/pg_ctl -D /tmp/data -l /tmp/server.log -w \
      -o "-c listen_addresses= -c unix_socket_directories=/tmp -p 5433" start > /dev/null
    export PGHOST=/tmp PGPORT=5433 PGUSER=restorer PGPASSWORD=
    $B/pg_restore -d layer_check --no-owner --no-privileges --exit-on-error /tmp/layer.dump
    # Read back as the superuser drill: the restored tables force row-level security.
    PGUSER=drill $B/pg_dump -d layer_check -Fp --data-only -f /out/restored.sql
    $B/pg_ctl -D /tmp/data -m fast -w stop > /dev/null
  '

# Compared here: the Lambda image has no diff. Plain dumps carry comments and a random \restrict
# key (Postgres 17.6 on); the data itself must match.
keep() { grep -vE '^(--|\\(un)?restrict )' "$1"; }
diff <(keep "$work/out/original.sql") <(keep "$work/out/restored.sql")
echo "the layer dumped, restored and matched the original inside the Lambda image"

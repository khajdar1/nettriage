#!/usr/bin/env bash
# Builds Postgres for the `ops` function's layer (Plan 7a §4), inside Amazon Linux 2023, the OS
# of Lambda's python3.14 runtime, from the PostgreSQL project's source, checked against its
# published SHA-256. LDAP, ICU, PAM, systemd and readline are left out: the Lambda image has
# none of their libraries, and the backup and the drill don't need them.
#
# CI runs it on an arm64 runner:
#   docker run --rm -v "$PWD:/w" -w /w public.ecr.aws/amazonlinux/amazonlinux:2023 \
#     bash tools/pg_client/build.sh build
# and finds the install under build/pg, for tools/pack_pg_client.py.
set -euo pipefail

VERSION=17.11
SHA256=dd27f2b3c59e73ed14aa3324901242bf69a032a6347805f274e6260322d42979
OUT=${1:?usage: build.sh OUT_DIR}

dnf install -y -q gcc make tar bzip2 binutils openssl-devel zlib-devel perl bison flex > /dev/null

work=$(mktemp -d)
curl -fsSL -o "$work/postgresql.tar.bz2" \
  "https://ftp.postgresql.org/pub/source/v$VERSION/postgresql-$VERSION.tar.bz2"
echo "$SHA256  $work/postgresql.tar.bz2" | sha256sum -c -
tar -xjf "$work/postgresql.tar.bz2" -C "$work"

cd "$work/postgresql-$VERSION"
./configure --prefix=/opt/pg --without-readline --without-icu --with-openssl --with-zlib > /dev/null
make -j"$(nproc)" -s > /dev/null
make -s install > /dev/null
# Only the programs and shared libraries: pgxs also installs shell scripts strip can't read.
find /opt/pg/bin -type f -exec strip --strip-unneeded {} +
find /opt/pg/lib -type f -name '*.so*' -exec strip --strip-unneeded {} +

mkdir -p "/w/$OUT"
cp -a /opt/pg "/w/$OUT/"
echo "built Postgres $VERSION into $OUT/pg"

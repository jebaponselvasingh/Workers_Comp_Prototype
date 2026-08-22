# The Postgres image this build runs everywhere: pgvector plus pgaudit.
#
# `pgvector/pgvector:pg18` is built on the official `postgres:18` image, which
# already configures the PGDG apt repository — so the audit extension is one
# package away and needs no third-party source, no compiler and no pinned
# tarball. pgvector itself is why the base image is not `postgres:18`: the
# embedding tables are `vector(1024)` and the extension has to be present
# before migration 0006 runs.
#
# **Nothing else belongs in this file.** The hardening posture itself — which
# pgaudit classes are captured, which logging GUCs are pinned off, where the
# collector writes — lives in the `command:` flag list on the postgres service
# in every compose profile, not here. That is deliberate (Story 8.2, Design
# Note 1): settings baked into the image's `postgresql.conf.sample` apply only
# at `initdb`, so a `pgdata` volume created before this image existed would
# keep the stock posture for ever and the drift would be invisible. `-c` flags
# apply on every start regardless of when the volume was created, and a
# weakened one is a reviewable one-line diff.
FROM pgvector/pgvector:pg18

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-18-pgaudit \
    && rm -rf /var/lib/apt/lists/*

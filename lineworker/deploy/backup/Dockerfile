# The backup job's image: the PostgreSQL 18 client tools, `age`, and `rsync`.
#
# ## Why the base image is a Postgres image, and why it is PG 18
#
# `pg_dump`, `pg_basebackup` and `pg_receivewal` are **client** programs whose
# version has to track the *server's* major version, and the direction of the
# rule is the one people get wrong: a newer client can read an older server, an
# older client cannot read a newer one. `pg_dump 17` against a PostgreSQL 18
# server refuses outright ("server version 18.x; pg_dump version 17.x"), and
# `pg_basebackup` speaks a replication protocol whose version negotiation is
# tighter still. So the tools have to come from the same major family as
# `deploy/postgres/Dockerfile`'s base — which is `pgvector/pgvector:pg18`, built
# on the official `postgres:18` image, which is this file's base by the shortest
# path that gets exactly those binaries and nothing else.
#
# `postgres:18-trixie` rather than `pgvector/pgvector:pg18`: this container never
# runs a server and never reads a vector, so the pgvector extension would be
# dead weight, and pinning the Debian codename keeps the client's libc and
# OpenSSL from moving under the image on a rebuild. The postmaster binary rides
# along unused; that is the cost of taking the client tools from the image that
# is guaranteed to have the matching ones.
#
# ## Why `age` rather than GPG
#
# Both would encrypt. What this job needs is a *non-interactive* encryptor with
# no keyring, no agent, no trust database and no home directory to provision
# inside a container that is recreated on every deploy — and `age -r <recipient>`
# is exactly that: one public key on the command line, ciphertext on stdout, no
# state anywhere. GPG can be driven that way too, with `--batch --yes
# --trust-model always --homedir …`, which is four flags whose job is to switch
# off machinery this job never wanted.
#
# The load-bearing property is not the algorithm and not the tool. It is that
# **this image, this container and this host hold only the recipient** — the
# public half. Compromising the backup host, which is the exact scenario an
# off-host backup exists to survive, yields no ability to read any backup, past
# or future. The identity (private key) lives in the operator's secret store and
# is fetched only at restore time, on a machine that is not this one; that is
# the runbook's first prerequisite and it is why `deploy/compose.e2e.yaml` can
# commit a recipient in plain sight with no identity for it anywhere on earth.
#
# ## Why `rsync` and `openssh-client`
#
# The off-host copy is `rsync -a`, over SSH when the destination names a host
# and over the filesystem when it names a path. `rsync` because the run
# directory is a directory (artifacts plus the encrypted WAL segments plus the
# manifest) and because a partial transfer that is resumed rather than restarted
# is the difference between a nightly job that keeps up and one that does not.
# `openssh-client` because rsync's `-e ssh` needs a client and the base image
# does not carry one. `ca-certificates` so a future `rclone` destination — an
# object store over HTTPS, which the deferred blob-backend decision may bring —
# has a trust store to verify against; it costs nothing and its absence is the
# kind of thing discovered at 02:30.
#
# ## Root, deliberately
#
# The official Postgres image drops to `postgres` only via its own entrypoint,
# which this image replaces; the process therefore runs as root. That is the
# right answer here rather than an oversight: the status directory is a
# **bind mount** in the e2e profile (`e2e/.tmp/backup`, read by the Playwright
# process on the host) and a named volume elsewhere, and a non-root uid inside
# the container matching neither the host user nor the volume's owner produces a
# job whose only failure mode is "permission denied" at 02:30 on somebody else's
# machine. The container publishes no port, joins the internal network only, and
# runs one shell script on a timer; the exposure that root buys is small and the
# breakage that a mismatched uid buys is certain.
FROM postgres:18-trixie

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        age \
        rsync \
        openssh-client \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY backup.sh entrypoint.sh healthcheck.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/backup.sh \
    /usr/local/bin/entrypoint.sh \
    /usr/local/bin/healthcheck.sh

# The entrypoint, not the backup script: `entrypoint.sh` with no argument is the
# scheduler; with `once` it `exec`s the backup script so the container's exit
# code *is* the run's. Both paths write the container-start marker the
# healthcheck measures its staleness window from.
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

-- e2e-profile-only DB owner role (AD-15). The per-spec reset (drop schema →
-- migrate → seed) runs as this role; it exists in no other environment.
-- SUPERUSER because re-running migrations after a schema drop must recreate
-- the pgvector extension (untrusted — superuser-only). This is harness
-- plumbing: the AD-4 guarantee is that the RUNTIME app role has no DDL,
-- which the grants migration enforces and pytest asserts.
CREATE ROLE lineworker_e2e_owner LOGIN SUPERUSER PASSWORD 'lineworker_e2e';
ALTER SCHEMA public OWNER TO lineworker_e2e_owner;

-- Layer 3 of the db_query boundary: a Postgres role that CANNOT read the
-- application's own tables, so the guarantee survives a bug in our
-- in-process allowlist (see src/agentsys/tools/db_query.py).
--
-- Run once, as a user with rights to create roles:
--     psql "$DATABASE_URL" -f scripts/create_readonly_role.sql
--
-- Then point the tool at it:
--     DB_QUERY_URL=postgresql+psycopg://agent_readonly:<password>@host:5432/agentsys
--
-- Until DB_QUERY_URL is set, the allowlist is the ONLY boundary. It is a real
-- one, but it is enforced by application code; this is enforced by the
-- database, which is the difference that matters if the application is wrong.

\set ON_ERROR_STOP on

-- Change this before running. Kept as a placeholder rather than a default so
-- nobody deploys the password that shipped in a repository.
\set readonly_password 'CHANGE_ME'

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agent_readonly') THEN
        CREATE ROLE agent_readonly LOGIN;
    END IF;
END
$$;

ALTER ROLE agent_readonly WITH PASSWORD :'readonly_password';

-- Start from nothing. PUBLIC has USAGE + CREATE on the public schema by
-- default in Postgres < 15, which would otherwise leave this role able to
-- read far more than intended.
REVOKE ALL ON SCHEMA public FROM agent_readonly;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM agent_readonly;

GRANT CONNECT ON DATABASE agentsys TO agent_readonly;
GRANT USAGE ON SCHEMA public TO agent_readonly;

-- The allowlist, as a grant. Keep in sync with DB_QUERY_ALLOWED_TABLES;
-- either layer refusing is enough, so a disagreement fails closed.
GRANT SELECT ON TABLE sample_metric TO agent_readonly;

-- Future tables are NOT readable unless granted explicitly. Without this,
-- a table created later by the migration user could inherit a default
-- privilege and silently become readable.
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM agent_readonly;

-- Belt and braces: this role must never write, even to what it can read.
ALTER ROLE agent_readonly SET default_transaction_read_only = on;
ALTER ROLE agent_readonly SET statement_timeout = '10s';

-- Verify (should return exactly sample_metric):
--   SELECT table_name FROM information_schema.table_privileges
--   WHERE grantee = 'agent_readonly' AND privilege_type = 'SELECT';

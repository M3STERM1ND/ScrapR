-- Least-privilege database roles for ScrapR (`REQ-SEC-006`, `DEC-26`).
--
-- Three roles, because three kinds of access exist:
--
--   scrapr_owner     owns the schema and runs migrations. Used by hand, by an
--                    operator, never by a running process (implementation plan
--                    section 12: migrations are a gated step, not a boot step).
--   scrapr_api       the FastAPI service. Reads and writes rows; cannot change
--                    the schema.
--   scrapr_worker    the worker. Same row access as the API; cannot change the
--                    schema.
--
-- Neither service role can CREATE, ALTER, DROP or TRUNCATE. A compromised
-- process can still read and write rows — that is the job — but it cannot
-- rebuild the database around itself.
--
-- Run once, as a superuser, against the production database, replacing the
-- passwords. Then point DATABASE_URL for each process at its own role.

CREATE ROLE scrapr_owner LOGIN PASSWORD 'replace-me-owner';
CREATE ROLE scrapr_api LOGIN PASSWORD 'replace-me-api';
CREATE ROLE scrapr_worker LOGIN PASSWORD 'replace-me-worker';

-- The schema belongs to the owner; the services may only use it.
ALTER SCHEMA public OWNER TO scrapr_owner;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO scrapr_api, scrapr_worker;

-- Row access on every table the migrations create, now and later. Run the
-- migrations as scrapr_owner so these default privileges apply to new tables.
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO scrapr_api, scrapr_worker;
ALTER DEFAULT PRIVILEGES FOR ROLE scrapr_owner IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO scrapr_api, scrapr_worker;

-- No sequences are used (identifiers are UUIDv7, generated in the application),
-- so none are granted.

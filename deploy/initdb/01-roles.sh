#!/bin/sh
# Runs ONCE, on first database init, as POSTGRES_USER (brand_ops_owner) against
# POSTGRES_DB (brand_ops). Creates the runtime role the app connects as --
# brand_ops_app, NOBYPASSRLS so it can never see across workspaces even if a
# query forgets its filter -- and grants it CRUD on the tables the owner creates
# (the alembic migration), via default privileges so they apply to tables made
# after this script. The app password comes from BRANDOPS_DB_APP_PASSWORD on the
# container environment; it is never committed.
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
CREATE ROLE brand_ops_app LOGIN PASSWORD '${BRANDOPS_DB_APP_PASSWORD}' NOBYPASSRLS;
GRANT brand_ops_app TO brand_ops_owner;
GRANT USAGE ON SCHEMA public TO brand_ops_app;
ALTER DEFAULT PRIVILEGES FOR ROLE brand_ops_owner IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO brand_ops_app;
SQL

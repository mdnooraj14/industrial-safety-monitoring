"""Create the application role and the dev/test databases (idempotent).

Reads PG_ADMIN_USER / PG_ADMIN_PASSWORD (superuser) and DB_* from .env. The admin password is used only here.
Usage:  python scripts/create_databases.py
"""
import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg import sql

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

host = os.getenv("DB_HOST", "localhost")
port = int(os.getenv("DB_PORT", "5432"))
admin_user = os.getenv("PG_ADMIN_USER", "postgres")
admin_pw = os.getenv("PG_ADMIN_PASSWORD", "")
app_user = os.getenv("DB_USER", "safety_app")
app_pw = os.getenv("DB_PASSWORD", "")
databases = [os.getenv("DB_NAME", "safety_db"), os.getenv("TEST_DB_NAME", "safety_test")]

if not admin_pw or not app_pw:
    sys.exit("Set PG_ADMIN_PASSWORD and DB_PASSWORD in .env first.")

with psycopg.connect(host=host, port=port, user=admin_user, password=admin_pw,
                     dbname="postgres", autocommit=True) as conn:
    print("Server:", conn.execute("select version()").fetchone()[0])
    exists = conn.execute("select 1 from pg_roles where rolname=%s", (app_user,)).fetchone()
    stmt = "alter role {} with login password {}" if exists else "create role {} with login password {}"
    conn.execute(sql.SQL(stmt).format(sql.Identifier(app_user), sql.Literal(app_pw)))
    print(("Updated" if exists else "Created"), "role", app_user)
    for db in databases:
        if conn.execute("select 1 from pg_database where datname=%s", (db,)).fetchone():
            print("Database exists:", db)
        else:
            conn.execute(sql.SQL("create database {} owner {}").format(sql.Identifier(db), sql.Identifier(app_user)))
            print("Created database", db)
print("Done.")

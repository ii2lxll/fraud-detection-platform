"""
Database connection helper. Every script that talks to PostgreSQL uses get_engine().

Connection details come from the .env file (never hardcoded, never committed to Git).
"""
import os

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import URL, Engine


def get_engine() -> Engine:
    """Create a SQLAlchemy engine for the project database, using settings from .env."""
    load_dotenv()                                  # reads .env in the folder you run the command from
    url = URL.create(
        drivername="postgresql+psycopg",           # SQLAlchemy dialect + the psycopg driver
        username=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=int(os.environ.get("POSTGRES_PORT", 5433)),
        database=os.environ["POSTGRES_DB"],
    )
    return create_engine(url, pool_pre_ping=True)  # pre_ping: check the connection is alive before using it

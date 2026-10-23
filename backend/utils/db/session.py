"""
Shared asynchronous database session and engine setup.
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine
from utils.setup_env import (
    POSTGRES_USER,
    POSTGRES_PASSWORD,
    POSTGRES_HOST,
    POSTGRES_PORT,
    POSTGRES_DB_NAME,
)


DATABASE_URL = (
    f"postgresql+asyncpg://{POSTGRES_USER}:{POSTGRES_PASSWORD}"
    f"@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB_NAME}"
)
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_pre_ping=True,          # Test connections before using them
    pool_recycle=300,            # Force recycle connections every 5 minutes
    pool_size=20,                # Base pool size
    max_overflow=40,             # Max overflow connections
)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)



"""Create current API schema for a fresh standalone database. Not an upgrade tool."""
import asyncio
import importlib
import pkgutil

from sqlmodel import SQLModel
from utils import models
from utils.db.session import engine
from utils.setup_env import MODE


async def main():
    if MODE != "STANDALONE":
        raise SystemExit("Bootstrap is restricted to MODE=STANDALONE fresh installations")
    for module in pkgutil.iter_modules(models.__path__):
        importlib.import_module(f"utils.models.{module.name}")
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    await engine.dispose()
    print(f"Standalone API schema ready ({len(SQLModel.metadata.tables)} tables)")


if __name__ == "__main__":
    asyncio.run(main())

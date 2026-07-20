"""
core/database.py — SQLAlchemy async engine, Base declarativo e dependency get_session.

Uso nos routers:
  from core.database import get_session
  from sqlalchemy.ext.asyncio import AsyncSession

  @router.post("/")
  async def meu_endpoint(db: AsyncSession = Depends(get_session)):
      ...

A transação é gerenciada pelo get_session: commit automático no sucesso,
rollback automático em exceção. Os services só precisam chamar flush().
"""
from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from core.settings import settings

engine = create_async_engine(
    settings.database_url,
    echo=settings.environment == "development",
    pool_pre_ping=True,
    # statement_cache_size=0: obrigatório com Supabase transaction pooler
    # (Supavisor/PgBouncer) — a conexão física é partilhada entre requisições,
    # e o cache de prepared statements do asyncpg causa "prepared statement
    # already exists" nesse modo.
    connect_args={"statement_cache_size": 0},
)

AsyncSessionFactory = async_sessionmaker(
    engine,
    expire_on_commit=False,   # evita MissingGreenlet ao acessar atributos pós-commit
    class_=AsyncSession,
)


class Base(DeclarativeBase):
    pass


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — injeta AsyncSession com commit/rollback automático."""
    async with AsyncSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise

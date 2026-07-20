"""
alembic/env.py — Configuração de migrações assíncronas com asyncpg.

Substitui o env.py padrão (síncrono) gerado pelo `alembic init`.
Usa create_async_engine + asyncio.run() conforme recomendação oficial do SQLAlchemy 2.0.

Os imports de models abaixo são obrigatórios para que o Alembic detecte as tabelas
ao gerar migrações com --autogenerate.
"""
from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy.ext.asyncio import create_async_engine

import models.message  # noqa: F401

# Importa todos os models para que o Base.metadata os contenha
import models.session  # noqa: F401
from alembic import context
from core.database import Base
from core.settings import settings

# Configuração de logging do alembic.ini
config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Modo offline: gera SQL sem conectar ao banco."""
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Modo online: conecta ao banco via engine assíncrono."""
    # statement_cache_size=0: mesmo motivo do core/database.py — obrigatório
    # com Supabase transaction pooler (Supavisor/PgBouncer).
    connectable = create_async_engine(
        settings.database_url, connect_args={"statement_cache_size": 0}
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

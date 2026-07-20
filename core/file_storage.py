"""
core/file_storage.py — Gerenciamento de arquivos temporários gerados pelo Nicodemus.

Salva .xlsx e .docx em disco com TTL configurável (default: 15 min).
Em produção com múltiplas instâncias, substitua por S3 + pre-signed URLs.

Convenção de nome: {school_id}_{uuid4}.{ext}
"""
from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

import structlog

from core.settings import settings

logger = structlog.get_logger(__name__)

_registry: dict[str, tuple[str, float]] = {}  # file_id → (path, expires_at)


def _storage_dir() -> Path:
    d = Path(settings.file_storage_dir)
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_file(school_id: str, content: bytes, extension: str) -> str:
    """
    Salva arquivo e retorna file_id para download posterior.
    Remove arquivos expirados do registry ao mesmo tempo (lazy GC).

    Args:
        school_id: ID da escola — prefixo do nome do arquivo.
        content:   Bytes do arquivo (.xlsx ou .docx).
        extension: "xlsx" ou "docx".

    Returns:
        file_id (str) — opaco, use em GET /report/download/{file_id}
    """
    _gc_expired()

    file_id = str(uuid.uuid4())
    filename = f"{school_id}_{file_id}.{extension}"
    path = _storage_dir() / filename
    path.write_bytes(content)

    expires_at = time.time() + settings.file_storage_ttl
    _registry[file_id] = (str(path), expires_at)

    logger.info(
        "file_storage.saved", file_id=file_id, path=str(path), ttl=settings.file_storage_ttl
    )
    return file_id


def get_file(file_id: str) -> tuple[bytes, str] | None:
    """
    Retorna (bytes, extension) ou None se expirado/inexistente.
    """
    entry = _registry.get(file_id)
    if not entry:
        return None

    path_str, expires_at = entry
    if time.time() > expires_at:
        _delete_entry(file_id, path_str)
        return None

    path = Path(path_str)
    if not path.exists():
        _registry.pop(file_id, None)
        return None

    extension = path.suffix.lstrip(".")
    return path.read_bytes(), extension


def _gc_expired() -> None:
    now = time.time()
    expired = [fid for fid, (p, exp) in _registry.items() if now > exp]
    for fid in expired:
        path_str, _ = _registry.pop(fid)
        _delete_entry(fid, path_str)


def _delete_entry(file_id: str, path_str: str) -> None:
    try:
        os.remove(path_str)
        logger.info("file_storage.deleted", file_id=file_id)
    except FileNotFoundError:
        pass
    _registry.pop(file_id, None)

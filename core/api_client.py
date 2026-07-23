"""
core/api_client.py — Cliente HTTP assíncrono para a Eleve API.

Mesmo padrão do eleve-agent. Usa ServiceKey do ServiceAccount da escola.
Sempre use como context manager para fechar a conexão corretamente.

Exemplo:
    async with DjangoAPIClient(token=user.sa_token) as client:
        result = await client.get("/api/v1/requests/")
"""
from __future__ import annotations

import asyncio

import httpx
import structlog

from core.settings import settings

logger = structlog.get_logger(__name__)

# GET é idempotente — pode reter com segurança em erro 5xx transitório.
# POST/PATCH não retêm em nível de resposta (poderiam duplicar efeitos);
# retêm apenas em nível de transporte (erro de conexão, antes do request chegar ao servidor).
_RETRYABLE_STATUS = {502, 503, 504}


class DjangoAPIClient:
    def __init__(self, token: str, timeout: float = 20.0, max_retries: int = 2):
        self._token = token
        self._timeout = timeout
        self._max_retries = max_retries
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> DjangoAPIClient:
        transport = httpx.AsyncHTTPTransport(retries=self._max_retries)
        self._client = httpx.AsyncClient(
            base_url=settings.eleve_api_url,
            headers={"Authorization": f"ServiceKey {self._token}"},
            timeout=self._timeout,
            transport=transport,
        )
        return self

    async def __aexit__(self, *_) -> None:
        if self._client:
            await self._client.aclose()

    async def get(self, path: str, **kwargs) -> dict | list:
        attempt = 0
        while True:
            resp = await self._client.get(path, **kwargs)
            if resp.status_code in _RETRYABLE_STATUS and attempt < self._max_retries:
                attempt += 1
                delay = 0.5 * 2**attempt
                logger.warning(
                    "eleve_api.retry", path=path, status=resp.status_code, attempt=attempt
                )
                await asyncio.sleep(delay)
                continue
            resp.raise_for_status()
            return resp.json()

    async def post(self, path: str, json: dict, **kwargs) -> dict:
        resp = await self._client.post(path, json=json, **kwargs)
        resp.raise_for_status()
        return resp.json()

    async def patch(self, path: str, json: dict, **kwargs) -> dict:
        resp = await self._client.patch(path, json=json, **kwargs)
        resp.raise_for_status()
        return resp.json()

    async def post_multipart(
        self,
        path: str,
        files: dict,
        data: dict | list[tuple[str, str]] | None = None,
        **kwargs,
    ) -> dict:
        """
        POST multipart/form-data. `data` aceita dict OU lista de tuplas —
        use lista de tuplas para campos repetidos (ex: várias tags),
        já que um dict só guarda um valor por chave.
        """
        resp = await self._client.post(path, files=files, data=data or [], **kwargs)
        resp.raise_for_status()
        return resp.json()

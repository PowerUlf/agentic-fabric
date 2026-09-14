"""Direct Fabric REST access.

The Fabric API has three behaviours every caller has to handle, and the docs are
explicit that hand-rolled automation gets them wrong: paginated lists, long-running
operations, and throttling. They are handled once, here.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from afabric.kernel.config import Settings

_MAX_RETRIES = 5
_LRO_POLL_SECONDS = 2.0
_LRO_TIMEOUT_SECONDS = 300.0


class FabricApiError(RuntimeError):
    def __init__(self, status: int, body: str, url: str) -> None:
        super().__init__(f"Fabric API {status} for {url}: {body[:500]}")
        self.status = status
        self.body = body
        self.url = url


class RestClient:
    """Thin async client over the Fabric REST API."""

    def __init__(self, token: str, settings: Settings) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.api_base,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=60.0,
        )

    async def __aenter__(self) -> RestClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def request(
        self, method: str, path: str, *, json: dict | None = None, params: dict | None = None
    ) -> httpx.Response:
        """One request, retrying on throttling and transient server errors."""
        for attempt in range(_MAX_RETRIES):
            response = await self._client.request(method, path, json=json, params=params)

            if response.status_code == 429 or response.status_code >= 500:
                if attempt == _MAX_RETRIES - 1:
                    raise FabricApiError(response.status_code, response.text, path)
                # Honour Retry-After when the service sends it; back off otherwise.
                delay = float(response.headers.get("Retry-After", 2**attempt))
                await asyncio.sleep(delay)
                continue

            if response.status_code >= 400:
                raise FabricApiError(response.status_code, response.text, path)

            return response

        raise AssertionError("unreachable")

    async def get_all(
        self, path: str, *, collection: str = "value", params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """GET a paginated collection, following continuation to the end.

        `params` are the caller's query arguments; they ride along on every page, since
        dropping them after the first would page through a differently filtered list.
        """
        items: list[dict[str, Any]] = []
        query = dict(params or {})

        while True:
            response = await self.request("GET", path, params=query or None)
            body = response.json()
            items.extend(body.get(collection, []))

            token = body.get("continuationToken")
            if not token:
                return items
            query = {**(params or {}), "continuationToken": token}

    async def get_one(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = await self.request("GET", path, params=params or None)
        return response.json()

    async def await_operation(self, operation_id: str) -> dict[str, Any] | None:
        """Poll a long-running operation until it settles.

        Returns the operation result, or None when the operation produced no body.
        """
        waited = 0.0
        while waited < _LRO_TIMEOUT_SECONDS:
            state = await self.get_one(f"/operations/{operation_id}")
            status = state.get("status")

            if status == "Succeeded":
                response = await self._client.get(f"/operations/{operation_id}/result")
                if response.status_code == 204 or not response.content:
                    return None
                # Operations that produce nothing (assignToCapacity) are documented to
                # answer /result with this error rather than an empty body; that is
                # success, not failure. Unverified against the live tenant — no operation
                # has come back asynchronously yet. Fail-safe either way: an unexpected
                # error raises rather than being mistaken for a result.
                if response.status_code >= 400:
                    if "OperationHasNoResult" in response.text:
                        return None
                    raise FabricApiError(response.status_code, response.text, operation_id)
                return response.json()

            if status == "Failed":
                raise FabricApiError(500, str(state.get("error", state)), operation_id)

            await asyncio.sleep(_LRO_POLL_SECONDS)
            waited += _LRO_POLL_SECONDS

        raise TimeoutError(
            f"operation {operation_id} did not settle within {_LRO_TIMEOUT_SECONDS:.0f}s"
        )

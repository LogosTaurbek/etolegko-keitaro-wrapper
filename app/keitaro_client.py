import asyncio
import json
import logging
from typing import Any, Dict, List, Optional
import httpx
from app.config import settings

logger = logging.getLogger("keitaro_client")


class KeitaroAPIError(Exception):
    def __init__(self, message: str, status_code: Optional[int] = None, response_text: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text


class KeitaroClient:
    def __init__(self):
        self.base_url = settings.KEITARO_BASE_URL.rstrip("/") + "/admin_api/v1"
        self.headers = {"Api-Key": settings.KEITARO_API_KEY}
        self.client: Optional[httpx.AsyncClient] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    async def init_client(self):
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if self.client is not None and (self.client.is_closed or self._loop is not current_loop):
            if not self.client.is_closed and self._loop is current_loop:
                await self.client.aclose()
            self.client = None
            self._loop = None

        if self.client is None:
            self.base_url = settings.KEITARO_BASE_URL.rstrip("/") + "/admin_api/v1"
            self.headers = {"Api-Key": settings.KEITARO_API_KEY}
            self.client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=self.headers,
                timeout=15.0,
            )
            self._loop = current_loop

    async def close(self):
        if self.client is not None:
            try:
                current_loop = asyncio.get_running_loop()
            except RuntimeError:
                current_loop = None

            if not self.client.is_closed and (self._loop is None or self._loop is current_loop):
                await self.client.aclose()
            self.client = None
        self._loop = None

    async def _request(self, method: str, endpoint: str, **kwargs) -> Any:
        await self.init_client()
        url = endpoint if endpoint.startswith("/") else f"/{endpoint}"
        payload_preview = ""
        if "json" in kwargs and kwargs["json"] is not None:
            try:
                payload_preview = json.dumps(kwargs["json"], ensure_ascii=False)
            except TypeError:
                payload_preview = str(kwargs["json"])

        try:
            response = await self.client.request(method, url, **kwargs)
            if response.status_code >= 400:
                logger.error(
                    "Keitaro API error %s on %s %s | payload=%s | response=%s",
                    response.status_code,
                    method,
                    url,
                    payload_preview,
                    response.text,
                )
                raise KeitaroAPIError(
                    f"Keitaro API error: {response.status_code} ({response.text[:200]})",
                    status_code=response.status_code,
                    response_text=response.text,
                )
            if response.status_code == 204 or not response.content:
                return None
            return response.json()
        except httpx.RequestError as exc:
            logger.error("Keitaro request connection error on %s %s: %s", method, url, exc)
            raise KeitaroAPIError(f"Keitaro connection error: {exc}")

    async def get_domains(self) -> List[Dict[str, Any]]:
        return await self._request("GET", "/domains")

    async def get_groups(self, type_param: str = "campaigns") -> List[Dict[str, Any]]:
        return await self._request("GET", "/groups", params={"type": type_param})

    async def get_traffic_sources(self) -> List[Dict[str, Any]]:
        return await self._request("GET", "/traffic_sources")

    async def get_offers(self) -> List[Dict[str, Any]]:
        return await self._request("GET", "/offers")

    async def get_campaigns(self) -> List[Dict[str, Any]]:
        return await self._request("GET", "/campaigns")

    async def create_campaign(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return await self._request("POST", "/campaigns", json=data)

    async def get_campaign_streams(self, campaign_id: int) -> List[Dict[str, Any]]:
        return await self._request("GET", f"/campaigns/{campaign_id}/streams")

    async def get_stream(self, stream_id: int) -> Dict[str, Any]:
        return await self._request("GET", f"/streams/{stream_id}")

    async def create_stream(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return await self._request("POST", "/streams", json=data)

    async def update_stream(self, stream_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
        return await self._request("PUT", f"/streams/{stream_id}", json=data)


keitaro_client = KeitaroClient()

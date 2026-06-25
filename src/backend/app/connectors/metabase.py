from __future__ import annotations

from typing import Any

import httpx

from ..config import Settings


class MetabaseConnector:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._session: str | None = None

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(f"{self.settings.metabase_url}/api/health")
                return response.status_code == 200
        except Exception:
            return False

    async def dashboards(self) -> list[dict[str, Any]]:
        headers: dict[str, str] = {}
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"{self.settings.metabase_url}/api/dashboard", headers=headers
            )
            if response.status_code == 401:
                headers = await self._login(client)
                if headers:
                    response = await client.get(
                        f"{self.settings.metabase_url}/api/dashboard",
                        headers=headers,
                    )
            if response.status_code != 200:
                return []
            result = []
            for dashboard in response.json():
                public_uuid = dashboard.get("public_uuid")
                result.append(
                    {
                        "id": dashboard.get("id"),
                        "name": dashboard.get("name"),
                        "description": dashboard.get("description"),
                        "public_uuid": public_uuid,
                        "embed_url": (
                            f"{self.settings.metabase_public_url}/public/dashboard/{public_uuid}"
                            if public_uuid
                            else None
                        ),
                    }
                )
            return result

    async def _login(self, client: httpx.AsyncClient) -> dict[str, str]:
        if self._session:
            return {"X-Metabase-Session": self._session}
        if (
            not self.settings.metabase_admin_email
            or not self.settings.metabase_admin_password
        ):
            return {}
        response = await client.post(
            f"{self.settings.metabase_url}/api/session",
            json={
                "username": self.settings.metabase_admin_email,
                "password": self.settings.metabase_admin_password,
            },
        )
        if response.status_code != 200:
            return {}
        self._session = str(response.json().get("id") or "")
        return {"X-Metabase-Session": self._session} if self._session else {}

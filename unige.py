from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import requests
from dotenv import load_dotenv
from requests.auth import HTTPBasicAuth


@dataclass
class UnigeToken:
    value: str
    expires_at: datetime

    def is_valid(self) -> bool:
        return datetime.utcnow() < self.expires_at - timedelta(seconds=60)


class UnigeClient:
    AUTH_URL = "https://webservices.unige.it/v3/auth"
    PERSON_URL_TEMPLATE = "https://webservices.unige.it/v3/persona/{identifier}"
    PERSON_LIST_URL = "https://webservices.unige.it/v3/persona/list"

    def __init__(
        self,
        username: Optional[str] = None,
        password: Optional[str] = None,
        *,
        timeout: int = 30,
        session: Optional[requests.Session] = None,
    ) -> None:
        load_dotenv()

        self.username = username or os.getenv("UNIGE_USERNAME")
        self.password = password or os.getenv("UNIGE_PASSWORD")
        if not self.username or not self.password:
            raise ValueError("UNIGE credentials missing. Set UNIGE_USERNAME and UNIGE_PASSWORD.")

        self.timeout = timeout
        self._session = session or requests.Session()
        self._session.headers.update({"Accept": "application/json"})
        self._token: Optional[UnigeToken] = None
        self._people_index: Optional[Dict[str, Dict[str, Any]]] = None

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "UnigeClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def get_person(self, identifier: str) -> Dict[str, Any]:
        if not identifier:
            raise ValueError("UNIGE identifier is required.")

        self._ensure_token()
        url = self.PERSON_URL_TEMPLATE.format(identifier=identifier)
        try:
            response = self._session.get(
                url,
                headers={"Authorization": f"Bearer {self._token.value}"},
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.HTTPError as exc:
            payload = exc.response.text if exc.response is not None else ""
            raise RuntimeError(
                f"UNIGE persona fetch failed! status={exc.response.status_code if exc.response else 'N/A'} body={payload}"
            ) from exc
        return response.json()

    def _ensure_token(self) -> None:
        if self._token and self._token.is_valid():
            return

        try:
            response = self._session.get(
                self.AUTH_URL,
                auth=HTTPBasicAuth(self.username, self.password),
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.HTTPError as exc:
            payload = exc.response.text if exc.response is not None else ""
            raise RuntimeError(
                f"UNIGE authentication failed! status={exc.response.status_code if exc.response else 'N/A'} body={payload}"
            ) from exc

        data = response.json()

        token = data.get("access_token")
        expires_in = int(data.get("expires_in", 3600))
        if not token:
            raise RuntimeError("UNIGE authentication failed: access_token missing.")

        self._token = UnigeToken(
            value=token,
            expires_at=datetime.utcnow() + timedelta(seconds=expires_in),
        )

    def test_connection(self) -> Dict[str, Any]:
        self._ensure_token()
        return {
            "access_token": self._token.value,
            "expires_at": self._token.expires_at.isoformat(),
        }

    def get_people_overview(self) -> Dict[str, Dict[str, Any]]:
        if self._people_index is not None:
            return self._people_index

        self._ensure_token()
        try:
            response = self._session.get(
                self.PERSON_LIST_URL,
                headers={"Authorization": f"Bearer {self._token.value}"},
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.HTTPError as exc:
            payload = exc.response.text if exc.response is not None else ""
            raise RuntimeError(
                f"UNIGE persona list failed! status={exc.response.status_code if exc.response else 'N/A'} body={payload}"
            ) from exc

        data = response.json()
        if isinstance(data, list):
            people = data
        elif isinstance(data, dict):
            people = next(
                (value for key, value in data.items() if isinstance(value, list)),
                [],
            )
        else:
            people = []

        mapping: Dict[str, Dict[str, Any]] = {}
        for entry in people:
            if not isinstance(entry, dict):
                continue
            identifier = (
                entry.get("matricola")
                or entry.get("Matricola")
                or entry.get("id")
                or entry.get("persona")
            )
            if identifier is None:
                continue
            mapping[str(identifier)] = entry

        self._people_index = mapping
        return mapping


if __name__ == "__main__":  # pragma: no cover
    client = UnigeClient()
    info = client.test_connection()
    print("Token acquired:")
    for key, value in info.items():
        print(f"  {key}: {value}")
    client.close()

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Iterator, List, Optional

import requests


class BlueskyError(RuntimeError):
    """Base class for errors raised when interacting with the Bluesky API."""


class DirectMessageNotSupported(BlueskyError):
    """Raised when the server does not support direct message endpoints."""


@dataclass
class Profile:
    """Minimal representation of a Bluesky profile."""

    did: str
    handle: str
    display_name: Optional[str] = None


class BlueskyClient:
    """Lightweight HTTP client for interacting with the public Bluesky API."""

    def __init__(
        self,
        identifier: str,
        app_password: str,
        service: str = "https://bsky.social",
        *,
        proxy: Optional[str] = None,
        timeout: float = 30.0,
    ) -> None:
        self.identifier = identifier
        self.app_password = app_password
        self.service = service.rstrip("/")
        self.timeout = timeout

        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "bluesky-tool/0.1"})
        if proxy:
            self.session.proxies.update({"http": proxy, "https": proxy})

        self._access_jwt: Optional[str] = None
        self._refresh_jwt: Optional[str] = None
        self._did: Optional[str] = None

    # ------------------------------------------------------------------
    # Session helpers
    # ------------------------------------------------------------------
    def login(self) -> Profile:
        """Authenticate the client and return the profile for the identifier."""

        payload = {"identifier": self.identifier, "password": self.app_password}
        data = self._request(
            "com.atproto.server.createSession",
            method="POST",
            json=payload,
            requires_auth=False,
        )

        self._access_jwt = data.get("accessJwt")
        self._refresh_jwt = data.get("refreshJwt")
        self._did = data.get("did")

        if not self._access_jwt or not self._did:
            raise BlueskyError("Session response did not contain authentication tokens.")

        profile_data = self.get_profile(self.identifier)
        return profile_data

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "BlueskyClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[override]
        self.close()

    # ------------------------------------------------------------------
    # Low level HTTP helpers
    # ------------------------------------------------------------------
    def _auth_headers(self) -> Dict[str, str]:
        if not self._access_jwt:
            raise BlueskyError("Client is not authenticated. Call 'login' first.")
        return {"Authorization": f"Bearer {self._access_jwt}"}

    def _request(
        self,
        endpoint: str,
        *,
        method: str = "GET",
        params: Optional[Dict[str, str]] = None,
        json: Optional[Dict[str, object]] = None,
        requires_auth: bool = True,
    ) -> Dict[str, object]:
        url = f"{self.service}/xrpc/{endpoint}"
        headers: Dict[str, str] = {}
        if requires_auth:
            headers.update(self._auth_headers())

        response = self.session.request(
            method,
            url,
            params=params,
            json=json,
            headers=headers,
            timeout=self.timeout,
        )

        if response.status_code >= 400:
            raise BlueskyError(
                f"Request to {endpoint} failed with status {response.status_code}: {response.text}"
            )

        if not response.content:
            return {}

        try:
            return response.json()
        except ValueError as exc:  # pragma: no cover - defensive branch
            raise BlueskyError(f"Failed to decode response from {endpoint}: {exc}") from exc

    # ------------------------------------------------------------------
    # Public API methods
    # ------------------------------------------------------------------
    @property
    def did(self) -> str:
        if not self._did:
            raise BlueskyError("Client DID is not available before login.")
        return self._did

    def get_profile(self, actor: str) -> Profile:
        data = self._request(
            "app.bsky.actor.getProfile",
            params={"actor": actor},
            requires_auth=False,
        )
        return Profile(
            did=str(data.get("did")),
            handle=str(data.get("handle", actor)),
            display_name=data.get("displayName"),
        )

    def get_followers(self, actor: str, limit: int = 50, cursor: Optional[str] = None) -> Dict[str, object]:
        params: Dict[str, str] = {"actor": actor, "limit": str(limit)}
        if cursor:
            params["cursor"] = cursor
        return self._request("app.bsky.graph.getFollowers", params=params, requires_auth=False)

    def iterate_followers(self, actor: str, page_size: int = 50) -> Iterator[Dict[str, object]]:
        cursor: Optional[str] = None
        while True:
            data = self.get_followers(actor, limit=page_size, cursor=cursor)
            followers: List[Dict[str, object]] = data.get("followers", [])  # type: ignore[assignment]
            for follower in followers:
                yield follower
            cursor = data.get("cursor") if isinstance(data, dict) else None
            if not cursor:
                break

    def follow(self, target_did: str) -> Dict[str, object]:
        record = {
            "subject": target_did,
            "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "$type": "app.bsky.graph.follow",
        }
        payload = {"repo": self.did, "collection": "app.bsky.graph.follow", "record": record}
        return self._request("com.atproto.repo.createRecord", method="POST", json=payload)

    def get_author_feed(self, actor: str, limit: int = 5) -> Dict[str, object]:
        params = {"actor": actor, "limit": str(limit)}
        return self._request("app.bsky.feed.getAuthorFeed", params=params, requires_auth=False)

    def like(self, subject_uri: str, subject_cid: str) -> Dict[str, object]:
        record = {
            "subject": {"uri": subject_uri, "cid": subject_cid},
            "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "$type": "app.bsky.feed.like",
        }
        payload = {"repo": self.did, "collection": "app.bsky.feed.like", "record": record}
        return self._request("com.atproto.repo.createRecord", method="POST", json=payload)

    def list_own_followers(self, limit: int = 50, cursor: Optional[str] = None) -> Dict[str, object]:
        return self.get_followers(self.did, limit=limit, cursor=cursor)

    # ------------------------------------------------------------------
    # Direct messages
    # ------------------------------------------------------------------
    def create_or_get_conversation(self, member_did: str) -> str:
        payload = {"members": [member_did]}
        last_error: Optional[Exception] = None

        for endpoint in (
            "chat.bsky.convo.createOrGet",
            "chat.bsky.convo.getConvoForMembers",
            "chat.bsky.convo.create",
        ):
            try:
                data = self._request(endpoint, method="POST", json=payload)
            except BlueskyError as exc:
                last_error = exc
                continue

            convo = data.get("convo") or data.get("conversation") or data
            if isinstance(convo, dict):
                if "id" in convo:
                    return str(convo["id"])
                if "convoId" in convo:
                    return str(convo["convoId"])
            if "convoId" in data:
                return str(data["convoId"])

        raise DirectMessageNotSupported(
            "The server does not expose a supported endpoint for starting conversations."
        ) from last_error

    def send_direct_message(self, convo_id: str, text: str) -> Dict[str, object]:
        payload = {"convoId": convo_id, "message": {"text": text, "facets": []}}
        try:
            return self._request("chat.bsky.convo.sendMessage", method="POST", json=payload)
        except BlueskyError as exc:
            raise DirectMessageNotSupported(
                "The server rejected the direct message request; verify DM support is enabled."
            ) from exc


__all__ = [
    "BlueskyClient",
    "BlueskyError",
    "DirectMessageNotSupported",
    "Profile",
]

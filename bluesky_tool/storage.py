from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, Set


@dataclass
class TargetState:
    """Stores engagement metadata for a specific follow target."""

    followed: Set[str] = field(default_factory=set)
    liked_posts: Set[str] = field(default_factory=set)

    @classmethod
    def from_dict(cls, data: Dict[str, Iterable[str]]) -> "TargetState":
        return cls(
            followed=set(data.get("followed", [])),
            liked_posts=set(data.get("liked_posts", [])),
        )

    def to_dict(self) -> Dict[str, Iterable[str]]:
        return {
            "followed": sorted(self.followed),
            "liked_posts": sorted(self.liked_posts),
        }


@dataclass
class AccountState:
    """Tracks per-account automation state."""

    known_followers: Set[str] = field(default_factory=set)
    dm_history: Dict[str, str] = field(default_factory=dict)
    targets: Dict[str, TargetState] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "AccountState":
        targets_raw: Dict[str, Dict[str, Iterable[str]]] = data.get("targets", {})  # type: ignore[assignment]
        targets = {name: TargetState.from_dict(value) for name, value in targets_raw.items()}
        return cls(
            known_followers=set(data.get("known_followers", [])),
            dm_history=dict(data.get("dm_history", {})),
            targets=targets,
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "known_followers": sorted(self.known_followers),
            "dm_history": self.dm_history,
            "targets": {name: target.to_dict() for name, target in self.targets.items()},
        }

    def target(self, handle: str) -> TargetState:
        if handle not in self.targets:
            self.targets[handle] = TargetState()
        return self.targets[handle]


class StateStore:
    """Simple JSON-backed persistence for automation state."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path_for(self, handle: str) -> Path:
        safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", handle)
        return self.root / f"{safe}.json"

    def load(self, handle: str) -> AccountState:
        path = self._path_for(handle)
        if not path.exists():
            return AccountState()
        with path.open("r", encoding="utf-8") as handle_stream:
            raw = json.load(handle_stream)
        return AccountState.from_dict(raw)

    def save(self, handle: str, state: AccountState) -> None:
        path = self._path_for(handle)
        with path.open("w", encoding="utf-8") as handle_stream:
            json.dump(state.to_dict(), handle_stream, ensure_ascii=False, indent=2)


__all__ = ["AccountState", "StateStore", "TargetState"]

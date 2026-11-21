"""Active restrictions, persisted to a JSON file so timers survive restarts."""

from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class Restriction:
    kind: str  # "pause" | "slow" | "sites"
    target: str  # the device/group name the user picked
    macs: list[str]
    until: str | None  # ISO timestamp, None = until undone
    label: str = ""  # speed preset or site list name
    # Data needed to undo: previous usergroup per mac, traffic rule id, ...
    undo: dict = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    created: str = ""

    def until_dt(self) -> datetime | None:
        return datetime.fromisoformat(self.until) if self.until else None


class State:
    def __init__(self, path: str):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.items: list[Restriction] = []
        if self.path.exists():
            raw = json.loads(self.path.read_text() or "[]")
            self.items = [Restriction(**r) for r in raw]

    def save(self) -> None:
        with self.lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps([asdict(r) for r in self.items], indent=2))
            os.replace(tmp, self.path)

    def add(self, r: Restriction) -> None:
        with self.lock:
            self.items.append(r)
            self.save()

    def remove(self, r: Restriction) -> None:
        with self.lock:
            self.items = [x for x in self.items if x.id != r.id]
            self.save()

    def find(self, kind: str | None = None, mac: str | None = None) -> list[Restriction]:
        with self.lock:
            return [
                r for r in self.items
                if (kind is None or r.kind == kind) and (mac is None or mac in r.macs)
            ]

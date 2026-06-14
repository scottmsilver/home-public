# homed/model.py
from dataclasses import dataclass

VALID_KINDS = {"toggle", "momentary", "slider", "speed", "readout"}


@dataclass
class Control:
    domain: str
    id: str
    name: str
    kind: str
    on: bool | None = None
    value: float | None = None
    range: tuple | None = None
    status: str | None = None
    online: bool = True

    def to_dict(self) -> dict:
        return {
            "domain": self.domain,
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "on": self.on,
            "value": self.value,
            "range": list(self.range) if self.range is not None else None,
            "status": self.status,
            "online": self.online,
        }

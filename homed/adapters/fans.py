# homed/adapters/fans.py
from homed.adapters.base import Adapter
from homed.model import Control


def _shared(values):
    """Single agreed value among 'on' devices, else None."""
    s = set(values)
    return values[0] if len(s) == 1 else None


class FansAdapter(Adapter):
    domain = "fans"

    def snapshot(self):
        data = self.get_json("/api/fans")
        fans = data.get("fans", [])
        heaters = data.get("heaters", [])
        out = []

        # All Fans (speed)
        on_fans = [f for f in fans if f.get("state", {}).get("fanOn")]
        speeds = [f["state"].get("fanSpeed") for f in on_fans if f["state"].get("fanSpeed")]
        out.append(
            Control(
                domain="fans",
                id="fans",
                name="All Fans",
                kind="speed",
                on=bool(on_fans),
                value=_shared(speeds) if speeds else None,
                range=(1, 6),
                status=f"{len(on_fans)} of {len(fans)}" if fans else None,
                online=any(f.get("online") for f in fans) if fans else False,
            )
        )

        # All Lights (slider)
        on_lights = [f for f in fans if f.get("state", {}).get("lightOn")]
        brights = [f["state"].get("lightBrightness") for f in on_lights if f["state"].get("lightBrightness")]
        out.append(
            Control(
                domain="fans",
                id="lights",
                name="All Lights",
                kind="slider",
                on=bool(on_lights),
                value=_shared(brights) if brights else None,
                range=(1, 100),
                status=f"{len(on_lights)} of {len(fans)}" if fans else None,
                online=any(f.get("online") for f in fans) if fans else False,
            )
        )

        # All Heaters (slider) — only if present
        if heaters:
            on_h = [h for h in heaters if h.get("state", {}).get("on")]
            levels = [h["state"].get("level") for h in on_h if h["state"].get("level")]
            out.append(
                Control(
                    domain="fans",
                    id="heaters",
                    name="All Heaters",
                    kind="slider",
                    on=bool(on_h),
                    value=_shared(levels) if levels else None,
                    range=(1, 100),
                    status=f"{len(on_h)} of {len(heaters)}",
                    online=any(h.get("online") for h in heaters),
                )
            )
        return out

    def command(self, control_id, payload):  # filled in Task 6
        raise NotImplementedError

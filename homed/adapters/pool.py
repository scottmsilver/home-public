# homed/adapters/pool.py
from homed.adapters.base import Adapter
from homed.model import Control


class PoolAdapter(Adapter):
    domain = "pool"

    def snapshot(self):
        data = self.get_json("/api/pool")
        out = []

        spa = data.get("spa")
        if spa is not None:
            prog = spa.get("spa_heat_progress") or {}
            if prog.get("active") and prog.get("target_temp_f"):
                mins = prog.get("minutes_remaining")
                status = f"Heating → {prog['target_temp_f']}°" + (f" ({mins}m)" if mins else "")
            else:
                status = "On" if spa.get("on") else "Off"
            out.append(
                Control(
                    domain="pool",
                    id="spa",
                    name="Spa",
                    kind="toggle",
                    on=bool(spa.get("on")),
                    value=spa.get("temperature"),
                    status=status,
                )
            )

        pool = data.get("pool")
        if pool is not None:
            out.append(
                Control(
                    domain="pool",
                    id="pool",
                    name="Pool",
                    kind="toggle",
                    on=bool(pool.get("on")),
                    value=pool.get("temperature"),
                    status="On" if pool.get("on") else "Off",
                )
            )

        lights = data.get("lights")
        if lights is not None:
            out.append(Control(domain="pool", id="lights", name="Pool Light", kind="toggle", on=bool(lights.get("on"))))

        for aux in data.get("auxiliaries", []):
            out.append(
                Control(
                    domain="pool", id=aux["id"], name=aux.get("name", aux["id"]), kind="toggle", on=bool(aux.get("on"))
                )
            )
        return out

    def command(self, control_id, payload):  # filled in Task 9
        raise NotImplementedError

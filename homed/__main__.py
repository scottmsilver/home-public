# homed/__main__.py
import argparse
import logging
from pathlib import Path

from homed.adapters.fans import FansAdapter
from homed.adapters.gate import GateAdapter
from homed.adapters.pool import PoolAdapter
from homed.aggregator import Aggregator
from homed.config import load_config
from homed.server import create_app


def build(cfg):
    b = cfg.backends
    adapters = {
        "fans": FansAdapter(b["fans"]["base_url"]),
        "pool": PoolAdapter(b["pool"]["base_url"]),
        "gate": GateAdapter(
            b["gate"]["base_url"], headers={"X-Verified-User": b["gate"].get("service_user", "home@local")}
        ),
    }
    agg = Aggregator(adapters)
    agg.refresh_all()
    agg.start()
    return create_app(agg, home_rows=cfg.home_rows, web=cfg.web)


def main():
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="home.toml")
    args = ap.parse_args()
    cfg = load_config(Path(args.config))
    app = build(cfg)
    bind = cfg.web.get("bind", "0.0.0.0:8099")
    host, _, port = bind.rpartition(":")
    app.run(host=host or "0.0.0.0", port=int(port), threaded=True)


if __name__ == "__main__":
    main()

# homed/aggregator.py
import queue
import threading


class Aggregator:
    def __init__(self, adapters: dict):
        self.adapters = adapters
        self._cache = {d: [] for d in adapters}  # domain -> list[Control]
        self._lock = threading.Lock()
        self._subscribers = set()  # set[queue.Queue]

    # ── snapshots ────────────────────────────────────────────────
    def refresh_domain(self, domain):
        controls = self.adapters[domain].snapshot()
        with self._lock:
            self._cache[domain] = controls
        self._notify()

    def refresh_all(self):
        for d in self.adapters:
            try:
                self.refresh_domain(d)
            except Exception:
                pass

    def state(self) -> dict:
        with self._lock:
            controls = [c.to_dict() for cs in self._cache.values() for c in cs]
        return {"controls": controls}

    # ── commands ─────────────────────────────────────────────────
    def dispatch(self, domain, control_id, payload):
        self.adapters[domain].command(control_id, payload)  # KeyError if unknown
        try:
            self.refresh_domain(domain)
        except Exception:
            pass

    # ── pub/sub for SSE ──────────────────────────────────────────
    def subscribe(self) -> queue.Queue:
        # Lifecycle: caller MUST call unsubscribe(q) (e.g. in a finally) when the
        # consumer disconnects, or dead queues accumulate.
        q = queue.Queue(maxsize=8)
        with self._lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            self._subscribers.discard(q)

    def _notify(self):
        payload = self.state()
        with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(payload)
            except queue.Full:
                pass

    # ── background updaters ──────────────────────────────────────
    def start(self):
        for d, adapter in self.adapters.items():
            adapter.start(lambda d=d: self._safe_refresh(d))

    def _safe_refresh(self, domain):
        try:
            self.refresh_domain(domain)
        except Exception:
            pass

"""HTTP API that the iOS Shortcut (and the small web page) talk to."""

from __future__ import annotations

import hmac
import logging
import threading
from contextlib import asynccontextmanager
from importlib import resources

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .config import Config, load_config
from .service import ACTIONS, DURATIONS, FamilyWifi
from .state import State
from .unifi import UnifiClient, UnifiError

log = logging.getLogger("family_wifi")


class DoRequest(BaseModel):
    action: str
    target: str | None = None
    duration: str | None = None
    option: str | None = None  # speed preset, website list, or kind to restore


def create_app(cfg: Config | None = None, service: FamilyWifi | None = None,
               tick_seconds: float = 15) -> FastAPI:
    cfg = cfg or load_config()
    svc = service or FamilyWifi(cfg, UnifiClient(cfg.unifi), State(cfg.state_file))
    stop = threading.Event()

    def ticker():
        while not stop.wait(tick_seconds):
            try:
                svc.expire()
            except Exception:
                log.exception("timer check failed")

    @asynccontextmanager
    async def lifespan(_app):
        svc.expire()  # catch up on timers that ended while we were down
        t = threading.Thread(target=ticker, daemon=True, name="timers")
        t.start()
        yield
        stop.set()

    app = FastAPI(title="Family WiFi", lifespan=lifespan)

    def auth(authorization: str = Header(default="")):
        token = authorization.removeprefix("Bearer ").strip()
        if not hmac.compare_digest(token.encode(), cfg.api_token.encode()):
            raise HTTPException(401, "Wrong or missing token")

    def run(fn, *a, **kw) -> dict:
        try:
            return {"ok": True, "message": fn(*a, **kw)}
        except (KeyError, ValueError) as e:
            raise HTTPException(400, str(e).strip("'\""))
        except UnifiError as e:
            raise HTTPException(502, f"UniFi said: {e}")

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/menu", dependencies=[Depends(auth)])
    def menu():
        """Everything the Shortcut needs to build its pick lists."""
        return {
            "actions": list(ACTIONS.values()),
            "targets": cfg.targets(),
            "durations": DURATIONS,
            "speeds": [s.name for s in cfg.speeds],
            "site_lists": list(cfg.site_lists),
        }

    @app.post("/do", dependencies=[Depends(auth)])
    def do(req: DoRequest):
        return run(svc.do, req.action, req.target, req.duration, req.option)

    @app.get("/status", dependencies=[Depends(auth)])
    def status():
        return run(svc.status)

    @app.get("/", response_class=HTMLResponse)
    def page():
        return resources.files("family_wifi").joinpath("static/index.html").read_text()

    return app

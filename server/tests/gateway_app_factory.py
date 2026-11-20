"""Throwaway FastAPI app builder for Gateway tests.

The factory accepts explicit ``GatewayPorts`` so report failures, ``None``
returns, and error mapping can be injected without mutating module-global
runtime state (design spec §7).
"""

from fastapi import FastAPI

from server.presentation.gateway.ports import GatewayPorts
from server.presentation.gateway.routes import create_router, install_error_handlers


def make_gateway_app(ports: GatewayPorts) -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(create_router(ports), prefix="/api/v1")
    return app


__all__ = ["make_gateway_app"]

from __future__ import annotations

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import keys_router, clients_router, messages_router, groups_router, health_router
from app.ws import ws_router


def create_app() -> FastAPI:
    app = FastAPI(title="ChatSeguro API", version="0.1")

    # CORS - local
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Rotas REST
    app.include_router(health_router)
    app.include_router(keys_router)
    app.include_router(clients_router)
    app.include_router(messages_router)
    app.include_router(groups_router)

    # WebSocket
    app.include_router(ws_router)

    return app


app = create_app()

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
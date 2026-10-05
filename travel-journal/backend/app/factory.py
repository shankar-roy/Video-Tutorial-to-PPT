from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.db import init_database, make_engine, make_session_factory
from app.gmail import GmailService
from app.routes import router
from app.security import live_apple_verifier
from app.settings import Settings


def create_app(settings: Settings | None = None, apple_verifier=None, gmail=None, llm=None) -> FastAPI:
    settings = settings or Settings()
    if settings.app_env == "production" and settings.secret_key == "dev-secret-change-me":
        raise RuntimeError("Set SECRET_KEY before running in production")
    app = FastAPI(title="Travel Journal")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.settings = settings
    app.state.apple = apple_verifier or live_apple_verifier(settings.apple_client_id)
    app.state.gmail = gmail or GmailService(settings)
    app.state.llm = llm
    engine = make_engine(settings.database_url)
    init_database(engine)
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    Path(settings.media_root).mkdir(parents=True, exist_ok=True)
    app.include_router(router)
    return app

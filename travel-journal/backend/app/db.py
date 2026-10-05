from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from app.models import Base


def make_engine(database_url: str):
    kwargs = {}
    if database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    engine = create_engine(database_url, **kwargs)
    if database_url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _enable_foreign_keys(dbapi_connection, _record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def make_session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_database(engine) -> None:
    Base.metadata.create_all(engine)
    if str(engine.url).startswith("sqlite"):
        with engine.connect() as connection:
            connection.execute(text("PRAGMA foreign_keys=ON"))

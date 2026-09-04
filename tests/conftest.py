from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from xhs_manager.api import create_app
from xhs_manager.config import Settings
from xhs_manager.db import create_db_engine, create_session_factory
from xhs_manager.models import Base


@pytest.fixture
def engine(tmp_path) -> Iterator[Engine]:
    database_path = tmp_path / "test.db"
    engine = create_db_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker:
    return create_session_factory(engine)


@pytest.fixture
def session(session_factory: sessionmaker) -> Iterator[Session]:
    value = session_factory()
    try:
        yield value
        value.rollback()
    finally:
        value.close()


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        env="test",
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        internal_api_token="test-token",
        feishu_verification_token="verify-token",
        feishu_allowed_user_ids=["operator-1"],
        publishing_enabled=False,
        comments_enabled=False,
    )


@pytest.fixture
def client(engine: Engine, settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings=settings, engine=engine)
    with TestClient(app) as value:
        yield value


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"X-Internal-Token": "test-token"}

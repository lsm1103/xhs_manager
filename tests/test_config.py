import pytest
from pydantic import ValidationError

from xhs_manager.config import Settings


def test_production_requires_internal_and_feishu_tokens():
    with pytest.raises(ValidationError):
        Settings(env="production")


def test_current_milestone_refuses_real_platform_actions():
    with pytest.raises(ValidationError):
        Settings(publishing_enabled=True)
    with pytest.raises(ValidationError):
        Settings(comments_enabled=True)

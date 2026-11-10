import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from fake_controller import FakeController  # noqa: E402


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture(autouse=True)
def auto_enable_sockets(socket_enabled):
    yield


@pytest.fixture
async def controller():
    fake = FakeController()
    await fake.start(8586)
    yield fake
    await fake.stop()

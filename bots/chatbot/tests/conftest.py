import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from chatbot.config import load_client  # noqa: E402
from chatbot.notify import LogNotifier  # noqa: E402
from chatbot.store import Store  # noqa: E402


@pytest.fixture
def cfg():
    return load_client("demo-salon", ROOT / "clients")


@pytest.fixture
def store(tmp_path, cfg):
    s = Store(cfg.slug, tmp_path)
    yield s
    s.close()


@pytest.fixture
def notifier():
    return LogNotifier()


def text_block(text):
    return SimpleNamespace(type="text", text=text)


def tool_use_block(name, input, id="toolu_1"):
    return SimpleNamespace(type="tool_use", name=name, input=input, id=id)


def response(*blocks, stop_reason="end_turn", stop_details=None):
    return SimpleNamespace(content=list(blocks), stop_reason=stop_reason, stop_details=stop_details)


class FakeMessages:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        # Snapshot the messages list: the engine keeps appending to the same list during a tool loop.
        self.calls.append({**kwargs, "messages": list(kwargs.get("messages", []))})
        return self.responses.pop(0)


class FakeAnthropic:
    """Stands in for anthropic.Anthropic: client.beta.messages.create(...)"""

    def __init__(self, *responses):
        self.messages = FakeMessages(responses)
        self.beta = SimpleNamespace(messages=self.messages)

    @property
    def calls(self):
        return self.messages.calls

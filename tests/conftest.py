import pytest


@pytest.fixture(autouse=True)
def stub_pages_are_not_thin(monkeypatch):
    """Test pages are a line or two of text. Real pages under THIN_PAGE_CHARS are shells; stubs are not."""
    from jev_ultrafast import contracts

    monkeypatch.setattr(contracts, "THIN_PAGE_CHARS", 0)


@pytest.fixture(autouse=True)
def no_final_answer_call(monkeypatch, request):
    """Planner tests count model calls; the final write-up is tested on its own."""
    if "keep_answer" in request.keywords:
        return
    from jev_ultrafast import planner

    monkeypatch.setattr(planner, "write_answer", lambda *_: "")


def pytest_configure(config):
    config.addinivalue_line("markers", "keep_answer: run the planner's real final write-up")

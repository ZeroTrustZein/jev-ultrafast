import pytest


@pytest.fixture(autouse=True)
def stub_pages_are_not_thin(monkeypatch):
    """Test pages are a line or two of text. Real pages under THIN_PAGE_CHARS are shells; stubs are not."""
    from jev_ultrafast import contracts

    monkeypatch.setattr(contracts, "THIN_PAGE_CHARS", 0)

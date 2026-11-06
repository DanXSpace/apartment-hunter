import sys
from datetime import datetime
from pathlib import Path

import pytest

from apartment_hunter.models import Listing

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def app_data_root(monkeypatch, tmp_path) -> Path:
    """Point this OS's per-user data location into tmp_path; return where the app's folder should be."""
    if sys.platform == "win32":  # %LOCALAPPDATA%\apartment-hunter
        monkeypatch.setenv("WIN_PD_OVERRIDE_LOCAL_APPDATA", str(tmp_path / "Local"))
        return tmp_path / "Local" / "apartment-hunter"
    if sys.platform == "darwin":  # ~/Library/Application Support/apartment-hunter
        monkeypatch.setenv("HOME", str(tmp_path))
        return tmp_path / "Library" / "Application Support" / "apartment-hunter"
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))  # ~/.local/share/apartment-hunter
    return tmp_path / "share" / "apartment-hunter"


@pytest.fixture
def fixture_html():
    def load(name: str) -> str:
        return (FIXTURES / name).read_text(encoding="utf-8")
    return load


def make_listing(listing_id="abc", price=2000, **overrides) -> Listing:
    fields = dict(
        listing_id=listing_id, url=f"https://www.apartments.com/x/{listing_id}/", name="Name",
        address="1 Main St", price_min=price, price_max=price, beds_min=2, beds_max=2,
        baths_min=1, baths_max=1, image_url=None, scraped_at=datetime(2026, 11, 5, 12, 0),
    )
    fields.update(overrides)
    return Listing(**fields)

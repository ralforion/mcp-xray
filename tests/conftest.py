import sys
from pathlib import Path

import pytest

# Allow running the suite without an editable install.
SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CONTRACT_FIXTURES = Path(__file__).resolve().parent / "contracts" / "fixtures"


@pytest.fixture
def fixtures_dir():
    return FIXTURES


@pytest.fixture
def crud_inventory():
    from mcp_xray import connect

    return connect.from_tools_json(FIXTURES / "crud_heavy.json")


@pytest.fixture
def clean_inventory():
    from mcp_xray import connect

    return connect.from_tools_json(FIXTURES / "clean.json")


@pytest.fixture
def bloated_inventory():
    from mcp_xray import connect

    return connect.from_tools_json(FIXTURES / "bloated.json")


@pytest.fixture
def overlapping_inventory():
    from mcp_xray import connect

    return connect.from_tools_json(FIXTURES / "overlapping.json")

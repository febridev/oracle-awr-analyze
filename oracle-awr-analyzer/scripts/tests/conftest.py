import pathlib

import pytest


@pytest.fixture
def sample_path() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[2] / "examples" / "awrrpt_1_3138_3162.html"

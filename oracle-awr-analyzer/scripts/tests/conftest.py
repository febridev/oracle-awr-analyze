import pathlib

import pytest


@pytest.fixture
def sample_path() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[2] / "examples" / "awrrpt_1_3138_3162.html"


def make_ctx(tmp_path, html):
    from parse_awr import Ctx, extract_meta, load_doc
    p = tmp_path / "r.html"
    p.write_text(html)
    doc = load_doc(p)
    return Ctx(doc, extract_meta(doc)["decimal"])


@pytest.fixture(scope="session")
def sample_ctx(tmp_path_factory):
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[2] / "examples" / "awrrpt_1_3138_3162.html"
    return make_ctx(tmp_path_factory.mktemp("s"), src.read_text())

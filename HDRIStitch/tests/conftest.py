import os
import sys

import pytest

HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))


def have_hugin():
    try:
        from hdristitch import hugin
        hugin.find_tools()
        return True
    except Exception:
        return False


needs_hugin = pytest.mark.skipif(not have_hugin(), reason="Hugin command-line tools not found")


@pytest.fixture
def hdri_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "templates").mkdir(parents=True)
    monkeypatch.setenv("HDRISTITCH_HOME", str(home))
    return home

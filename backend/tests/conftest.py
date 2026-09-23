import pytest


@pytest.fixture(autouse=True)
def tmp_data(tmp_path, monkeypatch):
    from app import db

    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    db.init_db()
    return tmp_path

"""Keep test investigations out of the developer's persistent accuracy log."""
import pytest


@pytest.fixture(autouse=True)
def isolated_accuracy_store(tmp_path, monkeypatch):
    import main
    from accuracy import AccuracyStore
    monkeypatch.setattr(main, "ACCURACY", AccuracyStore(tmp_path / "accuracy.sqlite3"))

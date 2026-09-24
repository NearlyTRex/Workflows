from fixture_app import add, newer


def test_add():
    assert add(2, 3) == 5


def test_newer():
    assert newer("1.10.0", "1.9.0")

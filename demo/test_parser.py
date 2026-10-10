from parser import parse


def test_preserves_last_item():
    assert parse([1, 2, 3]) == [1, 2, 3]


def test_empty_and_singleton():
    assert parse([]) == []
    assert parse([7]) == [7]

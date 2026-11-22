"""A reviewer's judgement (domain/judgement.py): levels that don't validate are dropped, not raised."""

from clean_eval.domain.judgement import Judgement


def test_invalid_entries_are_dropped_not_fatal():
    j = Judgement.model_validate(
        {
            "modules": {
                "a": {
                    "business": {"level": 1, "reason": "core path " * 50},
                    "effects": {"level": 9},
                    "nope": {"level": 0},
                },
                "b": {"entropy": {"level": True}},
                "c": "x",
                7: {"business": {"level": 0}},
            }
        }
    )
    assert list(j.modules) == ["a"]
    assert list(j.modules["a"]) == ["business"]
    assert len(j.modules["a"]["business"].reason) == 240
    assert Judgement.model_validate("not an object").modules == {}

import json

import pytest

from linkedin_post_bot.rotation import RotationError, TopicRotation, load_topics


def _write_topics(tmp_path, lines):
    path = tmp_path / "topics.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_load_topics_ignores_blanks_and_comments(tmp_path):
    path = _write_topics(
        tmp_path,
        ["# a comment", "", "  topic one  ", "topic two", "   ", "# trailing"],
    )
    assert load_topics(path) == ["topic one", "topic two"]


def test_load_topics_missing_file_raises(tmp_path):
    with pytest.raises(RotationError, match="not found"):
        load_topics(tmp_path / "nope.txt")


def test_load_topics_empty_file_raises(tmp_path):
    path = _write_topics(tmp_path, ["# only comments", ""])
    with pytest.raises(RotationError, match="empty"):
        load_topics(path)


def test_construction_fails_fast_on_missing_file(tmp_path):
    with pytest.raises(RotationError):
        TopicRotation(tmp_path / "nope.txt")


def test_next_topic_round_robin_and_wraps(tmp_path):
    path = _write_topics(tmp_path, ["a", "b", "c"])
    rot = TopicRotation(path)
    assert [rot.next_topic() for _ in range(7)] == ["a", "b", "c", "a", "b", "c", "a"]


def test_index_persists_across_instances(tmp_path):
    path = _write_topics(tmp_path, ["a", "b", "c"])

    first = TopicRotation(path)
    assert first.next_topic() == "a"
    assert first.next_topic() == "b"

    # Simulate a restart: a fresh instance continues from the persisted index.
    second = TopicRotation(path)
    assert second.next_topic() == "c"
    assert second.next_topic() == "a"


def test_index_sidecar_written_next_to_topics(tmp_path):
    path = _write_topics(tmp_path, ["a", "b"])
    rot = TopicRotation(path)
    rot.next_topic()
    index_file = tmp_path / "topics.txt.index.json"
    assert index_file.exists()
    assert json.loads(index_file.read_text())["index"] == 1


def test_corrupt_index_restarts_from_zero(tmp_path):
    path = _write_topics(tmp_path, ["a", "b"])
    (tmp_path / "topics.txt.index.json").write_text("not json", encoding="utf-8")
    rot = TopicRotation(path)
    assert rot.next_topic() == "a"


def test_index_modulo_handles_shrunk_topics_file(tmp_path):
    """If topics shrink below a persisted index, it wraps safely."""
    path = _write_topics(tmp_path, ["a", "b", "c", "d"])
    (tmp_path / "topics.txt.index.json").write_text(
        json.dumps({"index": 3}), encoding="utf-8"
    )
    _write_topics(tmp_path, ["a", "b"])
    rot = TopicRotation(path)
    # index 3 % 2 == 1 -> "b"
    assert rot.next_topic() == "b"

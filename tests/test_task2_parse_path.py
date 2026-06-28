"""Tests for Task 2: Updated parse_object_path() and parse_manifest_path()."""

from uuid import UUID

import pytest

from dags.agentic_bi_pipeline import parse_object_path, parse_manifest_path


class TestParseObjectPath:
    def test_parse_object_path_new_format(self):
        """New hierarchical path (8 parts) should parse correctly."""
        path = "raw/hoang_ai_chat/user_interactions/year=2026/month=06/day=25/hour=14/a1b2c3d4-e5f6-7890-abcd-ef1234567890.jsonl"
        app, table, batch_id = parse_object_path(path)
        assert app == "hoang_ai_chat"
        assert table == "user_interactions"
        assert isinstance(batch_id, UUID)
        assert str(batch_id) == "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

    def test_parse_object_path_accepts_legacy_hour_format(self):
        """Legacy hour path should still parse existing MinIO batches."""
        path = "raw/hoang_ai_chat/user_interactions/hour=2026-06-25-14/a1b2c3d4-e5f6-7890-abcd-ef1234567890.jsonl"
        app, table, batch_id = parse_object_path(path)
        assert app == "hoang_ai_chat"
        assert table == "user_interactions"
        assert str(batch_id) == "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

    def test_parse_object_path_other_source_app(self):
        """Test with another source app."""
        path = "raw/an_ai_chat/langfuse_generations/year=2026/month=06/day=25/hour=14/f1e2d3c4-b5a6-7980-cdef-1234567890ab.jsonl"
        app, table, batch_id = parse_object_path(path)
        assert app == "an_ai_chat"
        assert table == "langfuse_generations"
        assert isinstance(batch_id, UUID)

    def test_parse_object_path_invalid_prefix(self):
        """Path not starting with 'raw/' should raise ValueError."""
        path = "other/hoang_ai_chat/user_interactions/year=2026/month=06/day=25/hour=14/a1b2c3d4-e5f6-7890-abcd-ef1234567890.jsonl"
        with pytest.raises(ValueError):
            parse_object_path(path)

    def test_parse_object_path_not_jsonl(self):
        """Path not ending with .jsonl should raise ValueError."""
        path = "raw/hoang_ai_chat/user_interactions/year=2026/month=06/day=25/hour=14/a1b2c3d4-e5f6-7890-abcd-ef1234567890.txt"
        with pytest.raises(ValueError):
            parse_object_path(path)

    def test_parse_object_path_accepts_flat_format(self):
        """Flat dev-style path should parse correctly."""
        path = "raw/hoang_ai_chat/user_interactions/a1b2c3d4-e5f6-7890-abcd-ef1234567890.jsonl"
        app, table, batch_id = parse_object_path(path)
        assert app == "hoang_ai_chat"
        assert table == "user_interactions"
        assert str(batch_id) == "a1b2c3d4-e5f6-7890-abcd-ef1234567890"


class TestParseManifestPath:
    def test_parse_manifest_path_new_format(self):
        """New hierarchical manifest path (8 parts) should parse correctly."""
        path = "raw/hoang_ai_chat/_manifests/year=2026/month=06/day=25/hour=14/a1b2c3d4-e5f6-7890-abcd-ef1234567890.json"
        app, batch_id = parse_manifest_path(path)
        assert app == "hoang_ai_chat"
        assert isinstance(batch_id, UUID)
        assert str(batch_id) == "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

    def test_parse_manifest_path_accepts_legacy_hour_format(self):
        """Legacy hour manifest path should still parse existing MinIO batches."""
        path = "raw/hoang_ai_chat/_manifests/hour=2026-06-25-14/a1b2c3d4-e5f6-7890-abcd-ef1234567890.json"
        app, batch_id = parse_manifest_path(path)
        assert app == "hoang_ai_chat"
        assert str(batch_id) == "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

    def test_parse_manifest_path_invalid_prefix(self):
        """Path not starting with 'raw/' should raise ValueError."""
        path = "other/hoang_ai_chat/_manifests/year=2026/month=06/day=25/hour=14/a1b2c3d4-e5f6-7890-abcd-ef1234567890.json"
        with pytest.raises(ValueError):
            parse_manifest_path(path)

    def test_parse_manifest_path_not_json(self):
        """Path not ending with .json should raise ValueError."""
        path = "raw/hoang_ai_chat/_manifests/year=2026/month=06/day=25/hour=14/a1b2c3d4-e5f6-7890-abcd-ef1234567890.txt"
        with pytest.raises(ValueError):
            parse_manifest_path(path)

    def test_parse_manifest_path_missing_manifests(self):
        """Path missing _manifests segment should raise ValueError."""
        path = "raw/hoang_ai_chat/something/year=2026/month=06/day=25/hour=14/a1b2c3d4-e5f6-7890-abcd-ef1234567890.json"
        with pytest.raises(ValueError):
            parse_manifest_path(path)

    def test_parse_manifest_path_accepts_flat_format(self):
        """Flat dev-style manifest path should parse correctly."""
        path = "raw/hoang_ai_chat/_manifests/a1b2c3d4-e5f6-7890-abcd-ef1234567890.json"
        app, batch_id = parse_manifest_path(path)
        assert app == "hoang_ai_chat"
        assert str(batch_id) == "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

"""Regression tests for the spill/read loop.

The bug, from a real run: "read this file from Google Drive" worked --
google_drive_search and google_drive_read both succeeded on the first two
steps. Then the 28KB result was spilled to an artifact and the agent was
handed a pointer. Following that pointer was itself a tool call, so the read
was spilled too, and re-wrapped, and JSON-escaped, so the payload GREW.

    28,783 -> 29,766 -> 31,382 -> ... -> 2,625,118 bytes
    1 wrapper -> 13 wrappers

Thirteen successful tool calls, no errors, no dead calls, until
max_task_steps ran out. Two independent defects, so two sets of tests: the
wrapper had to stop compounding, and the dereference path had to stop being
spilled.

No key, no stack -- all pure functions.
"""

from agentsys.graph.nodes import _capped, _is_artifact_read
from agentsys.sanitize import wrap_untrusted


class TestWrapIsIdempotent:
    def test_wraps_plain_content_once(self):
        wrapped = wrap_untrusted("hello", "file_io")
        assert wrapped.count("untrusted_external_content") == 2  # open + close

    def test_does_not_re_wrap_already_wrapped_content(self):
        once = wrap_untrusted("hello", "file_io")
        twice = wrap_untrusted(once, "file_io")
        assert twice == once

    def test_survives_a_json_round_trip(self):
        """The real path: content is wrapped, json.dumps'd into an artifact,
        then read back. Escaping mangles the literal tag, so detection keys
        on the tag NAME, which survives."""
        import json

        once = wrap_untrusted("hello", "google_drive_read")
        escaped = json.dumps({"content": once})
        assert wrap_untrusted(escaped, "file_io") == escaped

    def test_thirteen_passes_do_not_grow_the_payload(self):
        """The exact shape of the observed failure."""
        text = wrap_untrusted("x" * 1000, "google_drive_read")
        size = len(text)
        for _ in range(13):
            text = wrap_untrusted(text, "file_io")
        assert len(text) == size

    def test_empty_text_is_untouched(self):
        assert wrap_untrusted("", "file_io") == ""


class TestArtifactReadDetection:
    def test_recognises_a_pointer_follow(self):
        assert _is_artifact_read("file_io", {"action": "read", "path": "_artifacts/abc_tool.json"})

    def test_windows_style_separators_still_match(self):
        assert _is_artifact_read("file_io", {"action": "read", "path": "_artifacts\\abc.json"})

    def test_a_normal_workspace_read_is_not_exempt(self):
        """Ordinary reads keep the usual size discipline -- the exemption is
        only for following a pointer."""
        assert not _is_artifact_read("file_io", {"action": "read", "path": "report.txt"})

    def test_a_write_is_not_a_read(self):
        assert not _is_artifact_read(
            "file_io", {"action": "write", "path": "_artifacts/x.json", "content": "y"}
        )

    def test_another_tool_is_never_exempt(self):
        assert not _is_artifact_read("google_drive_read", {"file_id": "_artifacts/x"})

    def test_missing_path_does_not_raise(self):
        assert not _is_artifact_read("file_io", {"action": "read"})


class TestCapped:
    def test_short_text_is_returned_whole(self):
        assert _capped("short") == "short"

    def test_long_text_is_truncated_with_a_terminal_notice(self):
        from agentsys.config import settings

        capped = _capped("x" * (settings.max_dereference_chars + 5000))
        assert len(capped) < settings.max_dereference_chars + 500
        # Crucially it must NOT offer another pointer to follow -- that is
        # what produced the loop in the first place.
        assert "no further pointer" in capped


class TestWrapperDetectionIsNotSpoofable:
    """The idempotence check must not become an injection bypass: content that
    merely MENTIONS the marker, deep in its body, still gets fenced."""

    def test_marker_in_the_body_does_not_prevent_wrapping(self):
        hostile = "x" * 5000 + "untrusted_external_content" + "y" * 100
        wrapped = wrap_untrusted(hostile, "web_search")
        assert wrapped.startswith("<untrusted_external_content")
        assert wrapped != hostile

    def test_marker_just_past_the_window_does_not_prevent_wrapping(self):
        from agentsys.sanitize import _MARKER_WINDOW

        hostile = "x" * (_MARKER_WINDOW + 10) + "untrusted_external_content"
        assert wrap_untrusted(hostile, "web_search").startswith("<untrusted_external_content")

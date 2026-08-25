"""The final answer must never hand the user a path they cannot open.

Observed live, on a Google Drive lookup: "you may need to read it from the
saved file at the path _artifacts/06b01af8-...json due to truncation of the
content preview." Every word true, addressed to entirely the wrong reader.

The cause is structural, not a wording slip: spill() returns a pointer dict
whose `hint` is an instruction ADDRESSED TO THE MODEL, and it rides inside
the tool output's data. agent_step needs it; synthesis must never see it.

No key, no stack.
"""

import json

from agentsys.artifacts import for_synthesis, spill


class TestForSynthesis:
    def test_drops_the_path_and_the_hint(self):
        pointer = json.dumps(
            {
                "_truncated": True,
                "total_chars": 28783,
                "preview": "INTERVIEW SCRIPT & ANSWER BOOK",
                "full_output_path": "_artifacts/06b01af8_google_drive_read.json",
                "hint": 'read it with file_io using {"action": "read", "path": "_artifacts/x.json"}',
            }
        )
        result = for_synthesis(pointer)
        assert "_artifacts" not in result
        assert "file_io" not in result
        assert "hint" not in result

    def test_keeps_the_preview_because_that_is_real_content(self):
        pointer = json.dumps(
            {"_truncated": True, "total_chars": 500, "preview": "the actual document text"}
        )
        assert "the actual document text" in for_synthesis(pointer)

    def test_says_the_result_was_long_in_plain_language(self):
        pointer = json.dumps({"_truncated": True, "total_chars": 28783, "preview": "x"})
        result = for_synthesis(pointer)
        assert "28783" in result and "long" in result.lower()

    def test_keeps_sibling_fields_the_tool_itself_produced(self):
        """A filename or id next to the preview is genuine content, not
        plumbing -- only the four pointer keys are dropped."""
        pointer = json.dumps(
            {
                "_truncated": True,
                "total_chars": 99,
                "preview": "body",
                "name": "Akshay_Dubey_Interview_Script",
                "full_output_path": "_artifacts/a.json",
            }
        )
        result = for_synthesis(pointer)
        assert "Akshay_Dubey_Interview_Script" in result
        assert "_artifacts" not in result

    def test_untruncated_output_passes_through_untouched(self):
        plain = json.dumps({"rows": [[1, 2]], "columns": ["a", "b"]})
        assert for_synthesis(plain) == plain

    def test_non_json_output_passes_through_untouched(self):
        assert for_synthesis("just some text") == "just some text"

    def test_none_and_empty_are_safe(self):
        assert for_synthesis(None) == ""
        assert for_synthesis("") == ""

    def test_a_json_list_is_not_mistaken_for_a_pointer(self):
        assert for_synthesis("[1, 2, 3]") == "[1, 2, 3]"


class TestAgainstARealSpill:
    """Belt and braces: build a pointer with the real spill() and confirm
    nothing it emits survives into synthesis. Keeps this test honest if
    spill's shape ever changes."""

    def test_real_spill_output_is_fully_redacted(self, tmp_path, monkeypatch):
        from agentsys.config import settings

        monkeypatch.setattr(settings, "workspace_dir", str(tmp_path))
        pointer = spill("task-1", "sub-1", "google_drive_read", "X" * 5000)

        result = for_synthesis(json.dumps(pointer))
        assert pointer["full_output_path"] not in result
        assert "file_io" not in result
        assert "_artifacts" not in result


class TestDigestBeatsPreview:
    """A preview is a lossless view of almost none of the document; a digest
    is a lossy view of all of it. For answering a question, the second is
    worth far more -- which is why spilling now summarizes."""

    def test_summary_is_used_in_place_of_the_preview(self):
        pointer = json.dumps(
            {
                "_truncated": True,
                "total_chars": 28783,
                "summary": "An interview script covering 12 questions on RAG, evals and system design.",
                "preview": "INTERVIEW SCRIPT & ANSWER BOOK\nPrepared for:",
            }
        )
        result = for_synthesis(pointer)
        assert "12 questions on RAG" in result
        assert "Summarized from a 28783-character result" in result

    def test_falls_back_to_the_preview_when_the_digest_failed(self):
        """A summarizer outage must cost answer quality, never the result."""
        pointer = json.dumps(
            {"_truncated": True, "total_chars": 900, "summary": "", "preview": "the opening words"}
        )
        result = for_synthesis(pointer)
        assert "the opening words" in result
        assert "beginning" in result

    def test_digest_is_skipped_when_disabled(self, monkeypatch):
        from agentsys import artifacts
        from agentsys.config import settings

        monkeypatch.setattr(settings, "summarize_spilled_output", False)
        assert artifacts._digest("x" * 5000) == ""

    def test_digest_failure_returns_empty_rather_than_raising(self, monkeypatch):
        from agentsys import artifacts

        def boom(*args, **kwargs):
            raise RuntimeError("provider down")

        monkeypatch.setattr("agentsys.llm.complete", boom)
        assert artifacts._digest("x" * 5000) == ""

    def test_digest_input_is_bounded(self, monkeypatch):
        """One pathological result must not become one pathological bill."""
        from agentsys import artifacts
        from agentsys.config import settings

        seen = {}

        def capture(prompt, **kwargs):
            seen["len"] = len(prompt)
            return "ok", None

        monkeypatch.setattr("agentsys.llm.complete", capture)
        artifacts._digest("x" * (settings.max_digest_input_chars * 3))
        assert seen["len"] < settings.max_digest_input_chars + 2000

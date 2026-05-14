import json


def test_parse_codex_jsonl_extracts_readable_messages(tmp_path):
    from codex_self_evolution.session_recall.parser import parse_codex_jsonl

    transcript = tmp_path / "session.jsonl"
    transcript.write_text(
        "\n".join(
            [
                json.dumps({"type": "session_meta", "payload": {"id": "s1", "cwd": str(tmp_path)}}),
                json.dumps({"role": "user", "content": "remember this plan"}),
                json.dumps({"type": "agent_message", "text": "recorded"}),
                json.dumps({"role": "tool", "tool_name": "exec_command", "content": "pytest failed"}),
                json.dumps({"type": "token_count", "tokens": 100}),
            ]
        ),
        encoding="utf-8",
    )

    parsed = parse_codex_jsonl(transcript, session_id="fallback", cwd=str(tmp_path))

    assert parsed.session_id == "s1"
    assert [m.role for m in parsed.messages] == ["user", "assistant", "tool"]
    assert parsed.messages[0].content == "remember this plan"
    assert parsed.messages[2].tool_name == "exec_command"
    assert parsed.messages[2].raw_json.startswith("{")


def test_parse_codex_jsonl_handles_response_item_content_parts(tmp_path):
    from codex_self_evolution.session_recall.parser import parse_codex_jsonl

    transcript = tmp_path / "session.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "id": "msg-1",
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": "part one"},
                        {"type": "output_text", "text": "part two"},
                    ],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    parsed = parse_codex_jsonl(transcript, session_id="s1", cwd=str(tmp_path))

    assert len(parsed.messages) == 1
    assert parsed.messages[0].message_uid == "msg-1"
    assert parsed.messages[0].content == "part one\npart two"


def test_parse_codex_jsonl_uses_raw_line_hash_for_uid(tmp_path):
    from codex_self_evolution.session_recall.parser import parse_codex_jsonl

    transcript = tmp_path / "session.jsonl"
    transcript.write_text(json.dumps({"role": "user", "content": "hello"}) + "\n", encoding="utf-8")

    parsed1 = parse_codex_jsonl(transcript, session_id="s1", cwd=str(tmp_path))
    parsed2 = parse_codex_jsonl(transcript, session_id="s1", cwd=str(tmp_path))

    assert parsed1.messages[0].message_uid == parsed2.messages[0].message_uid


def test_parse_codex_jsonl_does_not_use_turn_id_as_message_uid(tmp_path):
    from codex_self_evolution.session_recall.parser import parse_codex_jsonl

    transcript = tmp_path / "session.jsonl"
    transcript.write_text(
        "\n".join(
            [
                json.dumps({"turn_id": "same-turn", "role": "user", "content": "first"}),
                json.dumps({"turn_id": "same-turn", "role": "assistant", "content": "second"}),
            ]
        ),
        encoding="utf-8",
    )

    parsed = parse_codex_jsonl(transcript, session_id="s1", cwd=str(tmp_path))

    assert len(parsed.messages) == 2
    assert parsed.messages[0].message_uid != parsed.messages[1].message_uid

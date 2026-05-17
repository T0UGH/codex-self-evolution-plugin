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


def test_parse_claude_jsonl_extracts_text_and_tool_blocks(tmp_path):
    from codex_self_evolution.session_recall.parser import parse_claude_jsonl

    repo = tmp_path / "repo"
    repo.mkdir()
    transcript = tmp_path / "claude.jsonl"
    transcript.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "user",
                        "sessionId": "claude-session",
                        "uuid": "u1",
                        "cwd": str(repo),
                        "timestamp": "2026-05-17T01:00:00.000Z",
                        "message": {"role": "user", "content": "sync claude history"},
                    }
                ),
                json.dumps(
                    {
                        "type": "assistant",
                        "sessionId": "claude-session",
                        "uuid": "a1",
                        "cwd": str(repo),
                        "timestamp": "2026-05-17T01:00:01.000Z",
                        "message": {
                            "role": "assistant",
                            "content": [
                                {"type": "thinking", "thinking": "not for recall"},
                                {"type": "text", "text": "claude answer"},
                                {"type": "tool_use", "name": "Bash", "input": {"command": "pytest"}},
                            ],
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "user",
                        "sessionId": "claude-session",
                        "uuid": "u2",
                        "cwd": str(repo),
                        "timestamp": "2026-05-17T01:00:02.000Z",
                        "message": {
                            "role": "user",
                            "content": [
                                {"type": "tool_result", "tool_use_id": "toolu_1", "content": "pytest passed"},
                            ],
                        },
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    parsed = parse_claude_jsonl(transcript)

    assert parsed.session_id == "claude:claude-session"
    assert parsed.cwd == str(repo)
    assert parsed.metadata["source"] == "claude_code_jsonl"
    assert [message.role for message in parsed.messages] == ["user", "assistant", "assistant", "tool"]
    assert "not for recall" not in "\n".join(message.content for message in parsed.messages)
    assert parsed.messages[1].content == "claude answer"
    assert parsed.messages[2].tool_name == "Bash"
    assert "pytest" in parsed.messages[2].content
    assert parsed.messages[3].tool_name == "toolu_1"

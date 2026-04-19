Return strict JSON only.

Review the completed turn and emit at most three suggestion families:

- `memory_updates`: durable repo or workflow knowledge worth preserving
- `recall_candidate`: context that could help future turns retrieve relevant history
- `skill_action`: candidate managed skills to create, patch, edit, or retire

Rules:

- Single pass only
- No prose outside JSON
- Use empty arrays when no suggestion exists
- Keep suggestions concise
- Prefer thread/read output first, then transcript, then hook payload
- Reject one-off noise, obvious errors, and low-signal chatter

JSON shape:

```json
{
  "memory_updates": [
    {
      "summary": "short summary",
      "details": {
        "content": "durable memory text",
        "source_paths": ["src/example.py"]
      },
      "confidence": 0.9
    }
  ],
  "recall_candidate": [],
  "skill_action": [
    {
      "summary": "create a helper skill",
      "details": {
        "action": "create",
        "skill_id": "example-skill",
        "title": "Example Skill",
        "content": "Do X when Y."
      },
      "confidence": 0.8
    }
  ]
}
```

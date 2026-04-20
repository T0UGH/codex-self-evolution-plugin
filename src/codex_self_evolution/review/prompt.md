Return strict JSON only.

Review the completed turn and emit at most three suggestion families:

- `memory_updates`: durable repo or workflow knowledge worth preserving
- `recall_candidate`: context that could help future turns retrieve relevant history
- `skill_action`: candidate managed skills to create, patch, edit, or retire

## Rules

- Single pass only
- No prose outside JSON
- Use empty arrays when no suggestion exists
- Keep suggestions concise
- Prefer thread/read output first, then transcript, then hook payload
- Reject one-off noise, obvious errors, and low-signal chatter

## Per-suggestion schema (strict)

Each suggestion (in any family) MUST match this shape:

- `summary`: **non-empty string**. Required.
- `details`: **JSON object**. Required. NOT a string, NOT an array, NOT null.
- `details.content`: **non-empty string** for `memory_updates` and `recall_candidate`. Required.
  - If you only have a short note, put it in `details.content`. Do not invent fields like `details.note`, `details.text`, `details.body` — they will be ignored or coerced into `content` as a last resort.
- `confidence`: number in `[0, 1]`. Optional, defaults to `1.0`.
- `details.source_paths`: array of strings. Optional.

For `skill_action`, `details` must additionally include:

- `action`: one of `"create" | "patch" | "edit" | "retire"`
- `skill_id`: short kebab-case id
- `title`: non-empty string
- `content`: non-empty string

## Handling of non-conforming items

The runtime uses **per-item lenient parsing**: a single malformed suggestion is silently dropped, the rest are kept. **Do not rely on this** — optimise for strict-shape output. Runtime will log how many items were dropped.

## JSON shape

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

# Slide JSON schema

`scripts/build_pptx.py` reads one JSON object.

## Root

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `title` | string | yes | Deck title (used on title slide if first slide has no title) |
| `intent` | string | yes | `follow_instructions`, `teaching_outline`, or `mixed` |
| `source_url` | string | no | YouTube URL |
| `slides` | array | yes | At least one slide |

## Slide

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `layout` | string | yes | `title`, `section`, or `bullets` |
| `title` | string | yes | Slide heading |
| `subtitle` | string | no | `title` layout only |
| `bullets` | string[] | no | `bullets` layout; max 8 |
| `notes` | string | no | Presenter notes |

Unknown fields are ignored.

## Example: follow_instructions

```json
{
  "title": "Q3 Product Review",
  "intent": "follow_instructions",
  "source_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
  "slides": [
    {
      "layout": "title",
      "title": "Q3 Product Review",
      "subtitle": "As specified in the video"
    },
    {
      "layout": "bullets",
      "title": "Wins",
      "bullets": ["Shipped search v2", "NPS +8", "Churn down 1.2 pts"],
      "notes": "Speaker asked for three numbered wins."
    },
    {
      "layout": "bullets",
      "title": "Asks",
      "bullets": ["Hire two PMs", "Cut onboarding time in half"]
    }
  ]
}
```

## Example: teaching_outline

```json
{
  "title": "Intro to HTTP caching",
  "intent": "teaching_outline",
  "slides": [
    {
      "layout": "title",
      "title": "Intro to HTTP caching",
      "subtitle": "From the video lesson"
    },
    {
      "layout": "section",
      "title": "Why cache?"
    },
    {
      "layout": "bullets",
      "title": "Cache-Control basics",
      "bullets": [
        "max-age sets freshness in seconds",
        "no-store means do not write to disk",
        "private vs public controls shared caches"
      ],
      "notes": "Skip CDN examples if the audience is backend-only."
    },
    {
      "layout": "bullets",
      "title": "Recap",
      "bullets": ["Freshness vs validation", "Prefer explicit max-age", "Test with curl -I"]
    }
  ]
}
```

# design.json schema

`scripts/build_design.py` renders one JSON object into a `.pptx`.
`scripts/preview.py` renders the same object into a PNG.

All positions and sizes are **inches**. Font sizes are **points**. Colors are
hex strings without `#`.

## Root

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `canvas` | object | no | `{ "w_in": 13.333, "h_in": 7.5, "bg": "E6E7E9" }`. Defaults to 16:9. |
| `defaults` | object | no | Font defaults merged into every text block: `name`, `size`, `color`. |
| `shapes` | array | yes* | Single-slide shorthand. |
| `animations` | array | no | Single-slide shorthand. |
| `transition` | string / object | no | `fade`, `push`, `wipe`, `split`, `cut`, `morph`. Object form: `{"kind":"morph","speed":"slow"}`. Morph is a **slide transition**, not an Animation Pane effect. |
| `transition_speed` | string | no | `slow`, `med`, `fast`. Morph defaults to `slow`. |
| `slides` | array | yes* | Multi-slide form; each item takes `shapes`, `animations`, `bg`, `transition`. |
| `title`, `source_url`, `note` | string | no | Metadata, ignored by the renderer. |

\* Provide either `shapes` or `slides`.

Shapes render in array order, so later entries sit on top.

## Shape

| Field | Type | Notes |
| --- | --- | --- |
| `id` | string | Required to animate the shape. Also used as the PowerPoint shape name if `name` is omitted. |
| `name` | string | PowerPoint shape name. Morph pairs objects on consecutive slides by this string, so year tabs and other shared chrome must reuse the same `name` on every slide. |
| `type` | string | See the type list below. |
| `x`, `y`, `w`, `h` | number | Inches from the top-left of the slide. |
| `fill` | string / object / null | Hex, `{"type":"gradient","stops":[["FFF",0],["CCC",100]],"angle":90}`, or `null` for no fill. |
| `line` | object / null | `{"color":"F12F78","width":2.25,"round_ends":true}`. Omit for no outline. |
| `shadow` | object / false | `{"blur":16,"dist":5,"alpha":0.20,"color":"000000","dir":5400000}`. |
| `adj` | number / array | Shape adjustment handles, as a fraction. For `roundRect` this is the corner radius over the shorter side. |
| `rotation` | number | Degrees clockwise. |
| `flip_h`, `flip_v` | bool | Mirror the shape. |
| `text` | string | `\n` starts a new paragraph. |
| `runs` | array | Mixed formatting; each run takes `text` plus any font key. |
| `font` | object | See below. |
| `points` | array | `freeform` only: `[[x, y], ...]` in inches. |
| `close` | bool | `freeform` only; default `true`. |
| `path` | string | `picture` only: path to an image file. |

### Types

`rect`, `roundRect`, `round1Rect`, `round2SameRect`, `oval`, `pie`, `chord`,
`arc`, `triangle`, `rightTriangle`, `diamond`, `hexagon`, `star4`, `star5`,
`arrowRight`, `chevron`, `teardrop`, `donut`, `blockArc`, `plaque`, `line`,
`freeform`, `text`, `picture`.

`text` shapes are borderless, fill-less textboxes. `round2SameRect` rounds the
top two corners, which is what card headers usually want.

### Font object

| Key | Default | Notes |
| --- | --- | --- |
| `name` | `Calibri` | Prefer fonts that ship with Office so the deck travels. |
| `size` | `18` | Points. |
| `bold`, `italic` | `false` | |
| `color` | `000000` | |
| `align` | `center` | `left`, `center`, `right`, `justify`. |
| `anchor` | `middle` | Vertical: `top`, `middle`, `bottom`. |
| `spacing` | `0` | Letter spacing in points. Essential for wide display titles. |
| `line_spacing` | `1.0` | Multiple of single spacing. |
| `caps` | `false` | Render as all caps. |
| `wrap` | `true` | Set on the shape, not the font. |

## Animations

Either a flat list of steps (one click group) or explicit groups:

```json
"animations": [
  { "steps": [ { "target": "card1", "effect": "float_up", "dur": 500, "start": "on_click" } ] }
]
```

### Step

| Field | Default | Notes |
| --- | --- | --- |
| `target` / `targets` | required | Shape `id`, or a list that animates together. |
| `effect` | required | See the effect list. |
| `dur` | `500` | Milliseconds. |
| `delay` | `0` | Milliseconds, relative to the chosen start. |
| `start` | `after_previous` | `on_click`, `with_previous`, `after_previous`. |

`with_previous` begins when the previous step began; `after_previous` begins
when it ended. Each group becomes one click in the slideshow.

### Effects

Entrance: `appear`, `fade`, `zoom`, `float_up`, `float_down`,
`fly_in_left`, `fly_in_right`, `fly_in_top`, `fly_in_bottom`,
`wipe_up`, `wipe_down`, `wipe_left`, `wipe_right`, `split`, `grow_turn`.

Emphasis (these loop): `grow_shrink` (`scale`), `spin` (`clockwise`),
`bob` (`dx`, `dy`), `pulse_color` (`color`). Extra keys go on the step itself.

Animations only play in PowerPoint. The PNG preview and the Cursor file preview
both show the final resting state.

## Morph timelines

Morph is the Transitions-tab effect. The motion lives **between slides**: duplicate
the chrome, keep shape `name`s identical, and change `x` / `y` / `w` / `h` / fill.
PowerPoint interpolates the rest. Do not fake that motion with Fly In on one slide.

A year-tab timeline is typically 6–7 slides (title plus one state per year). Each
resting frame parks some years on the left stack and the rest on the right; Morph
slides a year from one stack to the other. Center content can be unique per slide —
unmatched names fade in and out.

Office writes Morph as markup-compatibility AlternateContent (`p159:morph` with a
fade fallback). `build_design.py` emits that when `transition` is `morph`. Older
PowerPoint that does not understand Morph will fade instead.

See [examples/morph-timeline.design.json](examples/morph-timeline.design.json).

## Example

```json
{
  "canvas": { "w_in": 13.333, "h_in": 7.5, "bg": "E6E7E9" },
  "defaults": { "name": "Century Gothic", "size": 12, "color": "8A8A8C" },
  "transition": "fade",
  "shapes": [
    { "id": "card1", "type": "roundRect", "x": 1.625, "y": 2.42, "w": 1.75, "h": 4.25,
      "fill": "F1F1F2", "adj": 0.105, "shadow": { "blur": 16, "dist": 5, "alpha": 0.2 } },
    { "id": "cap1", "type": "round2SameRect", "x": 1.625, "y": 2.42, "w": 1.75, "h": 1.02,
      "fill": "F12F78", "adj": [0.176, 0.0] },
    { "id": "plan1", "type": "text", "x": 1.625, "y": 2.56, "w": 1.75, "h": 0.32,
      "text": "PLAN",
      "font": { "size": 22, "bold": true, "color": "FFFFFF", "spacing": 0.5 } }
  ],
  "animations": [
    { "steps": [
      { "targets": ["card1", "cap1"], "effect": "float_up", "dur": 500, "start": "on_click" },
      { "target": "plan1", "effect": "zoom", "dur": 500, "delay": 150, "start": "with_previous" }
    ] }
  ]
}
```

See [examples/infographic-plan-cards.design.json](examples/infographic-plan-cards.design.json)
for a complete 78-shape slide.

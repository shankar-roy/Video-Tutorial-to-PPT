#!/usr/bin/env python3
"""DrawingML slide-timing builder.

python-pptx has no animation API, so entrance and emphasis effects are emitted
as raw <p:timing> trees. Effects are defined by their behaviour nodes (which is
what actually plays); the preset id only controls the label PowerPoint shows in
the Animation Pane.
"""

from __future__ import annotations

from lxml import etree

PML = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
DML = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


class Ids:
    """Timing nodes need unique ids across the whole slide."""

    def __init__(self, start: int = 2) -> None:
        self.n = start

    def next(self) -> str:
        self.n += 1
        return str(self.n)


def _e(tag, attrib=None, children=None, text=None):
    ns = PML if not tag.startswith("{") else ""
    el = etree.Element((ns + tag) if ns else tag, attrib or {})
    if text is not None:
        el.text = text
    for child in children or []:
        if child is not None:
            el.append(child)
    return el


def tgt(spid) -> etree._Element:
    return _e("tgtEl", children=[_e("spTgt", {"spid": str(spid)})])


def cond_delay(delay) -> etree._Element:
    return _e("stCondLst", children=[_e("cond", {"delay": str(delay)})])


def set_visible(ids: Ids, spid) -> etree._Element:
    return _e(
        "set",
        children=[
            _e(
                "cBhvr",
                children=[
                    _e("cTn", {"id": ids.next(), "dur": "1", "fill": "hold"}, [cond_delay(0)]),
                    tgt(spid),
                    _e("attrNameLst", children=[_e("attrName", text="style.visibility")]),
                ],
            ),
            _e("to", children=[_e("strVal", {"val": "visible"})]),
        ],
    )


def _fade(ids: Ids, spid, dur: int) -> etree._Element:
    return _e(
        "animEffect",
        {"transition": "in", "filter": "fade"},
        [_e("cBhvr", children=[_e("cTn", {"id": ids.next(), "dur": str(dur)}), tgt(spid)])],
    )


def _filter_effect(ids: Ids, spid, dur: int, filter_name: str) -> etree._Element:
    return _e(
        "animEffect",
        {"transition": "in", "filter": filter_name},
        [_e("cBhvr", children=[_e("cTn", {"id": ids.next(), "dur": str(dur)}), tgt(spid)])],
    )


def _scale_from_zero(ids: Ids, spid, dur: int, start: str = "0") -> list:
    def axis(attr: str) -> etree._Element:
        return _e(
            "anim",
            {"calcmode": "lin", "valueType": "num"},
            [
                _e(
                    "cBhvr",
                    children=[
                        _e("cTn", {"id": ids.next(), "dur": str(dur), "fill": "hold"}),
                        tgt(spid),
                        _e("attrNameLst", children=[_e("attrName", text=attr)]),
                    ],
                ),
                _e(
                    "tavLst",
                    children=[
                        _e("tav", {"tm": "0"}, [_e("val", children=[_e("fltVal", {"val": start})])]),
                        _e(
                            "tav",
                            {"tm": "100000"},
                            [_e("val", children=[_e("strVal", {"val": f"#{attr}"})])],
                        ),
                    ],
                ),
            ],
        )

    return [axis("ppt_w"), axis("ppt_h")]


def _motion(ids: Ids, spid, dur: int, dx: float, dy: float, **cTn) -> etree._Element:
    attrs = {"id": ids.next(), "dur": str(dur), "fill": "hold"}
    attrs.update({k: str(v) for k, v in cTn.items()})
    return _e(
        "animMotion",
        {"origin": "layout", "path": f"M {dx} {dy} L 0 0 E", "pathEditMode": "relative"},
        [
            _e(
                "cBhvr",
                children=[
                    _e("cTn", attrs),
                    tgt(spid),
                    _e(
                        "attrNameLst",
                        children=[_e("attrName", text="ppt_x"), _e("attrName", text="ppt_y")],
                    ),
                ],
            )
        ],
    )


def _rotate(ids: Ids, spid, dur: int, by: str, **cTn) -> etree._Element:
    attrs = {"id": ids.next(), "dur": str(dur), "fill": "hold"}
    attrs.update({k: str(v) for k, v in cTn.items()})
    return _e(
        "animRot",
        {"by": by},
        [
            _e(
                "cBhvr",
                children=[
                    _e("cTn", attrs),
                    tgt(spid),
                    _e("attrNameLst", children=[_e("attrName", text="r")]),
                ],
            )
        ],
    )


# name -> (presetID, presetClass, presetSubtype) for the Animation Pane label
PRESETS = {
    "appear": (1, "entr", 0),
    "fade": (10, "entr", 0),
    "zoom": (23, "entr", 0),
    "float_up": (34, "entr", 0),
    "float_down": (42, "entr", 0),
    "fly_in_left": (2, "entr", 8),
    "fly_in_right": (2, "entr", 2),
    "fly_in_top": (2, "entr", 1),
    "fly_in_bottom": (2, "entr", 4),
    "wipe_up": (22, "entr", 4),
    "wipe_down": (22, "entr", 1),
    "wipe_left": (22, "entr", 8),
    "wipe_right": (22, "entr", 2),
    "split": (16, "entr", 26),
    "grow_turn": (31, "entr", 0),
    "grow_shrink": (6, "emph", 0),
    "spin": (8, "emph", 0),
    "pulse_color": (2, "emph", 0),
    "bob": (0, "path", 0),
}

FLOAT_OFFSET = 0.08
FLY_OFFSET = 0.55


def entrance_behaviors(ids: Ids, spid, effect: str, dur: int) -> list:
    """Behaviour nodes for one entrance effect, always starting with a visibility set."""
    bits = [set_visible(ids, spid)]

    if effect == "appear":
        return bits
    if effect == "fade":
        bits.append(_fade(ids, spid, dur))
    elif effect == "zoom":
        bits.extend(_scale_from_zero(ids, spid, dur))
        bits.append(_fade(ids, spid, dur))
    elif effect in ("float_up", "float_down"):
        dy = FLOAT_OFFSET if effect == "float_up" else -FLOAT_OFFSET
        bits.append(_motion(ids, spid, dur, 0, dy))
        bits.append(_fade(ids, spid, dur))
    elif effect.startswith("fly_in_"):
        side = effect.rsplit("_", 1)[1]
        dx, dy = {
            "left": (-FLY_OFFSET, 0.0),
            "right": (FLY_OFFSET, 0.0),
            "top": (0.0, -FLY_OFFSET),
            "bottom": (0.0, FLY_OFFSET),
        }[side]
        bits.append(_motion(ids, spid, dur, dx, dy))
    elif effect.startswith("wipe_"):
        bits.append(_filter_effect(ids, spid, dur, f"wipe({effect.rsplit('_', 1)[1]})"))
    elif effect == "split":
        bits.append(_filter_effect(ids, spid, dur, "barn(inVertical)"))
    elif effect == "grow_turn":
        bits.extend(_scale_from_zero(ids, spid, dur))
        bits.append(_rotate(ids, spid, dur, "0"))
        bits.append(_fade(ids, spid, dur))
    else:
        raise ValueError(f"Unknown entrance effect: {effect}")
    return bits


def emphasis_behaviors(ids: Ids, spid, effect: str, dur: int, options: dict) -> list:
    """Looping emphasis effects. These do not toggle visibility."""
    if effect == "grow_shrink":
        scale = options.get("scale", 1.08)
        return [
            _e(
                "animScale",
                children=[
                    _e(
                        "cBhvr",
                        children=[
                            _e(
                                "cTn",
                                {
                                    "id": ids.next(),
                                    "dur": str(dur),
                                    "fill": "hold",
                                    "autoRev": "1",
                                    "repeatCount": "indefinite",
                                    "accel": "50000",
                                    "decel": "50000",
                                },
                            ),
                            tgt(spid),
                        ],
                    ),
                    _e("to", children=[_e("ptStr", {"x": str(scale), "y": str(scale)})]),
                ],
            )
        ]
    if effect == "spin":
        by = "21600000" if options.get("clockwise", True) else "-21600000"
        return [_rotate(ids, spid, dur, by, repeatCount="indefinite")]
    if effect == "bob":
        dx = options.get("dx", 0.0)
        dy = options.get("dy", 0.03)
        return [_motion(ids, spid, dur, dx, dy, autoRev="1", repeatCount="indefinite")]
    if effect == "pulse_color":
        to = _e("to")
        etree.SubElement(to, f"{DML}srgbClr").set("val", options.get("color", "FFE566"))
        return [
            _e(
                "animClr",
                {"clrSpc": "rgb"},
                [
                    _e(
                        "cBhvr",
                        children=[
                            _e(
                                "cTn",
                                {
                                    "id": ids.next(),
                                    "dur": str(dur),
                                    "fill": "hold",
                                    "autoRev": "1",
                                    "repeatCount": "indefinite",
                                },
                            ),
                            tgt(spid),
                            _e("attrNameLst", children=[_e("attrName", text="fillcolor")]),
                        ],
                    ),
                    to,
                ],
            )
        ]
    raise ValueError(f"Unknown emphasis effect: {effect}")


EMPHASIS = {"grow_shrink", "spin", "bob", "pulse_color"}


def effect_par(ids: Ids, effect: str, behaviors: list, node_type: str) -> etree._Element:
    preset_id, preset_class, subtype = PRESETS.get(effect, (1, "entr", 0))
    return _e(
        "par",
        children=[
            _e(
                "cTn",
                {
                    "id": ids.next(),
                    "presetID": str(preset_id),
                    "presetClass": preset_class,
                    "presetSubtype": str(subtype),
                    "fill": "hold",
                    "grpId": "0",
                    "nodeType": node_type,
                },
                [cond_delay(0), _e("childTnLst", children=behaviors)],
            )
        ],
    )


def delayed_par(ids: Ids, delay_ms, children) -> etree._Element:
    return _e(
        "par",
        children=[
            _e(
                "cTn",
                {"id": ids.next(), "fill": "hold"},
                [cond_delay(delay_ms), _e("childTnLst", children=children)],
            )
        ],
    )


def click_group(ids: Ids, children) -> etree._Element:
    inner = _e(
        "par",
        children=[
            _e(
                "cTn",
                {"id": ids.next(), "fill": "hold"},
                [cond_delay(0), _e("childTnLst", children=children)],
            )
        ],
    )
    return _e(
        "par",
        children=[
            _e(
                "cTn",
                {"id": ids.next(), "fill": "hold"},
                [cond_delay("indefinite"), _e("childTnLst", children=[inner])],
            )
        ],
    )


def build_timing(groups: list) -> tuple:
    """Turn click groups of steps into a <p:timing> element.

    groups: list of click groups; each is a list of dicts with keys
      spid, effect, dur (ms), delay (ms), start ("on_click"/"with_previous"/"after_previous"),
      and optional per-effect options.

    Returns (timing_element, animated_spids).
    """
    ids = Ids()
    click_pars = []
    animated = []

    for steps in groups:
        cursor = 0
        previous_end = 0
        children = []
        for step in steps:
            spid = str(step["spid"])
            effect = step["effect"]
            dur = int(step.get("dur", 500))
            delay = int(step.get("delay", 0))
            start = step.get("start", "after_previous")

            if start == "with_previous":
                begin = cursor + delay
            elif start == "on_click":
                begin = delay
            else:
                begin = previous_end + delay

            options = {k: v for k, v in step.items()
                       if k not in {"spid", "effect", "dur", "delay", "start", "id"}}
            if effect in EMPHASIS:
                behaviors = emphasis_behaviors(ids, spid, effect, dur, options)
                node_type = "withEffect"
            else:
                behaviors = entrance_behaviors(ids, spid, effect, dur)
                node_type = "clickEffect" if not children else "withEffect"

            children.append(delayed_par(ids, begin, [effect_par(ids, effect, behaviors, node_type)]))
            animated.append(spid)
            cursor = begin
            previous_end = begin + dur

        if children:
            click_pars.append(click_group(ids, children))

    seq = _e(
        "seq",
        {"concurrent": "1", "nextAc": "seek"},
        [
            _e(
                "cTn",
                {"id": ids.next(), "dur": "indefinite", "nodeType": "mainSeq"},
                [_e("childTnLst", children=click_pars)],
            ),
            _e(
                "prevCondLst",
                children=[
                    _e("cond", {"evt": "onPrev", "delay": "0"},
                       [_e("tgtEl", children=[_e("sldTgt")])])
                ],
            ),
            _e(
                "nextCondLst",
                children=[
                    _e("cond", {"evt": "onNext", "delay": "0"},
                       [_e("tgtEl", children=[_e("sldTgt")])])
                ],
            ),
        ],
    )

    timing = _e(
        "timing",
        children=[
            _e(
                "tnLst",
                children=[
                    _e(
                        "par",
                        children=[
                            _e(
                                "cTn",
                                {
                                    "id": "1",
                                    "dur": "indefinite",
                                    "restart": "never",
                                    "nodeType": "tmRoot",
                                },
                                [_e("childTnLst", children=[seq])],
                            )
                        ],
                    )
                ],
            )
        ],
    )

    bld = _e("bldLst")
    seen = set()
    for spid in animated:
        if spid in seen:
            continue
        seen.add(spid)
        bld.append(_e("bldP", {"spid": spid, "grpId": "0", "anim": "1"}))
    timing.append(bld)
    return timing, animated


def attach_timing(slide, groups: list) -> int:
    """Attach a timing tree to a slide. Returns the number of animated steps."""
    if not groups:
        return 0
    timing, animated = build_timing(groups)
    for stale in slide._element.findall(f"{PML}timing"):
        slide._element.remove(stale)
    slide._element.append(timing)
    return len(animated)


def add_transition(slide, kind: str = "fade", speed: str = "med") -> None:
    """Slide transition. PowerPoint expects <p:transition> before <p:timing>."""
    element = slide._element
    for stale in element.findall(f"{PML}transition"):
        element.remove(stale)
    transition = _e("transition", {"spd": speed})
    transition.append(_e(kind))
    timings = element.findall(f"{PML}timing")
    if timings:
        timings[0].addprevious(transition)
    else:
        element.append(transition)

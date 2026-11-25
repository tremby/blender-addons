bl_info = {
    "name": "Subtitle search",
    "author": "Claude",
    "version": (0, 5, 2),
    "blender": (5, 2, 2),
    "location": "Video Sequencer > Sidebar > Subtitles",
    "description": "Search the text of imported subtitle strips and jump between matches",
    "category": "Sequencer",
}

import bisect
import re
import textwrap

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import Operator, Panel, PropertyGroup, UIList

# Strip names produced by the SRT importer, including ".001" style suffixes
# that Blender adds when a name is already taken.
NAME_RE = re.compile(r"^Subtitle \d+(\.\d+)?$")

# Set while we change the active result index from code, so that the index
# update callback doesn't also trigger a jump.
_suppress_index_update = False

# Start frames of the current results (ascending), for fast next/previous.
_frames_cache = []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
# Settings live on the WindowManager rather than the Scene. Changing a Scene
# property from the UI pushes an undo step, which with thousands of strips is
# very slow; WindowManager properties don't.

def _settings(context):
    return context.window_manager.subtitle_search


def _top_level_strips(scene):
    """Strips of the scene's sequencer (not descending into meta strips)."""
    se = scene.sequence_editor
    if se is None:
        return []
    return se.strips


def _timecode(frame, fps):
    secs = max(0, int(frame / fps)) if fps else 0
    return f"{secs // 3600}:{(secs % 3600) // 60:02d}:{secs % 60:02d}"


def _snippet(text, match, context_chars):
    """Text around the match, with ellipses where it has been cut."""
    start = max(0, match.start() - context_chars)
    end = min(len(text), match.end() + context_chars)
    out = " ".join(text[start:end].split())
    if start > 0:
        out = "\u2026" + out
    if end < len(text):
        out = out + "\u2026"
    return out


def _redraw_all(context):
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            area.tag_redraw()


def _set_index(settings, index):
    global _suppress_index_update
    _suppress_index_update = True
    try:
        settings.results_index = index
    finally:
        _suppress_index_update = False


def _frames(s):
    global _frames_cache
    if len(_frames_cache) != len(s.results):
        _frames_cache = [r.frame for r in s.results]
    return _frames_cache


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def run_search(context):
    global _frames_cache
    scene = context.workspace.sequencer_scene
    if scene is None:
        return
    s = _settings(context)
    s.regex_error = False

    query = s.query
    if not query:
        s.results.clear()
        _frames_cache = []
        _set_index(s, 0)
        _redraw_all(context)
        return

    flags = 0 if s.case_sensitive else re.IGNORECASE
    try:
        pattern = re.compile(query if s.use_regex else re.escape(query), flags)
    except re.error:
        # Keep the previous results; the panel shows an "Invalid regex" message.
        s.regex_error = True
        _redraw_all(context)
        return

    strips = _top_level_strips(scene)
    hits = []
    for strip in strips:
        if strip.type != 'TEXT' or not NAME_RE.match(strip.name):
            continue
        text = strip.text
        m = pattern.search(text)
        if m:
            hits.append((strip.frame_final_start, strip.name, text, m))
    hits.sort(key=lambda h: (h[0], h[1]))

    fps = scene.render.fps / scene.render.fps_base
    s.results.clear()
    for frame, name, text, m in hits:
        item = s.results.add()
        item.strip_name = name
        item.frame = frame
        item.text = _snippet(text, m, s.context_chars)
        item.full_text = " ".join(text.split())
        item.timecode = _timecode(frame, fps)
    _frames_cache = [h[0] for h in hits]

    # Point the active row at the first match at or after the playhead,
    # without jumping anywhere.
    index = bisect.bisect_left(_frames_cache, scene.frame_current)
    if index >= len(hits):
        index = 0
    _set_index(s, index)

    if s.select_matches:
        names = {h[1] for h in hits}
        for strip in strips:
            strip.select = strip.name in names

    _redraw_all(context)


def _on_scene_change():
    context = bpy.context
    if context.window is not None:
        run_search(context)


_scene_change_owner = object()


def _subscribe_scene_change():
    bpy.msgbus.clear_by_owner(_scene_change_owner)
    bpy.msgbus.subscribe_rna(
        key=(bpy.types.WorkSpace, "sequencer_scene"),
        owner=_scene_change_owner,
        args=(),
        notify=_on_scene_change,
    )


def _on_search_change(self, context):
    """Option changed: search again, but don't move anything."""
    run_search(context)


def _on_query_change(self, context):
    # The field updates on every keystroke. If the text is the same as the
    # last search, this update must be the final commit (Enter / click away).
    committed = self.query == self.last_query
    self.last_query = self.query

    run_search(context)
    if self.regex_error or not self.results:
        return
    if committed or self.jump_while_typing:
        goto_result(context, self.results_index)


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------

def _pan_to_frame(context, area, region, frame):
    """Pan (never zoom) one timeline so `frame` is in the middle, without
    touching the playhead.

    view2d.scroll_left/right ignore their arguments and always move the view
    by a fixed step, so measure the step with one call, then repeat.
    """
    v2d = region.view2d
    centre = region.width / 2.0

    def offset():
        return v2d.view_to_region(frame, 0, clip=False)[0] - centre

    def scroll(right, times):
        op = bpy.ops.view2d.scroll_right if right else bpy.ops.view2d.scroll_left
        with context.temp_override(area=area, region=region):
            for _ in range(times):
                op()

    first = offset()
    if abs(first) < 1:
        return
    scroll(first > 0, 1)
    step = abs(first - offset())
    if step < 1:
        return
    n = round(offset() / step)  # negative if the first step overshot
    scroll(n > 0, min(abs(n), 5000))


def _centre_view(context, frame):
    """Centre every sequencer timeline on `frame`, keeping the zoom.

    If the playhead is already there, sequencer.view_frame does it. Otherwise
    the view is panned directly, and the playhead is never moved.
    """
    for area in context.screen.areas:
        if area.type != 'SEQUENCE_EDITOR':
            continue
        if area.spaces.active.view_type == 'PREVIEW':
            continue  # no timeline in this area
        region = next((r for r in area.regions if r.type == 'WINDOW'), None)
        if region is None:
            continue
        if context.workspace.sequencer_scene.frame_current == frame:
            with context.temp_override(area=area, region=region):
                bpy.ops.sequencer.view_frame()
        else:
            _pan_to_frame(context, area, region, frame)


def _apply_result(context, index):
    s = _settings(context)
    if not (0 <= index < len(s.results)):
        return
    frame = s.results[index].frame
    if s.move_playhead:
        context.workspace.sequencer_scene.frame_current = frame
    if s.centre_view:
        _centre_view(context, frame)
    _redraw_all(context)


def goto_result(context, index):
    s = _settings(context)
    if not (0 <= index < len(s.results)):
        return
    _set_index(s, index)
    _apply_result(context, index)


def _on_index_change(self, context):
    if _suppress_index_update:
        return
    _apply_result(context, self.results_index)


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------

class SUBTITLESEARCH_PG_result(PropertyGroup):
    strip_name: StringProperty()
    frame: IntProperty()
    text: StringProperty()       # snippet shown in the list
    full_text: StringProperty()  # whole subtitle, shown below the list
    timecode: StringProperty()


class SUBTITLESEARCH_PG_settings(PropertyGroup):
    query: StringProperty(
        name="Search",
        description="Text to look for in subtitle strips",
        options={'TEXTEDIT_UPDATE'},
        update=_on_query_change,
    )
    last_query: StringProperty()
    case_sensitive: BoolProperty(
        name="Match case",
        description="Match upper and lower case exactly",
        default=False,
        update=_on_search_change,
    )
    use_regex: BoolProperty(
        name="Regex",
        description="Treat the search text as a regular expression",
        default=False,
        update=_on_search_change,
    )
    regex_error: BoolProperty(default=False)

    move_playhead: BoolProperty(
        name="Move playhead",
        description="Move the playhead to the start of the match",
        default=True,
    )
    centre_view: BoolProperty(
        name="Centre view",
        description="Pan the sequencer horizontally so the match is in the middle (zoom is unchanged)",
        default=True,
    )
    wrap: BoolProperty(
        name="Wrap around",
        description="Continue from the other end after the last or first match",
        default=True,
    )
    jump_while_typing: BoolProperty(
        name="Jump while typing",
        description="Jump to the first match after every keystroke, not only when you press Enter",
        default=False,
    )
    select_matches: BoolProperty(
        name="Select matching strips",
        description="Select all matching strips and deselect everything else (replaces your selection)",
        default=False,
        update=_on_search_change,
    )
    context_chars: IntProperty(
        name="Context characters",
        description="How many characters to show before and after the match in the list",
        default=12,
        min=0,
        max=200,
        update=_on_search_change,
    )

    results: CollectionProperty(type=SUBTITLESEARCH_PG_result)
    results_index: IntProperty(default=0, min=0, update=_on_index_change)


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------

class SUBTITLESEARCH_OT_step(Operator):
    bl_idname = "subtitle_search.step"
    bl_label = "Step to match"
    bl_options = {'INTERNAL'}

    direction: EnumProperty(
        items=[
            ('NEXT', "Next", "Go to the next match"),
            ('PREV', "Previous", "Go to the previous match"),
        ],
        default='NEXT',
    )

    @classmethod
    def description(cls, context, properties):
        if properties.direction == 'PREV':
            return "Go to the previous match"
        return "Go to the next match"

    def execute(self, context):
        s = _settings(context)
        n = len(s.results)
        if n == 0:
            self.report({'INFO'}, "No matches")
            return {'CANCELLED'}

        forward = self.direction == 'NEXT'
        idx = None

        if s.move_playhead:
            # Compare against the playhead, so manual scrubbing is respected.
            current = context.workspace.sequencer_scene.frame_current
            frames = _frames(s)
            if forward:
                i = bisect.bisect_right(frames, current)
                idx = i if i < n else (0 if s.wrap else None)
            else:
                i = bisect.bisect_left(frames, current) - 1
                idx = i if i >= 0 else (n - 1 if s.wrap else None)
        else:
            # The playhead doesn't move, so step through the list instead.
            i = min(s.results_index, n - 1)
            idx = i + 1 if forward else i - 1
            if idx < 0 or idx >= n:
                idx = idx % n if s.wrap else None

        if idx is None:
            self.report({'INFO'}, "No more matches")
            return {'CANCELLED'}

        goto_result(context, idx)
        return {'FINISHED'}


class SUBTITLESEARCH_OT_refresh(Operator):
    bl_idname = "subtitle_search.refresh"
    bl_label = "Refresh results"
    bl_description = "Search again (use after editing or adding subtitle strips)"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        run_search(context)
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

class SUBTITLESEARCH_UL_results(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        split = layout.split(factor=0.2, align=True)
        split.label(text=item.timecode)
        split.label(text=item.text)


class SUBTITLESEARCH_PT_main(Panel):
    bl_label = "Subtitle search"
    bl_idname = "SUBTITLESEARCH_PT_main"
    bl_space_type = 'SEQUENCE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Subtitles"

    def draw(self, context):
        layout = self.layout
        s = _settings(context)

        layout.prop(s, "query", text="", icon='VIEWZOOM')

        row = layout.row(align=True)
        row.prop(s, "case_sensitive", toggle=True)
        row.prop(s, "use_regex", toggle=True)

        n = len(s.results)
        if s.regex_error:
            layout.label(text="Invalid regex", icon='ERROR')
        elif not s.query:
            layout.label(text="Enter a search term")
        elif n == 0:
            layout.label(text="No matches")
        else:
            layout.label(text=f"Match {min(s.results_index, n - 1) + 1} of {n}")

        layout.template_list(
            "SUBTITLESEARCH_UL_results", "",
            s, "results",
            s, "results_index",
            rows=8,
        )

        # Full text of the active result, wrapped to the sidebar width.
        if 0 <= s.results_index < n:
            item = s.results[s.results_index]
            scale = context.preferences.view.ui_scale
            width = max(16, int((context.region.width - 40) / (6.5 * scale)))
            box = layout.box()
            col = box.column(align=True)
            col.scale_y = 0.85
            col.label(text=f"{item.timecode}  \u00b7  {item.strip_name}")
            for line in textwrap.wrap(item.full_text, width=width)[:8]:
                col.label(text=line)

        row = layout.row(align=True)
        op = row.operator("subtitle_search.step", text="Previous", icon='TRIA_LEFT')
        op.direction = 'PREV'
        op = row.operator("subtitle_search.step", text="Next", icon='TRIA_RIGHT')
        op.direction = 'NEXT'

        layout.operator("subtitle_search.refresh", icon='FILE_REFRESH')


class SUBTITLESEARCH_PT_options(Panel):
    bl_label = "Options"
    bl_idname = "SUBTITLESEARCH_PT_options"
    bl_space_type = 'SEQUENCE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Subtitles"
    bl_parent_id = "SUBTITLESEARCH_PT_main"

    def draw(self, context):
        layout = self.layout
        s = _settings(context)
        col = layout.column(align=True)
        col.prop(s, "move_playhead")
        col.prop(s, "centre_view")
        col.prop(s, "wrap")
        col.prop(s, "jump_while_typing")
        col.prop(s, "select_matches")
        layout.prop(s, "context_chars")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

classes = (
    SUBTITLESEARCH_PG_result,
    SUBTITLESEARCH_PG_settings,
    SUBTITLESEARCH_OT_step,
    SUBTITLESEARCH_OT_refresh,
    SUBTITLESEARCH_UL_results,
    SUBTITLESEARCH_PT_main,
    SUBTITLESEARCH_PT_options,
)


def _remove_old_scene_data():
    """Version 0.1/0.2 stored settings (and all results) on the scenes."""
    try:
        for scene in bpy.data.scenes:
            if "subtitle_search" in scene:
                del scene["subtitle_search"]
    except Exception:
        pass


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.subtitle_search = PointerProperty(type=SUBTITLESEARCH_PG_settings)
    _remove_old_scene_data()
    _subscribe_scene_change()


def unregister():
    bpy.msgbus.clear_by_owner(_scene_change_owner)
    del bpy.types.WindowManager.subtitle_search
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()

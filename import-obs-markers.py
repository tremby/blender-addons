bl_info = {
    "name": "OBS Marker Log Importer",
    "author": "Bart Nagel",
    "version": (1, 1, 0),
    "blender": (5, 1, 0),
    "location": "Sequencer > Sidebar",
    "description": "Import OBS JSONL marker logs as timeline markers",
    "category": "Sequencer",
}


import bpy
import json
from pathlib import Path


LOG_SUFFIX = ".log.jsonl"
MARKER_PREFIX = "_obs_"


def get_selected_movie_strip():
    strips = [
        s
        for s in bpy.context.selected_editable_strips
        if s.type == "MOVIE"
    ]

    if len(strips) != 1:
        raise RuntimeError(
            "Select exactly one movie strip."
        )

    return strips[0]


def get_log_path(strip):
    movie_path = Path(
        bpy.path.abspath(strip.filepath)
    )

    return Path(
        str(movie_path) + LOG_SUFFIX
    )


def event_name(event):
    match event["type"]:
        case "attention":
            return "Attention"

        case "scene_change":
            return (
                f"Change to scene "
                f"{event['scene']}"
            )

        case "source_visibility":
            return (
                (
                    "Show"
                    if event["visible"]
                    else "Hide"
                )
                +
                f" layer {event['source']}"
            )

        case "source_mute":
            return (
                (
                    "Unmute"
                    if not event["muted"]
                    else "Mute"
                )
                +
                f" {event['source']}"
            )

        case "pause":
            return "Pause"

        case "pause_duration":
            return (
                f"Pause for "
                f"{event['duration']:.2f}s"
            )

        case "audio_buffer_change":
            return (
                "Audio buffer change: "
                f"{event.get('details', '')}"
            )

    return None


def import_log(strip, log_path, limit_to_strip=True):
    fps = (
        bpy.context.scene.render.fps
        /
        bpy.context.scene.render.fps_base
    )

    markers = {}

    with open(log_path) as f:
        for line in f:
            event = json.loads(line)

            timestamp = event.get("time")
            if timestamp is None:
                continue

            name = event_name(event)
            if name is None:
                continue

            frame = int(
                strip.content_start
                - strip.content_trim_start
                + round(timestamp * fps)
            )

            if limit_to_strip and not (
                strip.frame_final_start <= frame < strip.frame_final_end
            ):
                continue

            markers.setdefault(frame, []).append(name)

    for frame, names in markers.items():
        bpy.context.scene.timeline_markers.new(
            MARKER_PREFIX + ", ".join(names),
            frame=frame,
        )

    return len(markers)


class OBSMARKER_OT_import(bpy.types.Operator):
    bl_idname = "obs_marker.import_log"
    bl_label = "Import OBS Marker Log"
    bl_description = "Import OBS JSONL marker log for selected video strip"
    bl_options = {"REGISTER", "UNDO"}

    limit_to_strip: bpy.props.BoolProperty(
        name="Only within strip",
        description="Only import markers within the selected strip's timeline range",
        default=True,
    )

    def execute(self, context):
        try:
            strip = get_selected_movie_strip()
            log_path = get_log_path(strip)

            if not log_path.exists():
                raise RuntimeError(
                    f"Log file not found:\n{log_path}"
                )

            count = import_log(
                strip,
                log_path,
                self.limit_to_strip,
            )

            self.report(
                {"INFO"},
                f"Imported {count} OBS markers",
            )

        except Exception as e:
            self.report(
                {"ERROR"},
                str(e),
            )
            return {"CANCELLED"}

        return {"FINISHED"}


classes = (
    OBSMARKER_OT_import,
)

def draw_strip_menu(self, context):
    self.layout.separator()
    self.layout.operator(
        OBSMARKER_OT_import.bl_idname,
        text="Import OBS Marker Log",
    )

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.SEQUENCER_MT_strip.append(draw_strip_menu)


def unregister():
    bpy.types.SEQUENCER_MT_strip.remove(draw_strip_menu)

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()

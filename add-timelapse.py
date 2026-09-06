bl_info = {
    "name": "Fast-forward timelapse (old version)",
    "author": "Bart Nagel",
    "version": (0, 1, 0),
    "blender": (5, 1, 0),
    "location": "Sequencer > Strip > Retiming",
    "description": "Calculate timelapse retiming markers",
    "category": "Sequencer",
}

import bpy
from bpy.props import IntProperty


# ----------------------------------------------------------------------
# Calculation
# ----------------------------------------------------------------------

def calculate_timelapse(strip, scene, output_frames=None):
    fps = scene.render.fps / scene.render.fps_base

    source_frames = strip.duration
    source_seconds = source_frames / fps

    RAMP_FRACTION = 0.2      # each ramp, as a fraction of output length
    RAMP_CAP_SECONDS = 2.0   # each ramp, hard cap in seconds

    def ramp_seconds_for(out_seconds):
        # each ramp (up or down) individually = lesser of fraction or cap
        return min(RAMP_FRACTION * out_seconds, RAMP_CAP_SECONDS)

    def speed_for(out_seconds):
        t_r = ramp_seconds_for(out_seconds)
        # S = t_r + speed * (Y - t_r)   [see derivation note below]
        return (source_seconds - t_r) / (out_seconds - t_r)

    def max_source_for(out_seconds, max_speed=64):
        t_r = ramp_seconds_for(out_seconds)
        return t_r + max_speed * (out_seconds - t_r)

    if output_frames is None:
        max_at_5s = max_source_for(5)
        max_at_10s = max_source_for(10)

        if source_seconds <= max_at_5s:
            output_seconds = 5
            speed = speed_for(output_seconds)

        elif source_seconds <= max_at_10s:
            speed = 64
            t_r = ramp_seconds_for(10)  # boundary value; see note
            output_seconds = (source_seconds + speed * -t_r + t_r) / speed \
                if False else (source_seconds - t_r) / speed + t_r
            # simplifies to:
            output_seconds = t_r + (source_seconds - t_r) / speed

        else:
            output_seconds = 10
            speed = speed_for(output_seconds)

        output_frames = round(output_seconds * fps)

    output_seconds = output_frames / fps
    speed = speed_for(output_seconds)
    ramp_frames = round(ramp_seconds_for(output_seconds) * fps)
    if ramp_frames % 2:
        # keep the corner exactly centered; nudge total ramp width to the
        # nearest even value so both halves match
        ramp_frames += 1

    output_start = strip.left_handle
    output_end = output_start + output_frames

    return {
        "output_frames": output_frames,
        "speed": speed,
        "ramp_up_start": output_start,
        "ramp_up_midpoint": output_start + ramp_frames // 2,
        "ramp_up_end": output_start + ramp_frames,
        "ramp_down_start": output_end - ramp_frames,
        "ramp_down_midpoint": output_end - ramp_frames // 2,
        "ramp_down_end": output_end,
    }


# ----------------------------------------------------------------------
# Validation
# ----------------------------------------------------------------------

def validate_selection(context):
    strips = context.selected_strips

    if not strips:
        return None, "No strips selected."

    first = strips[0]

    if first.type not in {'MOVIE', 'SOUND'}:
        return None, "Only Movie and Sound strips are supported."

    for s in strips:
        if s.type not in {'MOVIE', 'SOUND'}:
            return None, "Only Movie and Sound strips are supported."

        if (
            s.left_handle != first.left_handle
            or s.right_handle != first.right_handle
            or s.duration != first.duration
        ):
            return None, "Selected strips must have identical timing."

        if len(s.retiming_keys.values()) != 0:
            return None, "Selected strips already have retiming."

    return first, None


# ----------------------------------------------------------------------
# Operator
# ----------------------------------------------------------------------

class FFWD_OT_add_timelapse(bpy.types.Operator):
    bl_idname = "sequencer.ffwd_add_timelapse"
    bl_label = "Add calculated timelapse retiming markers"
    bl_options = {'REGISTER', 'UNDO'}

    output_frames: IntProperty(
        name="Output Length",
        description="Output duration in frames",
        default=300,
        min=1,
        options={'SKIP_SAVE'},
    )

    def invoke(self, context, event):
        strip, error = validate_selection(context)

        if error:
            self.report({'ERROR'}, error)
            return {'CANCELLED'}

        result = calculate_timelapse(strip, context.scene)

        self.output_frames = result["output_frames"]

        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        self.layout.prop(self, "output_frames")

    def execute(self, context):
        strip, error = validate_selection(context)

        if error:
            self.report({'ERROR'}, error)
            return {'CANCELLED'}

        result = calculate_timelapse(
            strip,
            context.scene,
            self.output_frames,
        )

        speed_percent = result["speed"] * 100

        markers = [
            (
                result["ramp_up_start"],
                "_ffwd ramp up start",
            ),
            (
                result["ramp_up_midpoint"],
                "_ffwd ramp up midpoint",
            ),
            (
                result["ramp_up_end"],
                f"_ffwd ramp up end ({speed_percent:.1f}%)",
            ),
            (
                result["ramp_down_start"],
                "_ffwd ramp down start",
            ),
            (
                result["ramp_down_midpoint"],
                "_ffwd ramp down midpoint",
            ),
            (
                result["ramp_down_end"],
                "_ffwd ramp down end",
            ),
        ]

        for frame, name in markers:
            context.scene.timeline_markers.new(
                name,
                frame=frame,
            )

        self.report(
            {'INFO'},
            f"Added timelapse markers: "
            f"{result['output_frames']} frames, "
            f"{speed_percent:.1f}% max speed",
        )

        return {'FINISHED'}


# ----------------------------------------------------------------------
# Menu
# ----------------------------------------------------------------------

def draw_menu(self, context):
    self.layout.separator()
    self.layout.operator(
        FFWD_OT_add_timelapse.bl_idname,
        icon='TIME',
    )


classes = (
    FFWD_OT_add_timelapse,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.SEQUENCER_MT_strip_retiming.append(draw_menu)


def unregister():
    bpy.types.SEQUENCER_MT_strip_retiming.remove(draw_menu)

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()

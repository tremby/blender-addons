bl_info = {
    "name": "Fast-forward timelapse",
    "author": "Bart Nagel",
    "version": (0, 3, 0),
    "blender": (5, 2, 0),
    "location": "Sequencer > Strip > Retiming",
    "description": "Add timelapse retiming keys",
    "category": "Sequencer",
}

import bpy
import math
from bpy.props import IntProperty, FloatProperty, EnumProperty


# ----------------------------------------------------------------------
# Calculation
# ----------------------------------------------------------------------

def calculate_timelapse(strip, scene, output_frames=None):
    fps = scene.render.fps / scene.render.fps_base

    source_frames = strip.duration

    RAMP_FRACTION = 0.2      # each ramp, as a fraction of output length
    RAMP_CAP_SECONDS = 2.0   # each ramp, hard cap in seconds
    MIN_OUTPUT_SECONDS = 3   # shortest auto-selected output length

    def ramp_frames_for(out_frames):
        by_fraction = RAMP_FRACTION * out_frames
        by_cap = RAMP_CAP_SECONDS * fps
        return round(min(by_fraction, by_cap))

    def speed_for(out_frames):
        rf = ramp_frames_for(out_frames)
        return (source_frames - rf) / (out_frames - rf)

    def max_source_for(out_frames, max_speed=64):
        rf = ramp_frames_for(out_frames)
        return rf + max_speed * (out_frames - rf)

    if output_frames is None:
        min_frames = round(MIN_OUTPUT_SECONDS * fps)
        cap_frames = round(10 * fps)

        if source_frames <= max_source_for(min_frames):
            output_frames = min_frames

        elif source_frames <= max_source_for(cap_frames):
            speed = 64
            rf = ramp_frames_for(cap_frames)
            output_frames = round(rf + (source_frames - rf) / speed)

        else:
            output_frames = cap_frames

    speed = speed_for(output_frames)
    ramp_frames = ramp_frames_for(output_frames)

    output_start = strip.left_handle
    output_end = output_start + output_frames

    half1 = ramp_frames // 2
    half2 = ramp_frames - half1

    ramp_up_midpoint = output_start + half1
    ramp_down_midpoint = output_end - half2

    corner_width = ramp_down_midpoint - ramp_up_midpoint
    ramp_down_midpoint_initial = ramp_up_midpoint + math.floor(speed * corner_width)

    # Each transition handle can be stretched at most `half - 1` frames
    # before it collides with the next key, per observed behaviour.
    transition_duration = max(0, min(half1, half2) - 1)

    return {
        "output_frames": output_frames,
        "speed": speed,
        "ramp_up_midpoint": ramp_up_midpoint,
        "ramp_down_midpoint_initial": ramp_down_midpoint_initial,
        "ramp_down_midpoint": ramp_down_midpoint,
        "transition_duration": transition_duration,
    }


def output_frames_for_speed(source_frames, fps, target_speed):
    RAMP_FRACTION = 0.2
    RAMP_CAP_SECONDS = 2.0
    cap_frames = RAMP_CAP_SECONDS * fps

    y_fraction = source_frames / (target_speed * (1 - RAMP_FRACTION) + RAMP_FRACTION)

    if y_fraction * RAMP_FRACTION <= cap_frames:
        y = y_fraction
    else:
        y = cap_frames + (source_frames - cap_frames) / target_speed

    return max(1, round(y))


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
    bl_label = "Add timelapse retiming keys"
    bl_options = {'REGISTER', 'UNDO'}

    input_mode: EnumProperty(
        name="Specify",
        items=[
            ('FRAMES', "Output Length", "Choose the total output duration"),
            ('SPEED', "Cruising Speed", "Choose the plateau speed"),
        ],
        default='FRAMES',
        options={'SKIP_SAVE'},
    )
    output_frames: IntProperty(
        name="Output Length",
        description="Output duration in frames",
        default=180,
        min=1,
        options={'SKIP_SAVE'},
    )
    target_speed_percent: FloatProperty(
        name="Cruising Speed (%)",
        description="Desired speed during the constant-speed plateau",
        default=6400.0,
        min=100.0,
        options={'SKIP_SAVE'},
    )

    def invoke(self, context, event):
        strip, error = validate_selection(context)

        if error:
            self.report({'ERROR'}, error)
            return {'CANCELLED'}

        result = calculate_timelapse(strip, context.scene)

        self.output_frames = result["output_frames"]
        self.target_speed_percent = result["speed"] * 100

        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "input_mode", expand=True)
        if self.input_mode == 'FRAMES':
            layout.prop(self, "output_frames")
        else:
            layout.prop(self, "target_speed_percent")

    def execute(self, context):
        strip, error = validate_selection(context)

        if error:
            self.report({'ERROR'}, error)
            return {'CANCELLED'}

        strips = list(context.selected_strips)
        fps = context.scene.render.fps / context.scene.render.fps_base

        if self.input_mode == 'SPEED':
            output_frames = output_frames_for_speed(
                strip.duration, fps, self.target_speed_percent / 100,
            )
        else:
            output_frames = self.output_frames

        result = calculate_timelapse(
            strip,
            context.scene,
            output_frames,
        )

        speed_percent = result["speed"] * 100

        context.scene.timeline_markers.new(
            f"_ffwd set speed to {speed_percent:.2f}%",
            frame=result["ramp_down_midpoint_initial"],
        )
        context.scene.timeline_markers.new(
            f"_ffwd add transition",
            frame=result["ramp_up_midpoint"],
        )
        context.scene.timeline_markers.new(
            f"_ffwd add transition",
            frame=result["ramp_down_midpoint"],
        )

        bpy.ops.sequencer.retiming_show()

        for s in strips:
            for other in strips:
                other.select = (other is s)

            context.scene.sequence_editor.active_strip = s

            r1 = bpy.ops.sequencer.retiming_key_add(
                timeline_frame=result["ramp_up_midpoint"],
            )
            r2 = bpy.ops.sequencer.retiming_key_add(
                timeline_frame=result["ramp_down_midpoint_initial"],
            )

        for s in strips:
            s.select = True

        context.scene.frame_set(result["ramp_down_midpoint_initial"])
        bpy.ops.sequencer.view_frame()

        self.report(
            {'INFO'},
            f"Added timelapse keys: "
            f"{result['output_frames']} frames, "
            f"{speed_percent:.2f}% max speed, "
            f"stretch transitions {result['transition_duration']} frames each side",
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

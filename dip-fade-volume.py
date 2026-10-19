bl_info = {
    "name": "Dip Fade Volume (VSE)",
    "author": "Claude",
    "version": (1, 1),
    "blender": (5, 2, 0),
    "location": "Sequencer > Strip > Dip Fade Volume",
    "description": "Fade sound strip(s) down to 0 and back up across a frame range",
    "category": "Sequencer",
}

import bpy


def _selected_sound_strips(context):
    return [s for s in (context.selected_strips or []) if s.type == 'SOUND']


class SEQUENCER_OT_dip_fade(bpy.types.Operator):
    bl_idname = "sequencer.dip_fade_volume"
    bl_label = "Dip Fade Volume"
    bl_description = (
        "Fade sound strip(s) from current volume to 0, hold, "
        "then fade back up, across the scene's Preview Range"
    )
    bl_options = {'REGISTER', 'UNDO'}

    apply_to: bpy.props.EnumProperty(
        name="Apply To",
        description="Which sound strips to apply the dip fade to",
        items=[
            ('ACTIVE', "Active Strip Only", "Only the active sound strip"),
            ('SELECTED', "All Selected Sound Strips", "Every selected sound strip"),
        ],
        default='ACTIVE',
    )

    fade_frames: bpy.props.IntProperty(
        name="Fade Length",
        description="Number of frames for each fade in/out",
        default=10,
        min=1,
    )

    target_volume: bpy.props.FloatProperty(
        name="Target Volume",
        description="Volume level to dip down to in the middle of the selection",
        default=0.0,
        min=0.0,
    )

    use_preview_range: bpy.props.BoolProperty(
        name="Use Preview Range",
        description="Use the scene's Preview Range as the selection (Shift+O / Ctrl-drag timeline ruler to set it)",
        default=True,
    )

    start_frame: bpy.props.IntProperty(
        name="Start Frame",
        description="Used only when 'Use Preview Range' is off",
        default=0,
    )

    end_frame: bpy.props.IntProperty(
        name="End Frame",
        description="Used only when 'Use Preview Range' is off",
        default=100,
    )

    @classmethod
    def poll(cls, context):
        return bool(context.scene.sequence_editor and context.selected_strips)

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True

        # Only offer the choice when it actually matters.
        if len(_selected_sound_strips(context)) > 1:
            layout.prop(self, "apply_to")

        layout.prop(self, "fade_frames")
        layout.prop(self, "target_volume")
        layout.prop(self, "use_preview_range")

        if not self.use_preview_range:
            layout.prop(self, "start_frame")
            layout.prop(self, "end_frame")

    def execute(self, context):
        scene = context.scene

        if self.use_preview_range:
            if not scene.use_preview_range:
                self.report(
                    {'ERROR'},
                    "No Preview Range is set. Ctrl-drag the timeline ruler "
                    "(or set Start/End below) to define the selection first."
                )
                return {'CANCELLED'}
            start = scene.frame_preview_start
            end = scene.frame_preview_end
        else:
            start = self.start_frame
            end = self.end_frame

        if end - start <= self.fade_frames * 2:
            self.report({'ERROR'}, "Selection is too short for the chosen fade length")
            return {'CANCELLED'}

        sound_strips = _selected_sound_strips(context)

        # With a single sound strip selected, there's nothing to choose between.
        if len(sound_strips) > 1 and self.apply_to == 'ACTIVE':
            active = context.active_strip
            if active is None or active.type != 'SOUND':
                self.report({'ERROR'}, "The active strip is not a sound strip")
                return {'CANCELLED'}
            strips = [active]
        else:
            strips = sound_strips

        if not strips:
            self.report({'WARNING'}, "No sound strips were selected")
            return {'CANCELLED'}

        for strip in strips:
            original_volume = strip.volume

            for frame, value in (
                (start, original_volume),
                (start + self.fade_frames, self.target_volume),
                (end - self.fade_frames, self.target_volume),
                (end, original_volume),
            ):
                strip.volume = value
                strip.keyframe_insert(data_path="volume", frame=frame)

            strip.volume = original_volume

        self.report({'INFO'}, f"Applied dip fade to {len(strips)} strip(s)")
        return {'FINISHED'}


def menu_func(self, context):
    self.layout.operator(SEQUENCER_OT_dip_fade.bl_idname)


def register():
    bpy.utils.register_class(SEQUENCER_OT_dip_fade)
    bpy.types.SEQUENCER_MT_strip.append(menu_func)


def unregister():
    bpy.types.SEQUENCER_MT_strip.remove(menu_func)
    bpy.utils.unregister_class(SEQUENCER_OT_dip_fade)


if __name__ == "__main__":
    register()

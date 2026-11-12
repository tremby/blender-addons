bl_info = {
    "name": "Add and/or Rename Marker",
    "author": "Bart Nagel",
    "version": (1, 2, 0),
    "blender": (5, 2, 0),
    "location": "Sequencer > Markers menu",
    "description": "Adds a marker at the current playhead if none exists and prompts for a name",
    "category": "Sequencer",
}

import re
import bpy


def marker_at_frame(scene, frame):
    return next((m for m in scene.timeline_markers if m.frame == frame), None)


def ends_in_number(name):
    return re.search(r"\d+$", name) is not None


def matching_markers(scenes, name):
    pattern = re.compile(rf"^{re.escape(name)}(?:\s+(\d+)(.*))?$")

    matches = []
    for scene in scenes:
        for marker in scene.timeline_markers:
            match = pattern.fullmatch(marker.name)
            if match:
                number = int(match.group(1)) if match.group(1) else 1
                suffix = match.group(2) or ""
                matches.append((scene, marker, number, suffix))

    return matches


class SEQUENCER_OT_AddAndOrRenameMarker(bpy.types.Operator):
    """Add a marker at the current playhead position if none exists, then prompt for a name."""
    bl_idname = "sequencer.add_and_or_rename_marker"
    bl_label = "Add and/or rename marker"
    bl_property = "marker_name"

    marker_name: bpy.props.StringProperty(name="Marker name")
    numbering_mode: bpy.props.EnumProperty(
        name="Numbering",
        items=[
            ('EXACT', "Use exact name", ""),
            ('CURRENT', "Sequential (current scene)", ""),
            ('ALL', "Sequential (all scenes)", ""),
        ],
        default='ALL',
    )
    _new_marker_created: bool = False
    _marker_ref = None
    _prev_selected_markers = []

    def invoke(self, context, event):
        scene = context.scene
        frame = scene.frame_current

        # Store previously selected markers
        self._prev_selected_markers = [m for m in scene.timeline_markers if m.select]

        marker = marker_at_frame(scene, frame)
        if marker is None:
            marker = scene.timeline_markers.new("Marker", frame=frame)
            self._new_marker_created = True
        else:
            self._new_marker_created = False

        self._marker_ref = marker
        self.marker_name = marker.name

        return context.window_manager.invoke_props_dialog(self, title="Add and/or rename marker")


    def execute(self, context):
        scene = context.scene
        marker = self._marker_ref

        if not marker:
            return {'FINISHED'}

        name = self.marker_name

        if self.numbering_mode == 'EXACT' or ends_in_number(name):
            marker.name = name
        else:
            scenes = (
                [scene]
                if self.numbering_mode == 'CURRENT'
                else sorted(bpy.data.scenes, key=lambda s: s.name)
            )

            matches = matching_markers(scenes, name)

            # Only do sequence handling when another marker already has
            # the exact unnumbered name.
            exact_other = any(
                m is not marker and number == 1 and suffix == ""
                for _, m, number, suffix in matches
            )

            if exact_other:
                scene_order = {s.name: i for i, s in enumerate(scenes)}

                matches.sort(
                    key=lambda item: (scene_order[item[0].name], item[1].frame)
                )

                # Check the existing sequence.
                numbers = [number for _, _, number, _ in matches]
                sequential = all(
                    b == a + 1
                    for a, b in zip(numbers, numbers[1:])
                )

                if not sequential:
                    self.report(
                        {'WARNING'},
                        f"Marker sequence for '{name}' is not strictly sequential",
                    )

                marker_position = (scene_order[scene.name], marker.frame)

                before = [
                    (s, m, number, suffix)
                    for s, m, number, suffix in matches
                    if m is not marker
                    and (scene_order[s.name], m.frame) < marker_position
                ]

                after = [
                    (s, m, number, suffix)
                    for s, m, number, suffix in matches
                    if m is not marker
                    and (scene_order[s.name], m.frame) >= marker_position
                ]

                highest_before = max(
                    (number for _, _, number, _ in before),
                    default=0,
                )
                new_number = highest_before + 1

                # Markers after the current position need to move up if necessary.
                next_number = new_number
                for existing_scene, existing_marker, number, suffix in after:
                    if number < next_number:
                        existing_marker.name = f"{name} {next_number}{suffix}"
                    elif number == next_number:
                        existing_marker.name = f"{name} {next_number + 1}{suffix}"
                        next_number += 1

                    next_number = max(next_number, number + 1)

                marker.name = f"{name} {new_number}"
            else:
                marker.name = name

        # Select only this marker in the current scene.
        for m in scene.timeline_markers:
            m.select = (m == marker)

        self.report({'INFO'}, f"Marker name set to: {marker.name}")

        return {'FINISHED'}


    def cancel(self, context):
        scene = context.scene

        if self._new_marker_created and self._marker_ref:
            # Delete newly created marker
            scene.timeline_markers.remove(self._marker_ref)

            # Restore previous selection
            for m in scene.timeline_markers:
                m.select = (m in self._prev_selected_markers)

            self.report({'INFO'}, "New marker creation cancelled")

        return {'CANCELLED'}


def menu_func(self, context):
    self.layout.operator(
        SEQUENCER_OT_AddAndOrRenameMarker.bl_idname,
        text=SEQUENCER_OT_AddAndOrRenameMarker.bl_label,
    )


def register():
    bpy.utils.register_class(SEQUENCER_OT_AddAndOrRenameMarker)
    bpy.types.SEQUENCER_MT_marker.append(menu_func)


def unregister():
    bpy.types.SEQUENCER_MT_marker.remove(menu_func)
    bpy.utils.unregister_class(SEQUENCER_OT_AddAndOrRenameMarker)


if __name__ == '__main__':
    register()

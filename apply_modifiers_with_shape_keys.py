'''
Copyright (C) 2025 Wayne Dixon
wayne@cgcookie.com

Created by Wayne Dixon

    This file is part of Apply modifier with shape keys

    Export to .blend is free software; you can redistribute it and/or
    modify it under the terms of the GNU General Public License
    as published by the Free Software Foundation; either version 3
    of the License, or (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program; if not, see <https://www.gnu.org/licenses/>.

'''

import bpy

# Local imports
from .functions import apply_modifiers_with_shape_keys, resolve_method


METHOD_LABELS = {
    'REAPPLY': "Re-apply Per Shape",
    'INTERPOLATE': "Interpolate Offsets",
}


# Property Collection
class ModifierList(bpy.types.PropertyGroup):
    apply_modifier: bpy.props.BoolProperty(name="", default=False)


# Operator for applying modifiers
class OBJECT_OT_apply_modifiers_with_shape_keys(bpy.types.Operator):
    ''' Apply selected modifiers to mesh even if it has shape keys '''
    bl_idname = "object.apply_modifiers_with_shape_keys"
    bl_label = "Apply Modifier(s) for Mesh with Shape Keys"
    bl_options = {'REGISTER', 'UNDO'}

    collection_property: bpy.props.CollectionProperty(type=ModifierList)

    method: bpy.props.EnumProperty(
        name="Method",
        description="How the shape keys are carried through the modifier(s)",
        items=[
            ('AUTO', "Auto",
             "Use Interpolate Offsets for modifiers whose result depends on the vertex positions "
             "(Decimate, Weld, Remesh, Boolean, Volume to Mesh) and Re-apply Per Shape for everything else"),
            ('REAPPLY', "Re-apply Per Shape",
             "Evaluate the modifier(s) once per shape key with that shape pinned and join the results back "
             "in by vertex index. Correct when the resulting topology is the same for every shape "
             "(Mirror, Array, Subdivision)"),
            ('INTERPOLATE', "Interpolate Offsets",
             "Carry each shape key offset through the modifier(s) as a mesh attribute and evaluate only once, "
             "so the offsets are interpolated exactly like UVs and vertex weights. Required when the "
             "resulting topology depends on the vertex positions (Decimate, Weld, Remesh, Boolean)"),
        ],
        default='AUTO',
    )

    @classmethod
    def poll(cls, context):
        active_object = context.active_object
        # Check if the active object is a mesh, has shape keys, and is in Object mode
        return active_object and active_object.data.shape_keys and context.object.mode == 'OBJECT'

    def execute(self, context):
        selected_modifiers = [
            o.name for o in self.collection_property if o.apply_modifier]
        if not selected_modifiers:
            self.report({'ERROR'}, 'No modifiers selected!')
            return {'FINISHED'}

        method = resolve_method(context.object, selected_modifiers, self.method)

        success, error_info = apply_modifiers_with_shape_keys(
            context, selected_modifiers, method=method)
        if not success:
            self.report({'ERROR'}, error_info)
        else:
            self.report({'INFO'},
                        f"Applied {len(selected_modifiers)} modifier(s) using the "
                        f"'{METHOD_LABELS.get(method, method)}' method")

        return {'FINISHED'}

    def draw(self, context):
        self.layout.label(text="Select which modifier(s) to apply")
        box = self.layout.box()

        for prop in self.collection_property:
            box.prop(prop, "apply_modifier", text=prop.name)

        self.layout.prop(self, "method")

    def invoke(self, context, event):
        self.collection_property.clear()
        for modifier in context.object.modifiers:
            item = self.collection_property.add()
            item.name = modifier.name
            item.apply_modifier = False
        return context.window_manager.invoke_props_dialog(self)

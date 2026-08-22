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
import re

import numpy as np


# Modifiers whose output topology depends on the input vertex positions.
# For these the per shape re-evaluation used by the REAPPLY method produces a
# different vertex count (or a different vertex order) for every shape, so the
# shapes cannot be joined back by index.
GEOMETRY_DEPENDENT_MODIFIER_TYPES = {
    'DECIMATE',
    'WELD',
    'REMESH',
    'BOOLEAN',
    'VOLUME_TO_MESH',
}

# Prefix used for the throw away FLOAT_VECTOR attributes that carry the shape
# key offsets through the modifier stack in the INTERPOLATE method.
TEMP_ATTRIBUTE_PREFIX = "_amsk_tmp_"

# Shape key properties handled explicitly rather than through the generic
# save/restore loop.
_PROPERTIES_HANDLED_SEPARATELY = {'name', 'relative_key'}


def resolve_method(obj, selected_modifiers, method='AUTO'):
    ''' Turn the 'AUTO' method into a concrete one ('REAPPLY' or 'INTERPOLATE').
    Any explicit method is returned unchanged. '''
    if method != 'AUTO':
        return method

    for modifier_name in selected_modifiers:
        modifier = obj.modifiers.get(modifier_name)
        if modifier and modifier.type in GEOMETRY_DEPENDENT_MODIFIER_TYPES:
            return 'INTERPOLATE'
    return 'REAPPLY'


# Helper functions
def disable_modifiers(context, selected_modifiers):
    ''' disables any modifiers that are not selected so the mesh can be calculated.
    Returns a list of modifiers it changed so they can be reset later '''
    saved_enabled_modifiers = []

    for modifier in context.object.modifiers:
        if modifier.name not in selected_modifiers and modifier.show_viewport:
            saved_enabled_modifiers.append(modifier)
            modifier.show_viewport = False
    return saved_enabled_modifiers


def duplicate_object(obj):
    '''Copy the object, make it active and return it '''
    new_obj = obj.copy()
    new_obj.data = obj.data.copy()
    bpy.context.collection.objects.link(new_obj)
    bpy.context.view_layer.objects.active = new_obj
    return new_obj


def evaluate_mesh(context, obj):
    """Low-level alternative to `bpy.ops.object.convert` for converting to meshes"""
    depsgraph = context.evaluated_depsgraph_get()
    eval_obj = obj.evaluated_get(depsgraph)
    mesh = bpy.data.meshes.new_from_object(eval_obj, preserve_all_data_layers=True, depsgraph=depsgraph)
    return mesh


def apply_modifier_to_object(context, obj, selected_modifiers):
    ''' Disables all modifiers except the selected ones
    Creates a new mesh from that output and swaps it out
    Removes the selected modifiers
    Deletes the old mesh and restores the other modifiers to what they were
    '''
    context.view_layer.objects.active = obj

    # Disable all modifiers (except selected)
    saved_enabled_modifiers = disable_modifiers(context, selected_modifiers)

    # Make sure all selected modifiers are enabled
    for modifier_name in selected_modifiers:
        modifier = obj.modifiers.get(modifier_name)
        modifier.show_viewport = True

    # evaluate new mesh and swap it out
    new_mesh = evaluate_mesh(context, obj)
    old_mesh = obj.data
    old_mesh_name = old_mesh.name
    obj.data = new_mesh

    # Delete the selected modifiers from the object
    for modifier in selected_modifiers:
        obj.modifiers.remove(obj.modifiers[modifier])

    # Delete the old mesh and rename the data block
    bpy.data.meshes.remove(old_mesh)
    obj.data.name = old_mesh_name

    # restore the previously enabled modifiers
    if len(saved_enabled_modifiers) > 0:
        for modifier in saved_enabled_modifiers:
            modifier.show_viewport = True


def _shape_key_property_entry(key_block):
    ''' The saved state of a single shape key.
    The relative key is stored by NAME - a KeyBlock pointer would refer to the old
    (about to be deleted) Key datablock and would be silently ignored on restore. '''
    return {
        "properties": {p.identifier: getattr(key_block, p.identifier)
                       for p in key_block.bl_rna.properties
                       if not p.is_readonly and p.identifier not in _PROPERTIES_HANDLED_SEPARATELY},
        "relative_key": key_block.relative_key.name if key_block.relative_key else None,
    }


def save_shape_key_properties(obj, include_reference=False):
    ''' This function will save the settings on the shape keys (min/max etc) and return them
    as a dictionary keyed by the shape key NAME.
    Index 0 (the reference / Basis key) is skipped unless include_reference is set, because
    the result doubles as the list of shapes that have to be rebuilt. '''
    properties_dict = {}
    for idx, key_block in enumerate(obj.data.shape_keys.key_blocks):
        if idx == 0 and not include_reference:
            continue
        properties_dict[key_block.name] = _shape_key_property_entry(key_block)
    return properties_dict


def restore_shape_key_properties(obj, property_dict):
    ''' Restores the settings for each shape key (min/max etc), matching them up by name '''
    key_blocks = obj.data.shape_keys.key_blocks

    for name, saved in property_dict.items():
        key_block = key_blocks.get(name)
        if key_block is None:
            continue

        properties = dict(saved["properties"])

        # The slider limits clamp each other and clamp 'value', so they are set first
        # (twice, because whichever one is set first can be clamped by the value the
        # other one still holds) and 'value' is set last.
        ordered = []
        for prop in ('slider_min', 'slider_max', 'slider_min', 'slider_max'):
            if prop in properties:
                ordered.append((prop, properties[prop]))
        value = properties.pop('value', None)
        ordered.extend((p, v) for p, v in properties.items() if p not in ('slider_min', 'slider_max'))
        if value is not None:
            ordered.append(('value', value))

        for prop, val in ordered:
            try:
                setattr(key_block, prop, val)
            except Exception as e:  # read-only in this context, or an invalid value
                print(f"Apply Modifiers With Shape Keys: could not restore '{prop}' on shape key '{name}': {e}")

        relative_name = saved.get("relative_key")
        if relative_name:
            relative_block = key_blocks.get(relative_name)
            if relative_block is not None:
                try:
                    key_block.relative_key = relative_block
                except Exception as e:
                    print(f"Apply Modifiers With Shape Keys: could not restore relative key of '{name}': {e}")


def join_as_shape(temp_obj, original_obj, name=None):
    '''Join the temp object back to the original as a shape key
    Vertex positions are transferred by index.
    Different number of vertices or index will give unpredictable results
    '''

    # create a basis shape on the temp object to make the next step easier
    temp_obj_shape = temp_obj.shape_key_add(from_mix=False)
    if name is None:
        new_org_shape = original_obj.shape_key_add(from_mix=False)
    else:
        new_org_shape = original_obj.shape_key_add(name=name, from_mix=False)

    # Transfer Vertex Positions
    for source_vert, target_vert in zip(temp_obj_shape.data, new_org_shape.data):
        target_vert.co = source_vert.co

    return new_org_shape


def save_shape_key_drivers(obj, property_dict=None):
    ''' Copy drivers for shape key properties.
    Returns a dictionary with the drivers and the properties on the shape keys they drive.
    'property_dict' is accepted (and ignored) for backwards compatibility. '''

    drivers = {}

    # Ensure the object has shape keys animation data
    if not obj.data.shape_keys.animation_data:
        # print(f"No animation data found for {obj.name}.")  # DEBUG
        return drivers

    # Loop through all the drivers in the shape keys animation data
    for driver in obj.data.shape_keys.animation_data.drivers:
        # Extract the property name from the data_path
        data_path_parts = driver.data_path.split('.')
        if len(data_path_parts) <= 1:
            continue

        # The last part of the data path is the property name ('value', 'slider_min', ...)
        property_name = data_path_parts[-1]

        # Find the shape key name using a regular expression
        match = re.search(r'key_blocks\["(.*)"\]', driver.data_path)
        if not match:  # a driver on something else (eval_time for example)
            continue

        shape_key_name = match.group(1)

        # Create a dictionary for the driver data
        driver_data = {
            "driver": driver,
            "property": property_name
        }

        # Append the driver data to the shape_key_drivers list
        if shape_key_name not in drivers:
            drivers[shape_key_name] = []

        drivers[shape_key_name].append(driver_data)

    return drivers


def restore_shape_key_drivers(obj, copy_obj, drivers, context):
    ''' Restore drivers for shape key properties '''

    if not drivers:
        return

    if not obj.data.shape_keys.animation_data:
        obj.data.shape_keys.animation_data_create()

    for shape_key_name, shape_key_drivers in drivers.items():
        # Find the shape key block by name
        shape_key_block = obj.data.shape_keys.key_blocks.get(shape_key_name)
        if not shape_key_block:
            continue

        for driver_data in shape_key_drivers:
            # Extract the f-curve and property
            source_fcurve = driver_data["driver"]
            property_name = driver_data["property"]

            # Add the driver to the shape key property
            try:
                new_driver = shape_key_block.driver_add(property_name)

                # set the type
                new_driver.driver.type = source_fcurve.driver.type

                # Copy the driver expression if it exists
                if source_fcurve.driver.expression:
                    new_driver.driver.expression = source_fcurve.driver.expression

                # Copy the driver variables
                for var in source_fcurve.driver.variables:
                    new_var = new_driver.driver.variables.new()
                    new_var.name = var.name
                    new_var.type = var.type

                    # Copy each target for the variable
                    for idx, target in enumerate(var.targets):
                        new_var.targets[idx].id_type = target.id_type
                        if target.id == copy_obj:  # a target pointing at the copy should point at the original
                            target.id = obj
                        new_var.targets[idx].id = target.id
                        new_var.targets[idx].data_path = target.data_path
                        new_var.targets[idx].bone_target = target.bone_target
                        new_var.targets[idx].transform_type = target.transform_type
                        new_var.targets[idx].transform_space = target.transform_space

                # print(f"Restored driver for {property_name} on shape key {shape_key_name}.")  #DEBUG

            except Exception as e:
                print(f"Failed to restore driver for {property_name} on shape key {shape_key_name}: {str(e)}")


def copy_shape_key_animation(source_obj, target_obj):
    ''' Relink all shape key animations (keyframes) for all properties from one object to another '''

    # Ensure the source object has an action for shape keys
    if not source_obj.data.shape_keys.animation_data:
        # print(f"{source_obj.name} has no animation data for shape keys.")# DEBUG
        return

    if not source_obj.data.shape_keys.animation_data.action:
        # print(f"{source_obj.name} has no action for shape keys.") # DEBUG
        return

    # Link the existing action to the target object
    if not target_obj.data.shape_keys.animation_data:
        target_obj.data.shape_keys.animation_data_create()  # Create animation data for the target shape key if needed
    target_obj.data.shape_keys.animation_data.action = source_obj.data.shape_keys.animation_data.action

    # Link the existing action slot to the action (Blender ver > 4.4)
    if bpy.app.version >= (4, 4, 0):
        target_obj.data.shape_keys.animation_data.action_slot = source_obj.data.shape_keys.animation_data.action_slot

    # print(f"Shape key animations copied from {source_obj.name} to {target_obj.name}.") # DEBUG


# ---------------------------------------------------------------------------
# INTERPOLATE method
# ---------------------------------------------------------------------------
def _unique_attribute_name(mesh, base_name):
    ''' Return a name that is not already used by an attribute on this mesh '''
    name = base_name
    counter = 0
    while name in mesh.attributes:
        counter += 1
        name = f"{base_name}_{counter}"
    return name


def _get_vectors(rna_collection, count, attribute="co"):
    ''' foreach_get a flat float32 array of count * 3 values '''
    values = np.empty(count * 3, dtype=np.float32)
    rna_collection.foreach_get(attribute, values)
    return values


def _apply_interpolated(context, obj, selected_modifiers):
    ''' Apply the selected modifiers ONCE, carrying every shape key offset through the
    modifier stack as a FLOAT_VECTOR point attribute so it gets interpolated in exactly the
    same way UVs and vertex weights are.  This is the only correct approach when the
    modifier output depends on the vertex positions (Decimate, Weld, Remesh, Boolean...).
    Returns an error message or None. '''

    mesh = obj.data
    key = mesh.shape_keys
    key_blocks = key.key_blocks
    reference_key = key.reference_key
    reference_name = reference_key.name
    key_datablock_name = key.name
    use_relative = key.use_relative

    # Blender guarantees the reference key is key_blocks[0]; the code below only relies on
    # the order of the names, so it stays correct either way.
    reference_index = key_blocks.find(reference_name)
    original_order = [key_block.name for key_block in key_blocks]

    vertex_count = len(mesh.vertices)
    reference_co = _get_vectors(reference_key.data, vertex_count)

    # Bake every shape key offset into a temporary point attribute
    temp_attributes = {}  # shape key name -> attribute name
    for index, key_block in enumerate(key_blocks):
        if key_block.name == reference_name:
            continue
        delta = _get_vectors(key_block.data, vertex_count) - reference_co
        attribute = mesh.attributes.new(_unique_attribute_name(mesh, f"{TEMP_ATTRIBUTE_PREFIX}{index}"),
                                        'FLOAT_VECTOR', 'POINT')
        attribute.data.foreach_set("vector", delta)
        temp_attributes[key_block.name] = attribute.name

    # Pin the reference key so the modifiers see the plain basis geometry
    obj.show_only_shape_key = True
    obj.active_shape_key_index = reference_index
    context.view_layer.update()

    # Evaluate once and swap the mesh out (this also destroys the old shape keys)
    apply_modifier_to_object(context, obj, selected_modifiers)

    new_mesh = obj.data
    new_vertex_count = len(new_mesh.vertices)
    new_basis_co = _get_vectors(new_mesh.vertices, new_vertex_count)

    missing = []

    # Rebuild the shape keys in the original order
    for name in original_order:
        if name == reference_name:
            obj.shape_key_add(name=reference_name, from_mix=False)
            continue

        attribute_name = temp_attributes.get(name)
        new_key_block = obj.shape_key_add(name=name, from_mix=False)

        attribute = new_mesh.attributes.get(attribute_name) if attribute_name else None
        if attribute is None or len(attribute.data) != new_vertex_count:
            missing.append(name)
            continue

        delta = _get_vectors(attribute.data, new_vertex_count, attribute="vector")
        new_key_block.data.foreach_set("co", new_basis_co + delta)

    # Remove the temporary attributes again
    for attribute_name in temp_attributes.values():
        attribute = new_mesh.attributes.get(attribute_name)
        if attribute is not None:
            new_mesh.attributes.remove(attribute)

    new_key = obj.data.shape_keys
    new_key.use_relative = use_relative
    try:
        new_key.name = key_datablock_name
    except Exception:
        pass

    if missing:
        return ("The selected modifier(s) did not carry the shape key offsets through: "
                + ", ".join(missing)
                + ". Those shapes were left as copies of the basis.")
    return None


# ---------------------------------------------------------------------------
# REAPPLY method (the original approach)
# ---------------------------------------------------------------------------
def _apply_reapply(context, original_obj, copy_obj, selected_modifiers, shape_key_properties):
    ''' Re-evaluate the modifier stack once per shape key with that shape pinned and join the
    result back in by vertex index.  Correct as long as the modifier output topology does not
    depend on the vertex positions (Mirror, Array, Subdivision...).
    Returns an error message or None. '''

    # Cache the name of the first shape key before any risky operation later (needed if code runs from Quick Favorites)
    key_name = copy_obj.data.shape_keys.key_blocks[0].name

    # Remove all shape keys and apply modifiers on the original
    context.view_layer.objects.active = original_obj  # Force original object to be active (needed if code runs from Quick Favorites)
    bpy.ops.object.shape_key_remove(all=True)
    apply_modifier_to_object(context, original_obj, selected_modifiers)

    # Add a basis shape key back to the original object
    original_obj.shape_key_add(name=key_name, from_mix=False)

    failed_shapes = []

    # Loop over the original shape keys, create a temp mesh, apply single shape, apply modifiers
    # and merge back to the original (1 shape at a time)
    for key_block_name in list(shape_key_properties.keys()):
        # Create a temp object
        context.view_layer.objects.active = copy_obj
        temp_obj = duplicate_object(copy_obj)

        # Force Blender to evaluate new datablocks (needed if code runs from Quick Favorites)
        bpy.context.view_layer.update()

        # Pin the shape we want
        temp_key_blocks = temp_obj.data.shape_keys.key_blocks
        pinned_index = temp_key_blocks.find(key_block_name)
        temp_obj.show_only_shape_key = True
        temp_obj.active_shape_key_index = pinned_index

        # A pinned shape key is not evaluated at full strength if it is muted (Blender falls
        # back to the reference key, so the shape would come back empty) or if it has a vertex
        # group (the offsets get scaled by the weights).  The temp object is thrown away, so
        # both can simply be cleared here; the real settings are restored at the end.
        pinned_key_block = temp_key_blocks[pinned_index]
        pinned_key_block.mute = False
        pinned_key_block.vertex_group = ""

        # Disable all modifiers (including selected)
        for modifier in temp_obj.modifiers:
            modifier.show_viewport = False

        # Now freeze the mesh by applying the selected modifiers
        apply_modifier_to_object(context, temp_obj, selected_modifiers)

        # Verify the meshes have the same amount of verts
        if len(original_obj.data.vertices) != len(temp_obj.data.vertices):
            failed_shapes.append(key_block_name)
            # Still create the shape key (as a copy of the basis) so the names, the order,
            # and anything that refers to the shapes by index stay correct.
            context.view_layer.objects.active = original_obj
            original_obj.shape_key_add(name=key_block_name, from_mix=False)
            # Clean up the temp object and try to move on
            bpy.data.meshes.remove(temp_obj.data)
            continue

        # Transfer the temp object as a shape back to original
        join_as_shape(temp_obj, original_obj, name=key_block_name)

        # Clean up the temp object
        bpy.data.meshes.remove(temp_obj.data)

    if failed_shapes:
        return ("These shape keys could not be transferred because the mesh no longer has the same "
                "amount of vertices after applying the selected modifier(s): "
                + ", ".join(failed_shapes)
                + ". They were kept as copies of the basis so the other shapes keep their names. "
                "Use the Interpolate method for geometry-dependent modifiers such as Decimate/Weld.")
    return None


# Primary function (this gets imported and used by the operator)
def apply_modifiers_with_shape_keys(context, selected_modifiers, method='AUTO'):
    ''' Apply the selected modifiers to the mesh even if it has shape keys.
    'method' is one of 'AUTO', 'REAPPLY' or 'INTERPOLATE'. '''
    original_obj = context.view_layer.objects.active
    shapes_count = len(original_obj.data.shape_keys.key_blocks) if original_obj.data.shape_keys else 0

    method = resolve_method(original_obj, selected_modifiers, method)

    if shapes_count <= 1:  # if there is only a Basis shape, delete the shape and apply the modifiers
        if shapes_count == 1:
            original_obj.shape_key_remove(original_obj.data.shape_keys.key_blocks[0])
        apply_modifier_to_object(context, original_obj, selected_modifiers)
        return True, None

    # Save the pin option setting and active shape key index
    pin_setting = original_obj.show_only_shape_key
    saved_active_shape_key_index = original_obj.active_shape_key_index

    if method == 'REAPPLY' and saved_active_shape_key_index == 0:  # hack to prevent some modifiers from failing if 'Basis' is active
        original_obj.active_shape_key_index = 1

    # Duplicate the object.  It keeps the shape keys, their drivers and their action alive
    # while the original loses them.
    copy_obj = duplicate_object(original_obj)
    context.view_layer.objects.active = original_obj

    # Save the shape key properties.  The first dict drives the rebuild (so it must not
    # contain the reference key), the second one is only used to put the settings back.
    shape_key_properties = save_shape_key_properties(original_obj)
    all_shape_key_properties = save_shape_key_properties(original_obj, include_reference=True)

    # Copy drivers for shape keys (from the copy because the original ones will be gone in a moment)
    shape_key_drivers = save_shape_key_drivers(copy_obj)

    if method == 'INTERPOLATE':
        error_message = _apply_interpolated(context, original_obj, selected_modifiers)
    else:
        error_message = _apply_reapply(context, original_obj, copy_obj, selected_modifiers, shape_key_properties)

    # Restore shape key properties
    restore_shape_key_properties(original_obj, all_shape_key_properties)

    # Restore any shape key animation
    copy_shape_key_animation(copy_obj, original_obj)

    # Restore any shape key drivers
    restore_shape_key_drivers(original_obj, copy_obj, shape_key_drivers, context)

    # Clean up the duplicate object (removing its mesh removes the object with it)
    bpy.data.meshes.remove(copy_obj.data)

    # Restore the pin option setting and active shape key index
    original_obj.show_only_shape_key = pin_setting
    original_obj.active_shape_key_index = min(saved_active_shape_key_index,
                                              len(original_obj.data.shape_keys.key_blocks) - 1)

    # Make sure the original object is active before finishing
    context.view_layer.objects.active = original_obj

    # Report the error message if any
    if error_message:
        return False, error_message

    return True, None

# Apply Modifiers to Mesh with Shape Keys

> **This is a fork** of [CGCookie/apply_modifiers_with_shape_keys](https://github.com/CGCookie/apply_modifiers_with_shape_keys).
> It adds a second **Interpolate Offsets** method so that modifiers whose result depends on the
> vertex positions (Decimate, Weld, Remesh, Boolean, Volume to Mesh) can be applied at all, and
> fixes several ways the original could silently mislabel or empty out shape keys. See
> [Methods](#methods) below.

This is a Blender add-on that applies any modifiers to a mesh that has shape keys. It will apply any of the modifiers you select, restore all the settings, drivers, and any animation data on the shapes.

If you run into the problem below, this add-on will let you apply that modifier.  
![Modifier cannot be applied to a mesh with shape keys](images/cant-apply.png)

## Installation

### Method 1: From Blender Extensions Platform

1. Open **Blender** and go to **Edit > Preferences > Get-Extensions**.
2. In the **Get-Extensions** tab, click **Install...** at the top right of the window.
3. Search for the add-on by name in search bar, and click **Install** to enable it.

### Method 2: Manual Installation

1. Download the add-on `.zip` file.
2. In Blender, go to **Edit > Preferences > Add-ons**.
3. Click the **Install...** button at the top of the preferences window.
4. Select the `.zip` file you downloaded and click **Install Add-on**.
5. Enable the add-on by checking the box next to **Apply Modifiers to Mesh with Shape Keys**.

Once the add-on is installed, make sure to enable it with the checkbox in the Add-ons tab.
![enable add-on](images/enable-add-on.png)

## Usage

The add-on will only be available for **Mesh objects** in **Object Mode**.

It can be found in the **Shape Key Context Menu** or by using the search function.

### Shape Key Context Menu

![Shape Key Context Menu](images/context-menu.png)



### F3 Search
You can also search for it in the 3D Viewport by pressing **F3** and typing **"Apply Modifiers with Shape Keys"**.

![search box](images/f3-search.png)  

After activating the tool you will see a popup dialog box.  
![popup dialog box](images/popup-dialog-box.png)


### Checkboxes

Chose which modifiers to apply and click OK


## Methods

The dialog has a **Method** dropdown underneath the modifier checkboxes.

### Auto (default)

Picks **Interpolate Offsets** if *any* of the modifiers you ticked is one whose output topology
depends on where the vertices are — `DECIMATE`, `WELD`, `REMESH`, `BOOLEAN`, `VOLUME_TO_MESH` —
and **Re-apply Per Shape** otherwise.

### Re-apply Per Shape

The original behaviour. The modifiers are evaluated once per shape key with that shape pinned, and
each result is joined back in by vertex index.

This is the right choice whenever the modifier changes the topology *the same way for every shape*:
Mirror, Array, Subdivision Surface, Solidify, and so on. It is exact — every shape goes through the
modifier itself.

It cannot work when the resulting vertex count or vertex order differs between shapes. When that
happens the add-on now reports every shape that failed in one message and keeps those shapes (as
copies of the basis) so the names, the order, and anything referring to them by index stay correct.

### Interpolate Offsets

Each shape key's offset from the basis is written to the mesh as a temporary `FLOAT_VECTOR` point
attribute, the modifiers are evaluated **once** on the basis geometry, and the offsets come out the
other side interpolated in exactly the same way UVs and vertex weights are. The shape keys are then
rebuilt from the new basis plus those interpolated offsets, and the temporary attributes are removed.

This is the only approach that works for Decimate, Weld, Remesh and Boolean, and it is also much
faster on meshes with many shape keys, because the stack is evaluated once instead of once per shape.

**Known limitation:** the offsets travel as raw vectors, so they are moved and welded with the mesh
but they are never *transformed*. A modifier in the same selection that mirrors, rotates or
instances geometry (Mirror, Array with an object offset, Screw) would place correct geometry but
leave the offsets on the copies unmirrored/unrotated. Apply those separately — and first — with the
**Re-apply Per Shape** method, then apply the geometry-dependent one with **Interpolate Offsets**.


## How it Functions

### Re-apply Per Shape

1. **Duplicate Mesh**:  
    - The add-on creates a duplicate of the original mesh, removes all the shape keys, and applies the selected modifier(s) by evaluating the mesh and swapping it out (it avoids using the apply modifiers operator).

2. **Loop through Shape Keys**:  
    - For each shape key, it creates another duplicate of the mesh.
    - It evaluates the mesh with just that single shape key active with the selected modifier(s) enabled.
    - It then merges this new mesh back into the original as a shape key.
    (it also avoids using the apply modifier operator)

3. **Restore Shape Keys**:  
    - After processing all the shape keys, the add-on restores the original shape key values, animation data, and drivers (if applicable) on the original mesh.

This process is done one shape key at a time to reduce the memory load on your machine, which is important if you're working with a high-density mesh and a lot of shape keys.

### Interpolate Offsets

1. **Bake the offsets**:
    - Every shape key's offset from the reference (Basis) key is stored on the mesh as a temporary `FLOAT_VECTOR` point attribute.

2. **Evaluate once**:
    - The reference key is pinned, the unselected modifiers are switched off, and the mesh is evaluated a single time. The attributes are carried through and interpolated by the modifiers along with the UVs and the vertex weights.

3. **Rebuild the shape keys**:
    - The shape keys are recreated on the new mesh, in the original order and with the original names, each one set to the new basis position plus its interpolated offset. The temporary attributes are then deleted and the settings, drivers and animation data are restored.

## Troubleshooting

Theoretically, if you can apply the modifiers to the base shape, and all your shape keys and the resulting meshes have the same number of vertices, the add-on will work with any modifier.

However, as you may know, several Blender modifiers can change the number of vertices on the base mesh (e.g. Subdivision Surface, Mirror modifier with Bisect or Merge enabled, Geometry Nodes, etc.). This can cause problems because Blender can only join meshes with shape keys if they have the same number of vertices.

If the vertex count comes out *different for different shapes*, no amount of reordering will help — switch the **Method** to **Interpolate Offsets** instead (Auto already does this for Decimate, Weld, Remesh, Boolean and Volume to Mesh).

### What to Do If the Add-on Doesn't Work as Expected

If your attempt to apply the modifiers is incomplete or results in an error, **undo the operation and consider the following tips**.

#### Try a Different Order of Modifiers:
Certain modifiers can cause problems when applied in a specific order. For example:
- **Subdivision** followed by **Mirror (with Bisect)** could fail, especially if any shape keys move vertices away from the mid-line.
- Try applying the modifiers in the opposite order to see if that resolves the issue.

#### Apply Modifiers One at a Time:
Instead of applying multiple modifiers at once, try applying them individually. This can help pinpoint which modifier is causing the issue. For example, if a combination of **Subdivision** and **Mirror** fails, applying them one at a time might help identify which one is the problem.

#### Remove a Troublesome Shape Key:
Is there a specific shape key that causes the operation to fail?  
If you can identify it, try removing that particular shape key. Once the problematic shape is removed, the others may succeed. You can always rebuild the troublesome shape afterward.

#### Geometry Nodes:
The add-on is designed to work with **mesh objects**. If you're using a **Geometry Nodes** setup, make sure that the output is a mesh object (not instances, for example). You may need to "realize" the instances so the output is truly a mesh object.  
![realize instances](images/realize-instances.png)



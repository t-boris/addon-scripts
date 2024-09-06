import bpy
import bmesh
from bpy.props import IntProperty, StringProperty, BoolProperty, FloatProperty
from bpy_extras.io_utils import ExportHelper
import os
import traceback
from mathutils import Vector
from mathutils.geometry import intersect_line_plane
import ezdxf

class SliceObjectOperator(bpy.types.Operator, ExportHelper):
    """Slice Object and Export DXF"""
    bl_idname = "object.slice_object_operator"
    bl_label = "Slice Object and Export DXF"
    bl_options = {'REGISTER', 'UNDO'}

    num_layers: IntProperty(
        name="Number of Layers",
        description="Number of layers to slice the object into",
        default=10,
        min=1
    )
    slice_direction: BoolProperty(
        name="Slice Bottom to Top",
        description="Slice from bottom to top if checked, top to bottom if unchecked",
        default=True
    )
    add_outline: BoolProperty(
        name="Add Outline",
        description="Add a rectangular outline around each slice",
        default=False
    )
    outline_offset: FloatProperty(
        name="Outline Offset",
        description="Offset distance for the outline (in mm)",
        default=0.5,
        min=0.1,
        max=10.0
    )
    filename_ext = ".dxf"
    filter_glob: StringProperty(default="*.dxf", options={'HIDDEN'})

    def execute(self, context):
        selected_objects = context.selected_objects
        if not selected_objects:
            self.report({'ERROR'}, "No object selected.")
            return {'CANCELLED'}
        
        obj = selected_objects[0]
        export_path = os.path.dirname(self.filepath)
        
        self.report({'INFO'}, f"Selected object: {obj.name}")
        self.report({'INFO'}, f"Exporting DXF files to: {export_path}")
        self.report({'INFO'}, f"Number of layers: {self.num_layers}")
        self.report({'INFO'}, f"Slice direction: {'Bottom to Top' if self.slice_direction else 'Top to Bottom'}")
        self.report({'INFO'}, f"Add outline: {'Yes' if self.add_outline else 'No'}")
        if self.add_outline:
            self.report({'INFO'}, f"Outline offset: {self.outline_offset} mm")
        
        self.slice_object(context, obj, self.num_layers, export_path)
        return {'FINISHED'}
    
    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def slice_object(self, context, obj, num_layers, export_path):
        if not os.access(export_path, os.W_OK):
            self.report({'ERROR'}, f"No write permission for directory: {export_path}")
            return

        # Create BMesh
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bmesh.ops.transform(bm, matrix=obj.matrix_world, verts=bm.verts)

        # Calculate bounding box
        bbox_corners = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
        z_min = min(corner.z for corner in bbox_corners)
        z_max = max(corner.z for corner in bbox_corners)
        total_height = z_max - z_min
        layer_height = total_height / num_layers
        
        self.report({'INFO'}, f"Total height: {total_height:.4f}, Layer height: {layer_height:.4f}")
        
        bpy.context.window_manager.progress_begin(0, num_layers)

        for i in range(num_layers):
            if self.slice_direction:
                z_slice = z_min + i * layer_height
            else:
                z_slice = z_max - i * layer_height
            
            contours = self.slice_at_z(bm, z_slice)
            if contours:
                if self.add_outline:
                    outline = self.create_rectangular_outline(contours, self.outline_offset)
                    contours.append(outline)
                self.export_contours_to_dxf(contours, f"{export_path}/slice_{i + 1}.dxf")
            bpy.context.window_manager.progress_update(i)

        bpy.context.window_manager.progress_end()
        
        bm.free()
        
        exported_files = [f for f in os.listdir(export_path) if f.endswith('.dxf')]
        self.report({'INFO'}, f"Total DXF files exported: {len(exported_files)}")
        self.report({'INFO'}, f"Finished slicing and exporting {num_layers} layers to {export_path}.")

    def slice_at_z(self, bm, z):
        # Find all edges that intersect with the z-plane
        intersecting_edges = [e for e in bm.edges if (e.verts[0].co.z - z) * (e.verts[1].co.z - z) <= 0]
        
        # Calculate intersection points
        intersections = {}
        for edge in intersecting_edges:
            v1, v2 = edge.verts[0].co, edge.verts[1].co
            intersection = intersect_line_plane(v1, v2, Vector((0, 0, z)), Vector((0, 0, 1)))
            if intersection:
                key = (edge.verts[0].index, edge.verts[1].index)
                intersections[key] = intersection

        # Construct contours
        contours = []
        used_edges = set()
        for edge in intersecting_edges:
            if edge in used_edges:
                continue
            
            contour = []
            start_edge = edge
            current_edge = edge
            
            while True:
                used_edges.add(current_edge)
                v1, v2 = current_edge.verts[0], current_edge.verts[1]
                key = (v1.index, v2.index)
                rev_key = (v2.index, v1.index)
                point = intersections.get(key) or intersections.get(rev_key)
                
                if point:
                    contour.append(point)
                
                # Find the next edge
                next_edge = None
                for linked_face in current_edge.link_faces:
                    for face_edge in linked_face.edges:
                        if face_edge != current_edge and face_edge in intersecting_edges and face_edge not in used_edges:
                            next_edge = face_edge
                            break
                    if next_edge:
                        break
                
                if not next_edge or next_edge == start_edge:
                    break
                
                current_edge = next_edge
            
            if len(contour) > 2:
                contours.append(contour)

        return contours

    def create_rectangular_outline(self, contours, offset):
        # Flatten all points from all contours
        all_points = [point for contour in contours for point in contour]
        
        if not all_points:
            return []

        # Find the bounding box
        min_x = min(p.x for p in all_points)
        max_x = max(p.x for p in all_points)
        min_y = min(p.y for p in all_points)
        max_y = max(p.y for p in all_points)
        z = all_points[0].z  # Use the z-coordinate of the first point

        # Create the outline rectangle with offset
        outline = [
            Vector((min_x - offset, min_y - offset, z)),
            Vector((max_x + offset, min_y - offset, z)),
            Vector((max_x + offset, max_y + offset, z)),
            Vector((min_x - offset, max_y + offset, z))
        ]
        
        return outline

    def export_contours_to_dxf(self, contours, filename):
        try:
            doc = ezdxf.new('R2010')
            msp = doc.modelspace()
            for contour in contours:
                points = [(p.x, p.y) for p in contour]
                if len(points) > 2:
                    msp.add_lwpolyline(points, close=True)
            doc.saveas(filename)
            self.report({'INFO'}, f"Exported DXF: {filename}")
        except Exception as e:
            self.report({'ERROR'}, f"Error exporting to DXF: {str(e)}")
            self.report({'ERROR'}, f"Traceback: {traceback.format_exc()}")

# Registration
def register():
    bpy.utils.register_class(SliceObjectOperator)

def unregister():
    bpy.utils.unregister_class(SliceObjectOperator)

if __name__ == "__main__":
    register()

    # To trigger the operator, press F3 in Blender and search for "Slice Object and Export DXF"
    bpy.ops.object.slice_object_operator('INVOKE_DEFAULT')
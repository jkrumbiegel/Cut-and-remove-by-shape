#!/usr/bin/env python
# coding=utf-8
#
# Copyright (C) 2023 Julius Krumbiegel
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.
#
"""
This extension takes two path-like objects. The top one should be closed when converted to a path.
The bottom one is usually open. The extension then applies the "Cut path" command so that the
lower path is cut into pieces wherever it intersects with the upper path. It then deletes all
the path elements that are either inside or outside the top path. The visual effect is similar
to using the top path as a clipping mask on the bottom path. However, the path elements are actually
shortened so that they can be used with plotters or engraving machines that only work with open paths.
"""

import os
import math
import re
import inkex
from inkex import bezier, CubicSuperPath, PathElement, Path


# converts the prev-handle, anchor, next-handle format of CubicSuperPath
# into p1, c1, c2, p2 bezier segment format 
def iterate_beziers(subsegment):
    subsegment = list(subsegment)

    p1, c1 = subsegment[0][1], subsegment[0][2]
    for i in range(1, len(subsegment)):
        c2, p2 = subsegment[i][0], subsegment[i][1]
        yield [p1, c1, c2, p2]
        p1, c1 = subsegment[i][1], subsegment[i][2]

def z_sorted_elements(node, alist):
    ordered = []
    id_list = list(alist)
    count = len(id_list)
    for element in node.iter():
        element_id = element.get('id')
        if element_id is not None and element_id in id_list:
            id_list.remove(element_id)
            ordered.append(element)
            count -= 1
            if not count:
                break
    return ordered

class CutAndRemoveByShapeExtension(inkex.EffectExtension):
    def add_arguments(self, pars):
        pars.add_argument("--keep_paths", type=str, default="inside")

    def effect(self):
        debug = False
        if debug:
            debugstr = ""

        selected = self.svg.selected

        if len(selected) != 2:
            inkex.errormsg("Please select two objects.")
            return

        selected_ids = []
        for node in selected:
            selected_ids.append(node.get('id'))

        sorted_selected = z_sorted_elements(self.document.getroot(), selected_ids)

        tempfile = self.options.input_file + "-temp.svg"

        top_id = sorted_selected[1].get("id")
        bottom_id = sorted_selected[0].get("id")
        bottom_style_copy = str(self.svg.getElementById(bottom_id).style)

        actions = ';'.join([
            f'select-by-id:{top_id}',
            'duplicate',
            f'unselect-by-id:{top_id}',
            'object-to-path',
            f'object-set-attribute:id,{top_id}_copy',
            'duplicate',
            f'unselect-by-id:{top_id}_copy',
            f'object-set-attribute:id,{top_id}_copy_2',
            'select-clear',
            f'select-by-id:{bottom_id}',
            f'select-by-id:{top_id}_copy_2',
            'path-cut',
            'selection-group',
            f'object-set-attribute:id,{bottom_id}',
            f"export-filename:{tempfile};export-overwrite;export-do"
        ])

        inkex.command.inkscape(self.options.input_file, actions=actions)

        # replace current with temp document
        self.document = inkex.load_svg(tempfile)
        self.svg = self.document.getroot()
        try:
            os.remove(tempfile)
        except Exception:
            pass

        top_node = self.svg.getElementById(f'{top_id}_copy')
        group_node = self.svg.getElementById(bottom_id)

        top_node.apply_transform()
        top_node_superpath = top_node.get_path().to_superpath()

        # we build the compound path by simple string concatenation
        new_path = ''
        
        for elem in group_node:
            elem.apply_transform()
            superpath = elem.get_path().to_superpath()

            super_iter = iter(superpath)
            first_seg = next(super_iter)

            seg_iter = iter(first_seg)

            bezier_args = next(iterate_beziers(seg_iter))

            midpoint = bezier.bezierpointatt(bezier_args, 0.5)

            if debug:
                debugstr += '\n\nmidpoint: ' + str(midpoint) + '\n'

            n_intersects = 0
            for supseg in top_node_superpath:
                if debug:
                    debugstr += 'segment\n\n'
                for bezier_points in iterate_beziers(supseg):
                    if debug:
                        debugstr += str(bezier_points) + '\n'

                    # we align the bezier to the x-axis going off from midpoint by translating by midpoint's y
                    aligned_bezier_points = [(p[0], p[1] - midpoint[1]) for p in bezier_points]

                    if debug:
                        debugstr += 'aligned bezier points\n'
                        debugstr += str(aligned_bezier_points) + '\n'

                    root_ts = get_roots(aligned_bezier_points)

                    if debug:
                        debugstr += 'root ts\n'
                        debugstr += str(root_ts) + '\n'

                    intersects = [bezier.bezierpointatt(bezier_points, t) for t in root_ts]
                    intersects = [i for i in intersects if i[0] >= midpoint[0]] # our ray just goes in positive x direction
                    n_intersects += len(intersects)

                    # intersects = bezier.linebezierintersect(linepoints, bezier_points) # buggy implementation? sometimes gets weird points
                    if debug:
                        debugstr += str(len(intersects)) + ' intermediate intersects\n'
                        for point in intersects:
                            x, y = point
                            debugstr += f'x:{x} y:{y}\n'

            if debug:
                debugstr += str(n_intersects) + ' intersects\n'

            if self.options.keep_paths == "inside":
                keep_mod = 1
            elif self.options.keep_paths == "outside":
                keep_mod = 0
            else:
                raise Exception(f"Invalid keep_paths value {keep_mod}")

            if n_intersects % 2 == keep_mod:
                # hack because a concatenated path cannot start with relative m move
                # or the path segments are translated by the end point of the previous segment
                pathstr = re.sub(
                    r'^\s*m',
                    'M',
                    str(elem.get_path())
                )

                new_path += pathstr

        top_node.delete()
        group_node.delete()

        current_layer = self.svg.get_current_layer()
        path_elem = PathElement()
        path_elem.style = inkex.styles.Style(bottom_style_copy)
        path_elem.set_path(Path(new_path))
        current_layer.insert(0, path_elem)

        if debug:
            inkex.errormsg(debugstr)
        return

# bezier root finding implementation translated from https://pomax.github.io/bezierinfo/#intersections

def approximately(a, b, precision = None):
    epsilon = 0.000001
    eps = epsilon if precision is None else precision
    return abs(a - b) <= eps

def crt(v):
    return -math.pow(-v, 1/3) if v < 0 else math.pow(v, 1/3)

def get_roots(aligned):
    pa, pb, pc, pd = aligned[0][1], aligned[1][1], aligned[2][1], aligned[3][1]

    def reduce(t):
        return 0 <= t <= 1
    def reduced(ts):
        return [t for t in ts if reduce(t)]

    d = -pa + 3 * pb - 3 * pc + pd
    a = 3 * pa - 6 * pb + 3 * pc
    b = -3 * pa + 3 * pb
    c = pa

    if approximately(d, 0):
        if approximately(a, 0):
            if approximately(b, 0):
                return []
            return reduced([-c / b])
        tosqrt = b * b - 4 * a * c
        if tosqrt < 0:
            return [] 
        q = math.sqrt(tosqrt)
        a2 = 2 * a
        return reduced([(q - b) / a2, (-b - q) / a2])

    a /= d
    b /= d
    c /= d

    p = (3 * b - a * a) / 3
    p3 = p / 3
    q = (2 * a * a * a - 9 * a * b + 27 * c) / 27
    q2 = q / 2
    discriminant = q2 * q2 + p3 * p3 * p3

    if discriminant < 0:
        mp3 = -p / 3
        mp33 = mp3 * mp3 * mp3
        r = math.sqrt(mp33)
        t = -q / (2 * r)
        cosphi = max(-1, min(t, 1))
        phi = math.acos(cosphi)
        crtr = crt(r)
        t1 = 2 * crtr
        x1 = t1 * math.cos(phi / 3) - a / 3
        x2 = t1 * math.cos((phi + 2 * math.pi) / 3) - a / 3
        x3 = t1 * math.cos((phi + 4 * math.pi) / 3) - a / 3
        return reduced([x1, x2, x3])
    elif discriminant == 0:
        u1 = crt(-q2) if q2 < 0 else -crt(q2)
        x1 = 2 * u1 - a / 3
        x2 = -u1 - a / 3
        return reduced([x1, x2])
    else:
        sd = math.sqrt(discriminant)
        u1 = crt(-q2 + sd)
        v1 = crt(q2 + sd)
        x1 = u1 - v1 - a / 3
        return reduced([x1])

if __name__ == '__main__':
    CutAndRemoveByShapeExtension().run()

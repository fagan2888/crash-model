
# coding: utf-8

# Segment (intersection and non-intersection) creation
# Draws on: http://bit.ly/2m7469y
# Developed by: bpben

import rtree
import json
import copy
from shapely.ops import unary_union
from collections import defaultdict
from . import util
import argparse
import os
import geojson
import re
from shapely.geometry import MultiLineString, LineString
from .segment import Segment


BASE_DIR = os.path.dirname(
    os.path.dirname(
        os.path.dirname(
            os.path.abspath(__file__))))

MAP_FP = os.path.join(BASE_DIR, 'data/processed/maps')
PROCESSED_DATA_FP = os.path.join(BASE_DIR, 'data/processed')
DATA_FP = None


def get_intersection_buffers(intersections, intersection_buffer_units,
                             debug=False):
    """
    Buffers intersection according to proj units
    Args:
        intersections
        intersection_buffer_units - in meters
        debug - if true, will output the buffers to file for debugging
    Returns:
        a list of polygons, buffering the intersections
        these are circles, or groups of overlapping circles
    """

    buffered_intersections = [intersection['geometry'].buffer(
        intersection_buffer_units) for intersection in intersections]

    buffered_intersections = unary_union(buffered_intersections)
    if debug:
        util.output_polygons([(x, {}) for x in buffered_intersections], os.path.join(
            MAP_FP, 'int_buffers.geojson'))

    results = []
    # Get the points that overlap with the buffers
    for buff in buffered_intersections:
        matches = []
        for inter in intersections:
            if inter['geometry'].within(buff):
                matches.append(inter['geometry'])
        results.append([buff, matches])
    return results


def bfs(graph, components, start):
    visited, queue = set(), [start]
    while queue:
        vertex, components = queue.pop(0)
        if vertex not in visited:
            visited.add(vertex)
            queue.extend(graph[vertex] - visited)
    return visited


def get_connections(points, segments):

    # Create a dict with each intersection point's coords as key
    # The values are the point itself and an empty list that will
    # store all the linestrings with a connection to the point
    point_dict = {}
    for p in points:
        point_dict[str(list(p.coords))] = (p, [])

    # Get a starting list of all lines that touch any of the
    # intersection points
    connected_ids = []
    for line in segments:
        for point, linelist in point_dict.values():
            if line.geometry.intersects(point):
                point_dict[str(list(point.coords))][1].append(line)
                connected_ids.append(line.properties['id'])
                
    prev_length = len(segments)
    # Take out all the lines that we already know are connected
    segments = [x for x in segments if x.properties['id'] not in connected_ids]

    while len(segments) != prev_length:
        for segment in segments:
            for point, segmentlist in point_dict.values():
                if segment.geometry.intersects(
                        unary_union([x.geometry for x in segmentlist])):
                    point_dict[str(list(point.coords))][1].append(segment)
                    connected_ids.append(segment.properties['id'])
        prev_length = len(segments)
        # Take out all the lines that we already know are connected
        segments = [x for x in segments if x.properties['id'] not in connected_ids]

    # Now figure out whether the individual points' segments are connected
    merged_inters = []
    inters = [x[1] for x in point_dict.values()]
    for key, value in enumerate(inters):

        inter = value
        for key2, value2 in enumerate(inters):
            if key != key2 and unary_union([x.geometry for x in value]).intersects(
                    unary_union([x.geometry for x in value2])):
                inter.extend(value2)
        merged_inters.append(inter)

    # remove any duplicate segments
    return [set(x) for x in merged_inters]


def find_non_ints(roads, int_buffers):
    """
    Find the segments that aren't intersections
    Args:
        roads - a list of tuples of shapely shape and dict of segment info
        int_buffers - a list of polygons that buffer intersections
    Returns:
        tuple consisting of:
            non_int_lines - list in same format as input roads, just a subset
                each element in the list is a tuple of LineString or
                MultiLineString and dict of properties
            inter_segments - dict of lists with keys data and lines
                each element in the lines list is one of the lines
                overlapping the intersection buffer, and each element
                each element in the data list is a dict of properties
                corresponding to the lines
    """

    # Create index for quick lookup
    print("creating rindex")

    int_buffers_index = rtree.index.Index()
    for idx, intersection_buffer in enumerate([x[0] for x in int_buffers]):
        int_buffers_index.insert(idx, intersection_buffer.bounds)

    road_lines_index = rtree.index.Index()
    buffered_lines = []
    for idx, road in enumerate(roads):
        b = road.geometry.buffer(20)
        buffered_lines.append((b, road))
        road_lines_index.insert(idx, b.bounds)

    inter_segments = {'lines': defaultdict(list), 'data': defaultdict(list)}
    roads_with_int_segments = []
    count = 0
    for int_buffer in int_buffers:
        match_segments = []
        for idx in road_lines_index.intersection(int_buffer[0].bounds):
            match_segments.append(Segment(roads[idx].geometry.intersection(
                int_buffer[0]), roads[idx].properties))
        int_segments = get_connections(int_buffer[1], match_segments)
        # Each road_with_int is a road segment and a list of lists of segments
        # representing the intersections
        # to-do: turn these into intersection objects
        roads_with_int_segments.append((roads[idx], int_segments))

        for int_segment in int_segments:
            inter_segments['lines'][count] = [x.geometry for x in int_segment]
            inter_segments['data'][count] = [x.properties for x in int_segment]
            count += 1

    non_int_lines = []
    for road_info in roads_with_int_segments:

#        util.output_polygons([(road_info[0].geometry, {})], 'non_inter.geojson')
        # Check against each separate intersection
        inter_lines = []
        for inter in road_info[1]:
            inter_lines.append(unary_union([x.geometry for x in inter]))
#        util.output_polygons([(unary_union([x for x in inter_lines]), {})], 'inter_combo.geojson')

        diff = road_info[0].geometry.difference(unary_union([x for x in inter_lines]))
#            diff = diff.difference(unary_union([x.geometry for x in inter]))

        if diff != road_info[0]:
            if 'LineString' == diff.type:
                non_int_lines.append(geojson.Feature(
                    geometry=geojson.LineString([x for x in diff.coords]),
                    properties=road.properties)
                )
            elif 'MultiLineString' == diff.type:
                coords = []
                for l in diff:
                    for coord in l.coords:
                        coords.append(coord)
                non_int_lines.append(geojson.Feature(
                    geometry=geojson.LineString(coords),
                    properties=road.properties)
                )
        else:
            non_int_lines.append(geojson.Feature(
                geometry=geojson.LineString([x for x in road_info[0].geometry.coords])))

    return non_int_lines, inter_segments


def add_point_based_features(non_inters, inters, jsonfile,
                             feats_filename=None,
                             additional_feats_filename=None,
                             forceupdate=False):
    """
    Add any point-based set of features to existing segment data.
    If it isn't already attached to the segments
    Args:
        non_inters
        inters
        jsonfile - points_joined.json, storing the results of snapping
        feats_filename - geojson file for point-based features data
        addtiional_feats_filename (optional) - file for additional
            points-based data, in json format
        forceupdate - if True, re-snap points and write to file
    """

    if forceupdate or not os.path.exists(jsonfile):
        features = []
        if feats_filename:
            features = util.read_records_from_geojson(feats_filename)
        if additional_feats_filename:
            features += util.read_records(
                additional_feats_filename, 'record')
        print('Snapping {} point-based features'.format(len(features)))
        seg, segments_index = util.index_segments(
            inters + non_inters
        )

        util.find_nearest(features, seg, segments_index, 20, type_record=True)

        # Dump to file
        print("output {} point-based features to {}".format(
            len(features), jsonfile))
        with open(jsonfile, 'w') as f:
            json.dump([r.properties for r in features], f)

    else:
        features = util.read_records(jsonfile, None)
        print("Read {} point-based features from file".format(len(features)))
    matches = {}

    for feature in features:
        near = feature.near_id
        feat_type = feature.properties['feature']

        if near:
            if str(near) not in matches:
                matches[str(near)] = {}
            if feat_type not in matches[str(near)]:
                matches[str(near)][feat_type] = 0
            matches[str(near)][feat_type] += 1

    # Add point data to intersections
    for i, inter in enumerate(inters):
        if str(inter['properties']['id']) in list(matches.keys()):
            matched_features = matches[str(inter['properties']['id'])]
            # Since intersections consist of multiple segments, add the
            # point-based properties to each of them

            for prop in inter['properties']['data']:
                for feat in matched_features:
                    prop[feat] = matched_features[feat]

    # Add point data to non-intersections
    for i, non_inter in enumerate(non_inters):
        if str(non_inter['properties']['id']) in list(matches.keys()):
            matched_features = matches[non_inter['properties']['id']]

            n = copy.deepcopy(non_inter)

            for feat in matched_features:
                n['properties'][feat] = matched_features[feat]

            non_inters[i] = n

    return non_inters, inters


def get_intersection_name(inter_segments):
    """
    Get an intersection name from a set of intersection segment names
    Args:
        inter_segments - a list of properties
    Returns:
        intersection name - a string, e.g. First St and Second St
    """

    streets = []
    # Some open street maps segments have more than one name in them
    for street in [x['name'] if 'name' in x.keys() else None
                   for x in inter_segments]:
        if street:
            if '[' in street:
                streets.extend(re.sub("['\[\]]", '', street).split(', '))
            else:
                streets.append(street)
    streets = sorted(list(set(streets)))

    name = ''
    if not streets:
        return name
    if len(streets) == 2:
        name = streets[0] + " and " + streets[1]
    else:
        name = streets[0] + " near "
        name += ', '.join(streets[1:-1]) + ' and ' + streets[-1]

    return name


def get_non_intersection_name(non_inter_segment, inters_by_id):
    """
    Get non-intersection segment names. Mostly in the form:
    X Street between Y Street and Z Street, but sometimes the
    intersection has streets with two different names, in which case
    it will be X Street between Y Street/Z Street and A Street,
    or it's a dead end, in which case it will be X Street from Y Street
    Args:
        non_inter_segment - a geojson non intersection segment
        inters_by_id - a dict with osm node ids as keys
    Returns:
        The display name string
    """

    properties = non_inter_segment['properties']

    if 'name' not in properties or not properties['name']:
        return ''
    segment_street = properties['name']
    from_streets = None
    to_streets = None
    if properties['from'] in inters_by_id and inters_by_id[properties['from']]:
        from_street = inters_by_id[properties['from']]
        from_streets = from_street.split(', ')

        # Remove any street that's part of the named street sections
        if segment_street in from_streets:
            from_streets.remove(segment_street)
    if properties['to'] in inters_by_id and inters_by_id[properties['to']]:
        to_street = inters_by_id[properties['to']]
        to_streets = to_street.split(', ')

        # Remove any street that's part of the named street sections
        if segment_street in to_streets:
            to_streets.remove(segment_street)

    if not from_streets and not to_streets:
        return segment_street

    from_street = None
    if from_streets:
        from_street = '/'.join(from_streets)
    to_street = None
    if to_streets:
        to_street = '/'.join(to_streets)

    if not to_streets:
        return segment_street + ' from ' + from_street
    if not from_streets:
        return segment_street + ' from ' + to_street

    return segment_street + ' between ' + from_street + \
        ' and ' + to_street

    return segment_street


def create_segments_from_json(roads_shp_path, mapfp):

    roads, inters = util.get_roads_and_inters(roads_shp_path)
    print("read in {} road segments".format(len(roads)))

    # unique id did not get included in shapefile, need to add it for adjacency
    for i, road in enumerate(roads):
        road.properties['orig_id'] = int(str(99) + str(i))

    # Initial buffer = 20 meters
    int_buffers = get_intersection_buffers(inters, 20)

    non_int_lines, inter_segments = find_non_ints(
        roads, int_buffers)

    non_int_w_ids = []

    # Allow intersections that don't have osmids, because this
    # happens when we generate alternate maps from city data
    # They won't have display names, and this is okay, because
    # we only use them to map to the osm segments
    inters_by_id = {
        x['properties']['osmid'] if 'osmid' in x['properties'] else '0':
        x['properties']['streets']
        if 'streets' in x['properties'] else None
        for x in inters
    }

    for i, l in enumerate(non_int_lines):
        value = copy.deepcopy(l)
        value['type'] = 'Feature'
        value['properties']['id'] = '00' + str(i)
        value['properties']['inter'] = 0
        value['properties']['display_name'] = get_non_intersection_name(
            l, inters_by_id)
        non_int_w_ids.append(value)

        x, y = util.get_center_point(value)
        x, y = util.reproject([[x, y]], inproj='epsg:3857',
                              outproj='epsg:4326')[0]['coordinates']
        value['properties']['center_y'] = y
        value['properties']['center_x'] = x

    print("extracted {} non-intersection segments".format(len(non_int_w_ids)))

    # Planarize intersection segments
    # Turns the list of LineStrings into a MultiLineString
    union_inter = []
    for idx, lines in list(inter_segments['lines'].items()):

        lines = unary_union(lines)
        coords = []
        # Fixing issue where we had previously thought a dead-end node
        # was an intersection. Once this is fixed in osmnx
        # (or we have a better work around), this should be able to
        # be taken out
        if type(lines) == LineString:
            lines = MultiLineString([lines.coords])
        for line in lines:
            coords += [[x for x in line.coords]]

        name = get_intersection_name(inter_segments['data'][idx])
        # Add the number of segments coming into this intersection
        segment_data = []
        for segment in list(inter_segments['data'][idx]):
            segment['intersection_segments'] = len(
                inter_segments['data'][idx])
            segment_data.append(segment)

        properties = {
            'id': idx,
            'data': segment_data,
            'display_name': name
        }
        value = geojson.Feature(
            geometry=geojson.MultiLineString(coords),
            id=idx,
            properties=properties,
        )
        x, y = util.get_center_point(value)
        x, y = util.reproject([[x, y]], inproj='epsg:3857',
                              outproj='epsg:4326')[0]['coordinates']

        value['properties']['center_x'] = x
        value['properties']['center_y'] = y
        union_inter.append(value)

    return non_int_w_ids, union_inter


def write_segments(non_inters, inters, mapfp, datafp):

    # Store non-intersection segments

    # Project back into 4326 for storage

    non_inters = util.prepare_geojson(non_inters)

    with open(os.path.join(
            mapfp, 'non_inters_segments.geojson'), 'w') as outfile:
        geojson.dump(non_inters, outfile)

    # Get just the properties for the intersections
    inter_data = {
        str(x['properties']['id']): x['properties']['data'] for x in inters}

    with open(os.path.join(datafp, 'inters_data.json'), 'w') as f:
        json.dump(inter_data, f)

    # Store the individual intersections without properties, since QGIS appears
    # to have trouble with dicts of dicts, and viewing maps can be helpful
    int_w_ids = [{
        'geometry': x['geometry'],
        'properties': {
            'id': x['properties']['id'],
            'display_name': x['properties']['display_name']
                if 'display_name' in x['properties'] else '',
            'center_x': x['properties']['center_x']
                if 'center_x' in x['properties'] else '',
            'center_y': x['properties']['center_y']
                if 'center_y' in x['properties'] else ''
        }
    } for x in inters]
    
    int_w_ids = util.prepare_geojson(int_w_ids)

    with open(os.path.join(mapfp, 'inters_segments.geojson'), 'w') as outfile:
        geojson.dump(int_w_ids, outfile)

    # Store the combined segments with all properties
    segments = non_inters['features'] + int_w_ids['features']

    with open(os.path.join(mapfp, 'inter_and_non_int.geojson'), 'w') as outfile:
        geojson.dump(geojson.FeatureCollection(segments), outfile)


if __name__ == '__main__':

    parser = argparse.ArgumentParser()
    parser.add_argument("-d", "--datadir", type=str,
                        help="Can give alternate data directory")
    parser.add_argument("-r", "--altroad", type=str,
                        help="Can give alternate road elements geojson file." +
                        " This is generated by extract_intersections.py")
    parser.add_argument("-n", "--newmap", type=str,
                        help="If given, write output to new directory" +
                        "within the maps directory")
    parser.add_argument('--forceupdate', action='store_true',
                        help='Whether to force update the points-based data')

    args = parser.parse_args()
    DATA_FP = args.datadir
    PROCESSED_DATA_FP = os.path.join(args.datadir, 'processed')
    MAP_FP = os.path.join(args.datadir, 'processed/maps')

    if args.newmap:
        PROCESSED_DATA_FP = os.path.join(MAP_FP, args.newmap)
        MAP_FP = PROCESSED_DATA_FP

    print("Creating segments..........................")

    elements = os.path.join(
        MAP_FP, 'osm_elements.geojson')
    if args.altroad:
        elements = args.altroad

    non_inters, inters = create_segments_from_json(elements, MAP_FP)

    feats_file = os.path.join(MAP_FP, 'features.geojson')
    additional_feats_file = os.path.join(
        DATA_FP, 'standardized', 'points.json')
    if not os.path.exists(feats_file):
        feats_file = None
    if not os.path.exists(additional_feats_file):
        additional_feats_file = None

    if feats_file or additional_feats_file:
        jsonfile = os.path.join(DATA_FP, 'processed', 'points_joined.json')
        non_inters, inters = add_point_based_features(
            non_inters,
            inters,
            jsonfile,
            feats_filename=feats_file,
            additional_feats_filename=additional_feats_file,
            forceupdate=args.forceupdate
        )
    write_segments(non_inters, inters, MAP_FP, PROCESSED_DATA_FP)


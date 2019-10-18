#!/usr/bin/env python
#
# SPDX-License-Identifier: BSD-3-Clause
#

import argparse
import io
import logging
import textwrap
import pprint
import jinja2
import requests

proxies = {
 "http": "http://proxy.houston.hpecorp.net:8080",
 "https": "http://proxy.houston.hpecorp.net:8080",
}

LOG = logging.getLogger(__name__)

SCHEMA_BASE = 'http://redfish.dmtf.org/schemas/'

COMMON_NAME_CHANGES = {
    'Oem': 'OEM',
    'Id': 'ID',
}

COMMON_DESC = {
    'Description': 'Description provides a description of this resource.',
    'Id': 'ID uniquely identifies the resource.',
    'Name': 'Name is the name of the resource or array element.',
    '@odata.context': 'ODataContext is the odata context.',
    '@odata.etag': 'ODataEtag is the odata etag.',
    '@odata.id': 'ODataID is the odata identifier.',
    '@odata.type': 'ODataType is the odata type.',
    'Identifier': 'Identifier shall be unique within the managed ecosystem.',
}

def _ident(name):
    outname = name

    outname = outname.replace('-','_')              # converts dashes to underbars
    outname = outname.replace('switch','Switch')    # Watch out for keyword switch
    outname = outname.replace(' ','')               # Collapse spaces
    outname = outname.replace(':','_')               # Collapse spaces
    outname = outname.replace('/','_div_')
    outname = outname.replace('+','_plus_')
    return outname

def _format_comment(name, description, cutpoint='used', add=' is'):
    if name in COMMON_DESC:
        return '// %s' % COMMON_DESC[name]

    if cutpoint not in description:
        cutpoint = ''

    lines = textwrap.wrap(
        '%s%s %s' % (name, add, description[description.index(cutpoint):]))
    return '\n'.join([('// %s' % l) for l in lines])


def _get_desc(obj):
    desc = obj.get('longDescription')
    if not desc:
        desc = obj.get('description', '')
    return desc


def _get_type(name, obj):
    result = 'String'
    tipe = obj.get('type')
    anyof = obj.get('anyOf') or obj.get('items', {}).get('anyOf')
    if 'count' in name.lower():
        result = 'Int'
    elif name == 'Status':
        result = 'common.Status'
    elif name == 'Identifier':
        # result = 'common.Identifier'
        result = 'Id'
    elif name == 'Description':
        result = 'String'
    elif tipe == 'object':
        result = name
    elif isinstance(tipe, list):
        for kind in tipe:
            if kind == 'null':
                continue
            if kind == 'integer' or kind == 'number':
                result = 'Int'
            elif kind == 'boolean':
                result = 'Boolean'
            else:
                result = kind
    elif isinstance(anyof, list):
        for kind in anyof:
            if '$ref' in kind:
                result = kind['$ref'].split('/')[-1]
    elif '$ref' in obj.get('items', {}):
        result = obj['items']['$ref'].split('/')[-1]
    elif name[:1] == name[:1].lower() and 'odata' not in name.lower(): # doesn't work
        result = 'common.Link'

    if tipe == 'array':
        # result = '[]' + result
        result = 'List' + result

    if 'odata' in name or name in COMMON_NAME_CHANGES:
        #result = '%s `json:"%s"`' % (result, name)
        result = result

    return result


def _add_object(params, name, obj):
    """Adds object information to our template parameters."""
    class_info = {
        'name': name,
        'identname' : _ident(name),
        'description': _format_comment(name, _get_desc(obj)),
        'attrs': []}

    for prop in obj.get('properties', []):
        if prop in ['Name', 'Id']:
            continue
        prawp = obj['properties'][prop]
        if prawp.get('deprecated'):
            continue
        attr = {'name': COMMON_NAME_CHANGES.get(prop, prop)}

        if '@odata' in prop:
            props = prop.split('.')
            replacement = 'OData'
            if 'count' in props[-1]:
                replacement = ''
            attr['name'] = '%s%s' % (
                props[0].replace('@odata', replacement), props[-1].title())
        attr['type'] = _get_type(prop, prawp)
        attr['description'] = _format_comment(
            prop, _get_desc(prawp))
        class_info['attrs'].append(attr)
    params['classes'].append(class_info)


def _add_enum(params, name, enum):
    """Adds enum information to our template parameters."""
    enum_info = {
        'name': name,
        'identname' : _ident(name),
        'description': _format_comment(name, _get_desc(enum)),
        'members': []}

    for en in enum.get('enum', []):
        member = {'identname': _ident(en), 'name': en}
        if enum.get('enumLongDescriptions', {}).get(en):
            desc = enum.get('enumLongDescriptions', {}).get(en)
        else:
            desc = enum.get('enumDescriptions', {}).get(en, '')
        member['description'] = _format_comment(_ident('%s%s' % (en, name)), desc, cutpoint='shall', add='')
        enum_info['members'].append(member)
    params['enums'].append(enum_info)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        'object',
        help='The Redfish schema object to process.')
    parser.add_argument(
        '-o',
        '--output',
        help='File to write results to. Default is to stdout.')
    parser.add_argument(
        '-v', '--verbose', action='store_true',
        help='Emit verbose output to help debug.')
    parser.add_argument(
        '-s', '--source',
        help='Specify source template file.')

    args = parser.parse_args()

    url = '%s%s.json' % (SCHEMA_BASE, args.object)
    LOG.debug(url)
    sourcefile = '%s' % (args.source)

    data = requests.get(url, proxies=proxies)
    try:
        base_data = data.json()
    except Exception:
        LOG.exception('Error with data:\n%s' % data)
        return

    for classdef in base_data.get('definitions', []):
        if classdef == args.object:
            refs = base_data['definitions'][classdef].get('anyOf', [])
            for ref in refs:
                reflink = ref.get('$ref', '')
                if 'idRef' in reflink:
                    continue
                refurl = reflink.split('#')[0]
                if refurl > url:
                    url = refurl
            break

    object_data = requests.get(url, proxies=proxies).json()
    params = {'object_name': args.object, 'classes': [], 'enums': []}

    for name in object_data['definitions']:
        if name == 'Actions':
            continue
        definition = object_data['definitions'][name]
        if definition.get('type') == 'object':
            properties = definition.get('properties', '')
            if not ('target' in properties and 'title' in properties):
                _add_object(params, _ident(name), definition)
        elif definition.get('enum'):
            _add_enum(params, name, definition)
        else:
            LOG.debug('Skipping %s', definition)
    # pp = pprint.PrettyPrinter(indent=4)
    # pp. pprint(params)

    outputfile = '%s' % args.output
    print(outputfile)

    with io.open('source.tmpl', 'r', encoding='utf-8') as f:
        template_body = f.read()

    template = jinja2.Template(template_body)
    # print(template.render(**params))
    template.stream(**params).dump(outputfile)


if __name__ == '__main__':
    main()

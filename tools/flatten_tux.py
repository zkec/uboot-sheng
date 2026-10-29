#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Retain the maintained Tux contours and remove its highlight/shadow layers.

Original illustration: Larry Ewing / The GIMP. Vector: Garrett LeSage,
refined by IFo Hancroft, garrett/Tux (CC0). This is a flat-color derivative.
"""
from pathlib import Path
import xml.etree.ElementTree as E

OUT=Path(__file__).resolve().parents[1]/'assets'
source=E.fromstring((OUT/'tux-standard-color-source.svg').read_bytes())
SVG='http://www.w3.org/2000/svg'
E.register_namespace('',SVG)
colors={
    'Body Without Tummy':'#242424','Tummy':'#f5f5f5',
    'Right Arm':'#242424','Left Arm':'#242424','Hand':'#242424',
    'Right Foot':'#f6c344','Left Foot':'#f6c344',
    'Left Eyeball':'#f5f5f5','Right Eyeball':'#f5f5f5',
    'Left Eye Pupil':'#242424','Right Eye Pupil':'#242424',
    'Lower Beak':'#e9b72f','Upper Beak':'#f6c344',
    'Right Nostril':'#242424','Left Nostril':'#242424',
}
result=E.Element('{'+SVG+'}svg',{'viewBox':'0 0 '+source.get('width')+' '+source.get('height'),
                                'width':source.get('width'),'height':source.get('height')})
seen=set()
def walk(node,transforms=()):
    tag=node.tag.rsplit('}',1)[-1]
    if tag=='defs':return
    transforms=transforms+((node.get('transform'),) if node.get('transform') else ())
    label=next((v for k,v in node.attrib.items() if k.endswith('}label')),'')
    if tag=='path' and label in colors:
        assert label not in seen;seen.add(label)
        attrs={'d':node.attrib['d'],'fill':colors[label]}
        if transforms:attrs['transform']=' '.join(transforms)
        E.SubElement(result,'{'+SVG+'}path',attrs)
    for child in node:walk(child,transforms)
walk(source)
assert seen==set(colors),(seen,set(colors))
(OUT/'tux-standard-flat-color.svg').write_bytes(E.tostring(result,encoding='utf-8',xml_declaration=True))
print('Standard Tux flattened:',len(seen),'original contour layers; no filters or gradients')

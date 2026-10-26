#!/usr/bin/env python3
"""Compare two kernel modules (.ko or .ko.xz) section by section.

Usage: maintainers/compare-module.py OLD.ko[.xz] NEW.ko[.xz]

Prints each differing ELF section. Exit 0 when the only differences are the
ones caused by nixpkgs' cc-wrapper -frandom-seed=<output hash prefix>
(recorded in DW_AT_producer): .debug_str with an identical string set apart
from the producer string, its .rela.debug_info offsets, and the GNU build-id.
"""
import lzma
import re
import struct
import sys

def load(p):
    data = open(p, 'rb').read()
    return lzma.decompress(data) if p.endswith('.xz') else data

def sections(x):
    shoff = struct.unpack_from('<Q', x, 0x28)[0]
    shentsize, shnum, shstrndx = struct.unpack_from('<HHH', x, 0x3A)
    secs = []
    for i in range(shnum):
        name, typ, flags, addr, off, size = struct.unpack_from('<IIQQQQ', x, shoff + i * shentsize)
        secs.append([name, typ, x[off:off + size] if typ != 8 else b''])
    stroff = struct.unpack_from('<IIQQQQ', x, shoff + shstrndx * shentsize)[4]
    out = {}
    for name, _typ, body in secs:
        n = x[stroff + name:x.index(b'\0', stroff + name)].decode()
        out[n] = body
    return out

a, b = sections(load(sys.argv[1])), sections(load(sys.argv[2]))
seed = re.compile(rb'-frandom-seed=[0-9a-z]+')
differing = sorted(n for n in set(a) | set(b) if a.get(n) != b.get(n))
explained = True
for n in differing:
    print('differs:', n)
    if n == '.debug_str':
        sa = set(seed.sub(b'SEED', s) for s in a[n].split(b'\0'))
        sb = set(seed.sub(b'SEED', s) for s in b[n].split(b'\0'))
        same = sa == sb
        print('  string set identical after normalizing -frandom-seed:', same)
        explained &= same
    elif n not in ('.rela.debug_info', '.note.gnu.build-id'):
        explained = False
print('sections compared:', len(set(a) | set(b)), 'identical:', len(set(a) | set(b)) - len(differing))
print('RESULT:', 'identical apart from -frandom-seed' if explained else 'UNEXPLAINED DIFFERENCE')
sys.exit(0 if explained else 1)

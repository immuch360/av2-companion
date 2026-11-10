#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FreeKC
"""The machine and the imported DLLs of a Windows program, read from its PE tables.

av2_build.py checks with it that immuch360-av2.exe needs nothing but DLLs of Windows itself (the C runtime is static).
It reads the import directory and the delay load directory of a 32 or 64 bit PE file; a forwarded or bound import
changes nothing here, only the names of the DLLs are read.

  pe_imports.py FILE
      the machine and the DLLs of FILE, one per line

Only the Python standard library is used.
"""
from __future__ import annotations

import pathlib
import struct
import sys

MACHINES = {0x8664: 'x64', 0xAA64: 'arm64', 0x014C: 'x86'}


class NotPe(ValueError):
    pass


def read_pe(data: bytes) -> dict:
    """{'machine': 'x64'|'arm64'|'x86'|hex, 'imports': [dll], 'delay_imports': [dll]} of the PE file data"""
    if len(data) < 0x40 or data[:2] != b'MZ':
        raise NotPe('not a PE file')
    pe, = struct.unpack_from('<I', data, 0x3C)
    if data[pe:pe + 4] != b'PE\0\0':
        raise NotPe('no PE signature')
    machine, sections, _, _, _, optional_size, _ = struct.unpack_from('<HHIIIHH', data, pe + 4)
    optional = pe + 24
    magic, = struct.unpack_from('<H', data, optional)
    if magic == 0x20B:
        image_base, = struct.unpack_from('<Q', data, optional + 24)
        count, = struct.unpack_from('<I', data, optional + 108)
        directories = optional + 112
    elif magic == 0x10B:
        image_base, = struct.unpack_from('<I', data, optional + 28)
        count, = struct.unpack_from('<I', data, optional + 92)
        directories = optional + 96
    else:
        raise NotPe(f'unknown optional header {magic:#x}')

    table = optional + optional_size
    spans = []
    for index in range(sections):
        virtual_size, virtual_address, raw_size, raw_pointer = struct.unpack_from('<IIII', data, table + index * 40 + 8)
        spans.append((virtual_address, max(virtual_size, raw_size), raw_pointer))

    def offset(rva: int) -> int:
        for start, size, raw in spans:
            if start <= rva < start + size:
                return rva - start + raw
        raise NotPe(f'address {rva:#x} outside the sections')

    def name_at(rva: int) -> str:
        start = offset(rva)
        return data[start:data.index(b'\0', start)].decode('ascii', 'replace')

    def directory(index: int) -> tuple:
        if index >= count:
            return 0, 0
        return struct.unpack_from('<II', data, directories + index * 8)

    imports = []
    rva, size = directory(1)
    if rva and size:
        at = offset(rva)
        while any(data[at:at + 20]):
            name_rva, = struct.unpack_from('<I', data, at + 12)
            imports.append(name_at(name_rva))
            at += 20

    delay = []
    rva, size = directory(13)
    if rva and size:
        at = offset(rva)
        while True:
            attributes, name_rva = struct.unpack_from('<II', data, at)
            if not name_rva:
                break
            # Old linkers wrote virtual addresses instead of relative ones (attribute bit 0 clear)
            delay.append(name_at(name_rva if attributes & 1 else name_rva - image_base))
            at += 32

    return {'machine': MACHINES.get(machine, hex(machine)), 'imports': imports, 'delay_imports': delay}


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print('usage: pe_imports.py FILE', file=sys.stderr)
        return 2
    try:
        info = read_pe(pathlib.Path(argv[0]).read_bytes())
    except (OSError, NotPe, struct.error, ValueError) as error:
        print(f'pe_imports: {error}', file=sys.stderr)
        return 1
    print(f'machine {info["machine"]}')
    for dll in info['imports']:
        print(dll)
    for dll in info['delay_imports']:
        print(f'{dll} (delay loaded)')
    return 0


if __name__ == '__main__':
    sys.exit(main())

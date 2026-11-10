#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FreeKC
"""Plays an AV2 file (IVF or Section 5 OBUs) through immuch360-av2 serve, as Immuch360 Desktop does.

immuch360-av2 does not read files when it serves: the player side reads the container, cuts it into temporal units and
feeds them on the standard input of the program, which asks for them by its "need G U" lines (a new generation after
each seek). So this script is not a one way pipe: it starts the program itself, answers its lines, and prints the URL
of the virtual y4m on its first line of output, for mpv or any libmpv player.

  feed_ivf.py FILE [--program PATH] [--port P] [--threads N] [--depth 8] [--verbose]
      serves FILE until the program ends or Ctrl+C; prints http://127.0.0.1:PORT/TOKEN/video.y4m on the first line
      of the standard output, then the mpv options that open it, the lines of the program on the standard error
  feed_ivf.py FILE [...] --play [-- MPV OPTION...]
      the same, and plays the URL with mpv (on the PATH, or --mpv PATH) with the options the app opens it with;
      the options after -- go to mpv; ends with mpv, with its exit code

The steps, as the app takes them:
  1. "immuch360-av2 probe FILE": the size, depth, colour range and colour description of the first picture;
  2. the file indexed once, without reading it into memory: the temporal units (from one temporal delimiter OBU to the
     next) and the random access units among them (those with a CLOSED_LOOP_KEY OBU); a unit cut by the end of the
     file is left out;
  3. "immuch360-av2 serve" with those numbers, the token in the environment variable IMMUCH360_AV2_TOKEN (a command
     line is readable by every account of the computer) and --parent, so that the program never outlives this script;
  4. the index record, then for each "need G U" line the units from U on with generation G, and a record of length 0
     after the last unit, until the next "need" line.

Each record is a 16-byte header, little endian ("AV2R", generation, unit, length), then length bytes. Only the Python
standard library is used. Exit codes: 0 done, 1 the program failed, 2 bad arguments, 3 the file is not playable AV2.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import queue
import re
import secrets
import shutil
import signal
import struct
import subprocess
import sys
import threading

RECORD = struct.Struct('<4sIII')
INDEX_UNIT = 0xFFFFFFFF
TOKEN_VARIABLE = 'IMMUCH360_AV2_TOKEN'
TEMPORAL_DELIMITER, CLOSED_LOOP_KEY = 2, 4
HERE = pathlib.Path(__file__).resolve().parent
# The names mpv's format filter gives the colour codes (ITU-T H.273) it knows, as the app maps them
PRIMARIES = {1: 'bt.709', 4: 'bt.470m', 5: 'bt.601-625', 6: 'bt.601-525', 7: 'bt.601-525', 9: 'bt.2020', 11: 'dci-p3',
             12: 'display-p3'}
TRANSFERS = {1: 'bt.1886', 4: 'gamma2.2', 5: 'gamma2.8', 6: 'bt.1886', 8: 'linear', 13: 'srgb', 14: 'bt.1886',
             15: 'bt.1886', 16: 'pq', 18: 'hlg'}
MATRICES = {1: 'bt.709', 5: 'bt.601', 6: 'bt.601', 9: 'bt.2020-ncl', 10: 'bt.2020-cl'}


class Failure(Exception):
    pass


# --- the container ----------------------------------------------------------------------------------------------------

def leb128(data: bytes, at: int):
    """(value, bytes used) of the LEB128 number at data[at], None when it is cut"""
    value = 0
    for i in range(8):
        if at + i >= len(data):
            return None
        byte = data[at + i]
        value |= (byte & 0x7F) << (7 * i)
        if not byte & 0x80:
            return value, i + 1
    return None


def obus(data: bytes):
    """(offset, length, type) of each whole OBU of data: a LEB128 size that counts the header, then a header byte whose
    bits 2 to 6 are the type; stops at a cut one"""
    at = 0
    while at < len(data):
        size = leb128(data, at)
        if size is None or size[0] < 1 or at + size[1] + size[0] > len(data):
            return
        yield at, size[1] + size[0], (data[at + size[1]] >> 2) & 0x1F
        at += size[1] + size[0]


class Index:
    """The temporal units of an AV2 IVF or Section 5 file as (offset, length) in the file, the random access units
    among them and the frame rate of the IVF header; read once, a packet at a time"""

    def __init__(self, path: pathlib.Path):
        self.path = path
        self.units = []
        self.raps = []
        self.fps = (30, 1)
        with path.open('rb') as stream:
            head = stream.read(32)
            if head[:4] == b'DKIF':
                if head[8:12] != b'AV02':
                    raise Failure(f'{path.name}: an IVF of {head[8:12]!r}, not AV2')
                header, = struct.unpack_from('<H', head, 6)
                rate, scale = struct.unpack_from('<II', head, 16)
                if rate and scale:
                    self.fps = (rate, scale)
                stream.seek(header)
                while True:
                    at = stream.tell()
                    frame = stream.read(12)
                    if len(frame) < 12:
                        break
                    size, = struct.unpack_from('<I', frame, 0)
                    packet = stream.read(size)
                    self._packet(at + 12, packet, whole=len(packet) == size)
                    if len(packet) < size:
                        break
            else:
                # Section 5: the file is one row of OBUs, read in blocks; a unit ends where the next delimiter starts
                self._obu_file(stream)
        if not self.units:
            raise Failure(f'{path.name}: no whole temporal unit')

    def _packet(self, start: int, packet: bytes, whole: bool):
        """The units of one IVF packet; in a cut packet, a unit is whole when another delimiter follows it"""
        found = []
        end = 0
        for offset, length, kind in obus(packet):
            if kind == TEMPORAL_DELIMITER or not found:
                found.append([offset, offset + length, False])
            else:
                found[-1][1] = offset + length
            if kind == CLOSED_LOOP_KEY:
                found[-1][2] = True
            end = offset + length
        if found and (not whole or end != len(packet)):
            found.pop()  # cut by the end of the file, or the packet ends inside an OBU
        for offset, stop, key in found:
            if key:
                self.raps.append(len(self.units))
            self.units.append((start + offset, stop - offset))

    def _obu_file(self, stream):
        stream.seek(0)
        block = b''
        at = 0  # where the next OBU starts in block
        base = 0  # the offset of block[0] in the file
        unit = None  # [start, end, key] of the unit under way
        ended = False
        while True:
            size = leb128(block, at)
            if (size is None or size[1] + size[0] > len(block) - at) and not ended:
                # The next OBU is not whole in the block: the bytes read are dropped, then more are read
                block, base, at = block[at:], base + at, 0
                more = stream.read(max(4 << 20, (size[1] + size[0]) if size else 0))
                ended = not more
                block += more
                continue
            if size is None or size[0] < 1 or size[1] + size[0] > len(block) - at:
                break  # the end of the file, or an OBU it cuts or a wrong size
            length = size[1] + size[0]
            kind = (block[at + size[1]] >> 2) & 0x1F
            if kind == TEMPORAL_DELIMITER and unit is not None:
                self._add(unit)
                unit = None
            if unit is None:
                unit = [base + at, base + at + length, False]
            else:
                unit[1] = base + at + length
            if kind == CLOSED_LOOP_KEY:
                unit[2] = True
            at += length
        # The last unit is whole when the file ends right after its last OBU; otherwise it is left out
        if unit is not None and at == len(block):
            self._add(unit)

    def _add(self, unit: list):
        if unit[2]:
            self.raps.append(len(self.units))
        self.units.append((unit[0], unit[1] - unit[0]))


# --- the program ------------------------------------------------------------------------------------------------------

def find_program(given: str | None) -> pathlib.Path:
    if given:
        return pathlib.Path(given)
    name = 'immuch360-av2.exe' if os.name == 'nt' else 'immuch360-av2'
    for candidate in (shutil.which('immuch360-av2'), HERE.parent / 'build' / name, pathlib.Path.cwd() / name):
        if candidate and pathlib.Path(candidate).is_file():
            return pathlib.Path(candidate)
    raise Failure('no immuch360-av2 on the PATH nor in build/: give --program')


def probe(program: pathlib.Path, path: pathlib.Path, threads: int) -> dict:
    result = subprocess.run([str(program), 'probe', str(path), '--threads', str(threads)], capture_output=True,
                            timeout=120)
    text = result.stdout.decode('utf-8', 'replace').strip()
    try:
        info = json.loads(text.splitlines()[-1] if text else '{}')
    except json.JSONDecodeError:
        raise Failure(f'probe: not JSON: {text!r}') from None
    if result.returncode != 0 or not info.get('playable'):
        reason = info.get('error') or info.get('refusal') or f'exit code {result.returncode}'
        raise Failure(f'{path.name} is not playable: {reason}')
    return info


def mpv_options(info: dict) -> list:
    """The options the app opens the y4m with: the y4m demuxer without a probe, and the colours of the stream, which a
    y4m does not carry, by the format filter"""
    options = ['--demuxer-lavf-format=yuv4mpegpipe']
    colour = info.get('color', {})
    if colour.get('description'):
        parts = []
        for key, names, name in (('matrix', MATRICES, 'colormatrix'), ('primaries', PRIMARIES, 'primaries'),
                                 ('transfer', TRANSFERS, 'gamma')):
            if colour.get(key) in names:
                parts.append(f'{name}={names[colour[key]]}')
        if parts:
            options.append('--vf=format=' + ':'.join(parts))
    return options


class Feeder:
    """immuch360-av2 serve on one file: started, fed, and its lines read"""

    def __init__(self, program: pathlib.Path, index: Index, info: dict, args):
        self.index = index
        self.token = secrets.token_urlsafe(24)
        picture = info['picture']
        rate, scale = index.fps
        command = [str(program), 'serve', '--width', str(picture['width']), '--height', str(picture['height']),
                   '--bits', str(picture['bits']), '--fps', f'{rate}/{scale}', '--frames', str(len(index.units)),
                   '--range', info['color']['range'], '--threads', str(args.threads), '--parent', str(os.getpid())]
        if args.depth:
            command += ['--depth', str(args.depth)]
        elif info.get('y4m', {}).get('frame_bytes'):
            command += ['--frame-bytes', str(info['y4m']['frame_bytes'])]
        if args.port:
            command += ['--port', str(args.port)]
        environment = dict(os.environ, **{TOKEN_VARIABLE: self.token})
        self.verbose = args.verbose
        self.needs = queue.Queue()
        self.listening = queue.Queue()
        self.stopping = threading.Event()
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0,
                                        env=environment)
        threading.Thread(target=self._read_lines, daemon=True).start()
        threading.Thread(target=self._feed, daemon=True).start()
        line = self.listening.get(timeout=30)
        if not line.startswith('listening '):
            self.stop()
            raise Failure(f'serve did not listen: {line!r}')
        self.port = int(line.split()[1])
        self.url = f'http://127.0.0.1:{self.port}/{self.token}/video.y4m'

    def _read_lines(self):
        for raw in self.process.stdout:
            text = raw.decode('utf-8', 'replace').rstrip('\r\n')
            if text.startswith(('listening ', 'error ')):
                self.listening.put(text)
            match = re.match(r'^need (\d+) (\d+)$', text)
            if match:
                self.needs.put((int(match.group(1)), int(match.group(2))))
            if self.verbose or not text.startswith('stat '):
                print(f'immuch360-av2: {text}', file=sys.stderr, flush=True)
        self.listening.put('error the program ended')
        self.needs.put(None)

    def _write(self, generation: int, unit: int, payload: bytes) -> bool:
        try:
            self.process.stdin.write(RECORD.pack(b'AV2R', generation, unit, len(payload)) + payload)
            return True
        except (OSError, ValueError):
            return False

    def _feed(self):
        """The index first, then for each need the units from its unit on, until the next need"""
        if not self._write(0, INDEX_UNIT, b''.join(struct.pack('<I', rap) for rap in self.index.raps)):
            return
        with self.index.path.open('rb') as stream:
            need = self.needs.get()
            while need is not None and not self.stopping.is_set():
                generation, first = need
                need = None
                for unit in range(first, len(self.index.units)):
                    try:
                        need = self.needs.get_nowait()
                        break
                    except queue.Empty:
                        pass
                    offset, length = self.index.units[unit]
                    stream.seek(offset)
                    # A write waits while the queue of the program is full (a paused player); a seek empties it
                    if not self._write(generation, unit, stream.read(length)):
                        return
                else:
                    if not self._write(generation, len(self.index.units), b''):
                        return
                if need is None:
                    need = self.needs.get()

    def wait(self) -> int:
        return self.process.wait()

    def stop(self) -> int:
        """Closes the standard input, which ends the program within a second; killed if it is still there after 3 s"""
        self.stopping.set()
        try:
            self.process.stdin.close()
        except OSError:
            pass
        try:
            return self.process.wait(3)
        except subprocess.TimeoutExpired:
            self.process.kill()
            return self.process.wait()


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    extra = []
    if '--' in argv:
        at = argv.index('--')
        argv, extra = argv[:at], argv[at + 1:]
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('file', type=pathlib.Path, help='an AV2 file: IVF, or Section 5 OBUs')
    parser.add_argument('--program', help='immuch360-av2 (by default on the PATH, or build/immuch360-av2)')
    parser.add_argument('--port', type=int, default=0, help='the port to serve on (default: any free port)')
    parser.add_argument('--threads', type=int, default=0, help='threads of the decoder, 0 for every processor')
    parser.add_argument('--depth', type=int, choices=[8], help='serve 10-bit pictures as 8-bit samples')
    parser.add_argument('--play', action='store_true', help='play the URL with mpv; options after -- go to mpv')
    parser.add_argument('--mpv', default='mpv', help='the mpv program of --play')
    parser.add_argument('--verbose', action='store_true', help='also show the stat lines of the program')
    args = parser.parse_args(argv)
    if extra and not args.play:
        parser.error('options after -- are for mpv: add --play')
    feeder = None
    # A kill (SIGTERM) stops as Ctrl+C does: the standard input of the program closed, which ends it
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        program = find_program(args.program)
        info = probe(program, args.file, args.threads)
        index = Index(args.file)
        feeder = Feeder(program, index, info, args)
        options = mpv_options(info)
        print(feeder.url, flush=True)
        print('mpv ' + ' '.join(options) + ' ' + feeder.url, flush=True)
        print(f'{args.file.name}: {info["picture"]["width"]}x{info["picture"]["height"]}, {info["picture"]["bits"]} '
              f'bits, {len(index.units)} pictures at {index.fps[0]}/{index.fps[1]} fps, {len(index.raps)} random '
              'access points', file=sys.stderr, flush=True)
        if args.play:
            player = subprocess.run([args.mpv, *options, *extra, feeder.url])
            feeder.stop()
            return player.returncode
        code = feeder.wait()
        return 0 if code == 0 else 1
    except Failure as error:
        print(f'feed_ivf: {error}', file=sys.stderr)
        return 3 if 'not playable' in str(error) or 'not AV2' in str(error) else 1
    except (OSError, queue.Empty) as error:
        print(f'feed_ivf: {error or "the program did not answer"}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        if feeder is not None and feeder.process.poll() is None:
            feeder.stop()


if __name__ == '__main__':
    sys.exit(main())

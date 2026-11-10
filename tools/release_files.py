#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FreeKC
"""The files of a release of av2-companion, from the three archives of one run of its CI.

The release job of .github/workflows/build.yml runs it on a tag v*: it takes the archives
Immuch360-av2-decoder-<lock id>-<system>.zip the windows, linux and macos jobs built, checks each one as its job did
(av2_build.py check: its files, its sums, its licences, the libraries and imports of its program, no string of GPL
code) and that the three were built from one commit, the commit of the tag, then writes next to them:

  AV2-DECODER-LICENSES.md    what the decoder is made of and under which licences, the files of each archive with their
                             sizes and SHA-256, where the source is, and the full licence texts
  licence-<name>.txt         each licence text the archives carry, as a file of its own
  av2-companion-<version>.json
                             the entry of each archive for a catalogue of components (that of Immuch360 Desktop):
                             its download URL, size, SHA-256 and the files it holds with their sizes, sums and modes
  SHA256SUMS                 the SHA-256 of every file of the release, written last

  release_files.py --archives DIR --out DIR --tag vX.Y.Z [--commit SHA] [--run ID] [--repo OWNER/NAME] [--date D]
                   [--notes FILE] [--require-signed-windows]
      the files above in the folder --out (the archives are copied there); --notes writes the text of the release
      from the section of CHANGELOG.md for the version, outside --out; --require-signed-windows refuses a Windows
      archive whose program carries no Authenticode signature or whose BUILD-INFO.txt does not say it is signed

Only the Python standard library is used, and no Python cache is written next to the scripts. Exit codes: 0 done,
1 a check failed, 2 bad arguments or files.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import io
import json
import pathlib
import re
import shutil
import struct
import sys
import zipfile

sys.dont_write_bytecode = True

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import av2_build  # noqa: E402

NOTICES = 'AV2-DECODER-LICENSES.md'
SUMS = 'SHA256SUMS'
CHANGELOG = ROOT / 'CHANGELOG.md'
# The id of each archive in the catalogue of components of Immuch360 Desktop, and the programs it holds
COMPONENTS = {'windows-x64': 'av2-decoder-windows-x64', 'linux-x64': 'av2-decoder-linux-x64',
              'macos-universal': 'av2-decoder-macos-universal'}
PROGRAMS = {'windows-x64': {'immuch360-av2.exe'}, 'linux-x64': {'immuch360-av2', 'immuch360-run'},
            'macos-universal': {'immuch360-av2'}}


class Failure(Exception):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def files_of(data: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(data)) as bundle:
        return {info.filename: bundle.read(info) for info in bundle.infolist() if not info.is_dir()}


def built_from(files: dict) -> str | None:
    """The commit an archive was built from, as its BUILD-INFO.txt says"""
    found = re.search(rb'^immuch360-av2 \S+/tree/([0-9a-f]{40}) ', files.get('BUILD-INFO.txt', b''), re.M)
    return found.group(1).decode() if found else None


def has_certificate(data: bytes) -> bool:
    """Whether the PE file data carries a certificate table (an Authenticode signature)"""
    if len(data) < 0x40 or data[:2] != b'MZ':
        return False
    pe, = struct.unpack_from('<I', data, 0x3C)
    if data[pe:pe + 4] != b'PE\0\0':
        return False
    optional = pe + 24
    magic, = struct.unpack_from('<H', data, optional)
    directories = optional + (112 if magic == 0x20B else 96)
    count, = struct.unpack_from('<I', data, directories - 4)
    if count <= 4:
        return False
    table, size = struct.unpack_from('<II', data, directories + 4 * 8)
    return bool(table and size) and table + size <= len(data)


def signature_of(system: str, files: dict) -> str:
    """How the program of an archive is signed, as the manifest says it"""
    if system == 'windows-x64':
        signed = av2_build.SIGNED_LINE.encode() in files.get('BUILD-INFO.txt', b'') and \
            has_certificate(files.get('immuch360-av2.exe', b''))
        return 'Authenticode, APROGSYS (Azure Artifact Signing)' if signed else 'none'
    return 'ad hoc' if system == 'macos-universal' else 'none'


def changelog_section(version: str, text: str) -> str:
    """The body of the section ## [version] of CHANGELOG.md"""
    match = re.search(rf'^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## |\Z)', text, re.M | re.S)
    if not match:
        raise Failure(f'CHANGELOG.md has no section [{version}]')
    return match.group(1).strip()


def notices_text(tag: str, date: str, lock: dict, commit: str, names: dict, archives: dict) -> str:
    readme = (av2_build.LICENCES / 'README.md').read_text(encoding='utf-8').strip().split('\n', 1)[1].strip()
    lines = [f'# av2-companion {tag}: the licences of the AV2 decoder', '', f'Date: {date}.', '', readme, '',
             '## The archives', '',
             f'Built from {av2_build.REPOSITORY}/tree/{commit} (lock {lock["id"]}: dav2d '
             f'{lock["dav2d"]["commit"]}, {lock["dav2d"]["describe"]}).', '',
             '| Archive | Component of Immuch360 Desktop | Bytes | SHA-256 |', '|---|---|---:|---|']
    for system, name in names.items():
        data = archives[system]
        lines.append(f'| `{name}` | `{COMPONENTS[system]}` | {len(data)} | `{sha256(data)}` |')
    for system, name in names.items():
        lines += ['', f'### {name}', '', '| File | Bytes | SHA-256 |', '|---|---:|---|']
        for path, content in sorted(files_of(archives[system]).items()):
            lines.append(f'| `{path}` | {len(content)} | `{sha256(content)}` |')
    lines += ['', '## The licence texts', '']
    for path, content in sorted(licence_texts(archives).items()):
        lines += [f'### {path}', '', '```', content.decode('utf-8').rstrip('\n'), '```', '']
    return '\n'.join(lines).rstrip('\n') + '\n'


def licence_texts(archives: dict) -> dict:
    """The licence texts of the archives (the Linux one has them all, immuch360-run included), by file name"""
    return {path.removeprefix('licenses/'): content for path, content in files_of(archives['linux-x64']).items()
            if path.startswith('licenses/') and path.endswith('.txt')}


def manifest(tag: str, repo: str, run: str | None, commit: str, date: str, lock: dict, names: dict,
             archives: dict) -> dict:
    base = f'https://github.com/{repo}/releases/download/{tag}/'
    entries = {}
    for system, name in names.items():
        data = archives[system]
        entries[COMPONENTS[system]] = {
            'system': system,
            'assetName': name,
            'url': base + name,
            'size': len(data),
            'sha256': sha256(data),
            'signature': signature_of(system, files_of(data)),
            'archiveFiles': [{'path': path, 'size': len(content), 'sha256': sha256(content),
                              'executable': path in PROGRAMS[system]}
                             for path, content in sorted(files_of(data).items())],
        }
    return {'name': 'av2-companion', 'tag': tag, 'version': tag.removeprefix('v'),
            'repository': f'https://github.com/{repo}', 'commit': commit, 'run': run, 'date': date,
            'protocol': 1, 'lock': lock['id'],
            'dav2d': {key: lock['dav2d'][key] for key in ('repository', 'commit', 'describe')},
            'archives': entries}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--archives', type=pathlib.Path, required=True)
    parser.add_argument('--out', type=pathlib.Path, required=True)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--commit')
    parser.add_argument('--run')
    parser.add_argument('--repo', default='immuch360/av2-companion')
    parser.add_argument('--date', default=datetime.date.today().isoformat())
    parser.add_argument('--notes', type=pathlib.Path)
    parser.add_argument('--lock', type=pathlib.Path, default=av2_build.LOCK)
    parser.add_argument('--require-signed-windows', action='store_true')
    args = parser.parse_args(argv)
    if not re.fullmatch(r'v\d+\.\d+\.\d+', args.tag):
        print(f'--tag {args.tag!r} is not vX.Y.Z')
        return 2
    try:
        lock = av2_build.load_lock(args.lock)
        names = {system: av2_build.artifact_name(lock, system) for system in COMPONENTS}
        args.out.mkdir(parents=True, exist_ok=True)
        archives, problems, commits = {}, [], set()
        for system, name in names.items():
            source = args.archives / name
            if not source.is_file():
                raise Failure(f'no {name} in {args.archives}')
            archives[system] = source.read_bytes()
            target = args.out / name
            if source.resolve() != target.resolve():
                shutil.copyfile(source, target)
            if av2_build.main(['--lock', str(args.lock), 'check', '--system', system, '--archive', str(target)]):
                problems.append(f'{name}: av2_build.py check failed')
            commits.add(built_from(files_of(archives[system])))
            if system == 'windows-x64' and args.require_signed_windows and \
                    signature_of(system, files_of(archives[system])) == 'none':
                problems.append(f'{name}: immuch360-av2.exe is not signed (no Authenticode certificate, or no line '
                                'of BUILD-INFO.txt saying it is)')
        if len(commits) != 1 or None in commits:
            problems.append(f'the three archives come from {sorted(str(c) for c in commits)}, not one commit')
        commit = next(iter(commits))
        if args.commit and commit != args.commit:
            problems.append(f'the archives were built from {commit}, the tag {args.tag} is at {args.commit}')
        for problem in problems:
            print(f'::error::{problem}')
        if problems:
            return 1

        (args.out / NOTICES).write_text(notices_text(args.tag, args.date, lock, commit, names, archives),
                                        encoding='utf-8', newline='\n')
        for name, content in licence_texts(archives).items():
            (args.out / f'licence-{name}').write_bytes(content)
        record = manifest(args.tag, args.repo, args.run, commit, args.date, lock, names, archives)
        (args.out / f'av2-companion-{record["version"]}.json').write_text(json.dumps(record, indent=2) + '\n',
                                                                          encoding='utf-8', newline='\n')
        made = sorted(path for path in args.out.iterdir() if path.is_file() and path.name != SUMS)
        (args.out / SUMS).write_text(''.join(f'{sha256(path.read_bytes())}  {path.name}\n' for path in made),
                                     encoding='utf-8', newline='\n')
        for path in [*made, args.out / SUMS]:
            print(f'{path.stat().st_size:>10}  {sha256(path.read_bytes())}  {path.name}')

        if args.notes:
            changes = changelog_section(record['version'], CHANGELOG.read_text(encoding='utf-8'))
            rows = '\n'.join(f'| `{name}` | {len(archives[system])} | `{sha256(archives[system])}` |'
                             for system, name in names.items())
            args.notes.write_text('\n'.join([
                changes, '',
                '### Downloads', '',
                '| Archive | Bytes | SHA-256 |', '|---|---:|---|', rows, '',
                f'Built by {av2_build.REPOSITORY}/actions/runs/{args.run} from {commit} (dav2d '
                f'{lock["dav2d"]["commit"][:7]}, lock {lock["id"]}). Licences and the files of each archive: '
                f'`{NOTICES}`. Every file of the release: `{SUMS}`. The entries for a catalogue of components: '
                f'`av2-companion-{record["version"]}.json`.', '',
            ]), encoding='utf-8', newline='\n')
        return 0
    except (Failure, av2_build.Failure, OSError, zipfile.BadZipFile, json.JSONDecodeError, KeyError) as error:
        print(f'::error::release_files: {error}')
        return 2 if isinstance(error, (OSError, KeyError)) else 1


if __name__ == '__main__':
    sys.exit(main())

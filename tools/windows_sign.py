#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FreeKC
"""Signs immuch360-av2.exe with Azure Artifact Signing (formerly Trusted Signing), on the Windows runner of a release.

Azure Artifact Signing keeps the certificate of the publisher, APROGSYS, issued by Microsoft (Microsoft ID Verified CS
EOC CA 03), and its private key: signtool of the Windows SDK signs through the client library of the service (the
dlib), which sends the digest of the file to the signing account and gets the signature back. The windows job of
.github/workflows/build.yml runs this script on a tag vX.Y.Z only, with the three credentials of the signing account
(AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, secrets of the repository) in the environment of its steps,
where the client reads them; this script never reads them. The same tools and settings as the release workflow of
Immuch360 Desktop, which signs the app.

  windows_sign.py tools --dir DIR [--packages DIR]
      signtool (Microsoft.Windows.SDK.BuildTools) and the client (Microsoft.ArtifactSigning.Client) from nuget.org,
      at the versions the official azure/artifact-signing-action v2.0.0 installs, each checked by its SHA-256 and
      unpacked in DIR, with metadata.json (endpoint, account, certificate profile) and signing.json, which the
      commands below read. --packages takes the two .nupkg files from a folder instead of nuget.org.
  windows_sign.py sign --tools DIR FILE ...
      signs the files (SHA-256 digest, RFC 3161 timestamp of Microsoft, SHA-256 too), and tries a file that is not
      signed afterwards twice more; the log of signtool also goes to sign.log in the tools folder
  windows_sign.py verify --tools DIR FILE ...
      signtool verify /pa /v of each file, its output printed: a valid signature of APROGSYS, with a timestamp

Then av2_build.py signed puts the signed program back into its archive (it refuses a program that differs by more than
its signature). Only the Python standard library is used. Exit codes: 0 good, 1 a file is not signed as it must be,
2 bad arguments or missing files.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile

sys.dont_write_bytecode = True

# The signing account: none of these is a secret
PUBLISHER = 'APROGSYS'
ENDPOINT = 'https://weu.codesigning.azure.net/'
ACCOUNT = 'APROGSYS'
PROFILE = 'APROGSYS-PublicTrust'
TIMESTAMP = 'http://timestamp.acs.microsoft.com'
DESCRIPTION = 'immuch360-av2'
DESCRIPTION_URL = 'https://github.com/immuch360/av2-companion'

NUGET = 'https://api.nuget.org/v3-flatcontainer'
# The packages of azure/artifact-signing-action v2.0.0 (its "Set variables" step), with the SHA-256 of each package
# as nuget.org served it on 22 October 2026, when its SHA-512 matched the one of the nuget.org catalog, and the folder
# of the package that is unpacked
PACKAGES = {
    'buildtools': ('Microsoft.Windows.SDK.BuildTools', '10.0.26100.4188',
                   '180deb372659029864c10a0c04787833234d64aacd1d2c0661d2c00295d8e022', 'bin/10.0.26100.0/x64/'),
    'client': ('Microsoft.ArtifactSigning.Client', '1.0.128',
               '74bd7d27e6ce1051409c38d9b46bc8df0400ecd643d51ffbf2ac00869061e40b', 'bin/x64/'),
}
# Every credential of DefaultAzureCredential but the environment's: the client signs with the variables of the step or
# not at all, instead of trying the Azure CLI or a managed identity of the runner
EXCLUDED_CREDENTIALS = [
    'WorkloadIdentityCredential', 'ManagedIdentityCredential', 'SharedTokenCacheCredential', 'VisualStudioCredential',
    'VisualStudioCodeCredential', 'AzureCliCredential', 'AzurePowerShellCredential', 'AzureDeveloperCliCredential',
    'InteractiveBrowserCredential',
]


class Problem(Exception):
    pass


# --- the tools ---------------------------------------------------------------------------------------------------


def download(url: str) -> bytes:
    for attempt in range(1, 4):
        try:
            request = urllib.request.Request(url, headers={'User-Agent': 'av2-companion-windows-sign'})
            with urllib.request.urlopen(request, timeout=300) as response:
                return response.read()
        except OSError as error:
            if attempt == 3:
                raise Problem(f'{url}: {error}') from None
            print(f'{url}: {error}, trying again')
            time.sleep(10 * attempt)
    raise AssertionError


def tools(args) -> int:
    folder = args.dir.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    for key, (name, version, digest, prefix) in PACKAGES.items():
        file = f'{name.lower()}.{version}.nupkg'
        if args.packages:
            path = args.packages / file
            if not path.is_file():
                raise Problem(f'no {file} in {args.packages}')
            data = path.read_bytes()
        else:
            data = download(f'{NUGET}/{name.lower()}/{version}/{file}')
        found = hashlib.sha256(data).hexdigest()
        if found != digest:
            raise Problem(f'{file}: SHA-256 {found}, {digest} expected')
        target = folder / key
        if target.exists():
            shutil.rmtree(target)
        count = 0
        with zipfile.ZipFile(io.BytesIO(data)) as package:
            for entry in package.infolist():
                if not entry.filename.startswith(prefix) or entry.is_dir():
                    continue
                relative = pathlib.PurePosixPath(entry.filename[len(prefix):])
                if relative.is_absolute() or '..' in relative.parts:
                    raise Problem(f'{file}: unsafe path {entry.filename}')
                out = target.joinpath(*relative.parts)
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(package.read(entry))
                count += 1
        print(f'{name} {version}: SHA-256 checked, {count} files in {target}')

    signtool_exe = folder / 'buildtools' / 'signtool.exe'
    dlib = folder / 'client' / 'Azure.CodeSigning.Dlib.dll'
    for path in (signtool_exe, dlib):
        if not path.is_file():
            raise Problem(f'{path.name} is not in its package')
    metadata = {'Endpoint': ENDPOINT, 'CodeSigningAccountName': ACCOUNT, 'CertificateProfileName': PROFILE,
                'ExcludeCredentials': EXCLUDED_CREDENTIALS}
    if os.environ.get('GITHUB_RUN_ID'):
        # Found again in the logs of the signing account
        metadata['CorrelationId'] = (f'{os.environ.get("GITHUB_REPOSITORY", "")} run {os.environ["GITHUB_RUN_ID"]} '
                                     f'attempt {os.environ.get("GITHUB_RUN_ATTEMPT", "1")}')
    (folder / 'metadata.json').write_text(json.dumps(metadata, indent=2) + '\n', encoding='utf-8')
    settings = {'signtool': [str(signtool_exe)], 'dlib': str(dlib), 'metadata': str(folder / 'metadata.json'),
                'log': str(folder / 'sign.log')}
    (folder / 'signing.json').write_text(json.dumps(settings, indent=2) + '\n', encoding='utf-8')
    print(f'endpoint {ENDPOINT}, account {ACCOUNT}, certificate profile {PROFILE}: {folder / "signing.json"}')
    return 0


def load_tools(folder: pathlib.Path) -> dict:
    path = folder / 'signing.json'
    if not path.is_file():
        raise Problem(f'no {path}: run windows_sign.py tools --dir {folder} first')
    return json.loads(path.read_text(encoding='utf-8'))


def signtool(settings: dict, arguments: list) -> subprocess.CompletedProcess:
    return subprocess.run(settings['signtool'] + arguments, capture_output=True, text=True, encoding='utf-8',
                          errors='replace')


# --- signing and checking ----------------------------------------------------------------------------------------


def read_signature(output: str) -> dict:
    """The signer, its issuer and the timestamp in the output of signtool verify /pa /v for one file"""
    signer = issuer = None
    chain = output.split('Signing Certificate Chain:', 1)
    if len(chain) == 2:
        section = re.split(r'^\s*(?:The signature is timestamped|File is not timestamped|Timestamp Verified by)',
                           chain[1], maxsplit=1, flags=re.MULTILINE)[0]
        # The chain goes from the root down to the certificate of the signer, the last one
        subjects = re.findall(r'^\s*Issued to:\s*(.+?)\s*$', section, re.MULTILINE)
        issuers = re.findall(r'^\s*Issued by:\s*(.+?)\s*$', section, re.MULTILINE)
        signer = subjects[-1] if subjects else None
        issuer = issuers[-1] if issuers else None
    stamp = re.search(r'^\s*The signature is timestamped:\s*(.+?)\s*$', output, re.MULTILINE)
    return {'signer': signer, 'issuer': issuer, 'timestamp': stamp.group(1) if stamp else None,
            'verified': bool(re.search(r'^\s*Successfully verified:', output, re.MULTILINE))}


def verify_file(settings: dict, path: pathlib.Path) -> dict:
    result = signtool(settings, ['verify', '/pa', '/v', str(path)])
    found = read_signature(result.stdout + result.stderr)
    found['valid'] = result.returncode == 0 and found['verified']
    found['output'] = result.stdout + result.stderr
    return found


def ours(signature: dict) -> bool:
    return bool(signature['valid'] and signature['signer'] == PUBLISHER and signature['timestamp'])


def describe(signature: dict) -> str:
    if not signature['valid']:
        return 'no valid signature'
    stamp = f'timestamped {signature["timestamp"]}' if signature['timestamp'] else 'not timestamped'
    return f'signed by {signature["signer"]} (issuer {signature["issuer"]}), {stamp}'


def sign_files(settings: dict, files: list, attempts: int = 3) -> None:
    arguments = ['sign', '/v', '/debug', '/fd', 'SHA256', '/tr', TIMESTAMP, '/td', 'SHA256', '/dlib', settings['dlib'],
                 '/dmdf', settings['metadata'], '/d', DESCRIPTION, '/du', DESCRIPTION_URL]
    pending = list(files)
    for attempt in range(1, attempts + 1):
        result = signtool(settings, arguments + [str(path) for path in pending])
        text = result.stdout + result.stderr
        print(text, end='' if text.endswith('\n') else '\n')
        with open(settings['log'], 'a', encoding='utf-8') as log:
            log.write(text)
        # 0 signed, 2 signed with warnings: each file is checked below whatever signtool answered
        if result.returncode not in (0, 2):
            print(f'signtool exit {result.returncode} for {len(pending)} files')
        pending = [path for path in pending if not ours(verify_file(settings, path))]
        if not pending:
            return
        if attempt < attempts:
            print(f'not signed yet: {", ".join(path.name for path in pending)}; trying again')
            time.sleep(15 * attempt)
    raise Problem(f'not signed by {PUBLISHER} with a timestamp: ' + ', '.join(str(path) for path in pending))


def sign(args) -> int:
    settings = load_tools(args.tools)
    files = [pathlib.Path(path).resolve() for path in args.files]
    missing = [str(path) for path in files if not path.is_file()]
    if missing:
        raise FileNotFoundError(', '.join(missing))
    sign_files(settings, files)
    for path in files:
        print(f'{path.name}: {describe(verify_file(settings, path))}')
    return 0


def verify(args) -> int:
    settings = load_tools(args.tools)
    failed = 0
    for name in args.files:
        signature = verify_file(settings, pathlib.Path(name))
        print(signature['output'].rstrip('\n'))
        print(f'{name}: {describe(signature)}')
        if not ours(signature):
            failed += 1
    return 1 if failed else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest='command', required=True)
    get = commands.add_parser('tools')
    get.add_argument('--dir', type=pathlib.Path, required=True)
    get.add_argument('--packages', type=pathlib.Path)
    for name in ('sign', 'verify'):
        command = commands.add_parser(name)
        command.add_argument('--tools', type=pathlib.Path, required=True)
        command.add_argument('files', nargs='+')
    args = parser.parse_args(argv)
    try:
        return {'tools': tools, 'sign': sign, 'verify': verify}[args.command](args)
    except Problem as error:
        print(f'::error::{error}')
        return 1
    except (OSError, json.JSONDecodeError) as error:
        print(f'::error::{error}')
        return 2


if __name__ == '__main__':
    sys.exit(main())

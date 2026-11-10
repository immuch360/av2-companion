# Changelog

The versions of av2-companion. The archives of a release are named after the lock of dav2d they were built with
(`Immuch360-av2-decoder-<lock id>-<system>.zip`); the protocol of `serve` has its own number, given by `--version`.

## [1.0.0] (2026-11-10)

The first release of this repository: `immuch360-av2` as shipped in Immuch360 Desktop build 24 of 9 November 2026
(the archives `av2-decoder-1` of the Immuch360 repository), now in a repository of its own.

* **The program**: `probe` and `serve`, protocol 1, the same code and the same `--help` as in build 24. It decodes AV2
  (IVF and Section 5 OBUs, 4:2:0, 8 and 10 bits) with dav2d and serves the pictures as a virtual y4m over HTTP on
  127.0.0.1, with ranges and seeks, the token in the environment, `--parent`, `--depth 8` and the `stat` lines.
* **dav2d** `30140b563596a1b14aef808ff021c90288948315` (`0.0.1-525-g30140b56`, 2 November 2026), linked statically:
  lock `30140b5-1`, Meson 1.7.0, Ninja 1.13.0, nasm 2.16.03.
* **Archives** for Windows x64 (MSVC with its static runtime, one `.exe`), Linux x64 (Ubuntu 22.04, glibc 2.35, with
  the launcher `immuch360-run`) and macOS 12 or later (arm64 and x86_64 in one program, signed ad hoc), each with its
  licences, `BUILD-INFO.txt` and `SHA256SUMS.txt`.
* **New here**: the MIT licence for `immuch360-av2` and its scripts (it was under the AGPL 3.0 of the Immuch360
  repository); `tools/feed_ivf.py`, which plays any AV2 file through the program with mpv; a CI of its own that builds
  the three archives, runs every check of `tools/av2_serve_check.py` on them and plays a 1080p clip with mpv; releases
  with the archives, `AV2-DECODER-LICENSES.md`, each licence text, `av2-companion-1.0.0.json` (the size and SHA-256 of
  each archive and of every file in it, for a catalogue of components) and the sources with dav2d.
* **Changed from build 24**: `BUILD-INFO.txt` and `licenses/immuch360-av2-SOURCE.txt` point at this repository, and
  `licenses/immuch360-av2-MIT.txt` replaces `licenses/immuch360-av2-AGPL-3.0.txt`. The Windows program is not signed
  (the `av2-decoder-1` one is, by APROGSYS).

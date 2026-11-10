# av2-companion

**AV2 video playback for mpv and libmpv players.**

[![Build](https://github.com/immuch360/av2-companion/actions/workflows/build.yml/badge.svg)](https://github.com/immuch360/av2-companion/actions/workflows/build.yml)
[![Latest release](https://img.shields.io/github/v/release/immuch360/av2-companion)](https://github.com/immuch360/av2-companion/releases/latest)
[![Licence: MIT](https://img.shields.io/badge/licence-MIT-blue)](LICENSE)

`immuch360-av2` is a small program that lets a video player play AV2 videos today. It decodes the video with
[dav2d](https://code.videolan.org/videolan/dav2d), the AV2 decoder of VideoLAN, and serves the pictures on your own
computer as an ordinary raw video stream (y4m over HTTP, on 127.0.0.1 only). Any player built on mpv or libmpv opens
that address like a file and plays it, seeks included, without knowing anything about AV2. It is the AV2 companion of
[Immuch360 Desktop](https://immuch360.com), where it plays the AV2 videos of your library, and it works the same with
mpv on its own.

* Windows x64, Linux x64 and macOS (Apple silicon and Intel), one program of about 2 MB, no library to install.
* AV2 in IVF files (`.ivf`) and in plain OBU streams (`.obu`), 4:2:0, 8 and 10 bits.
* Seeks to any picture: the program starts again at the random access point before it.
* A paused player costs nothing: the program waits, no processor time is spent.

## Contents

* [Why it exists](#why-it-exists)
* [Play an AV2 file with mpv](#play-an-av2-file-with-mpv)
* [Use it from your own player](#use-it-from-your-own-player)
* [Build it](#build-it)
* [How Immuch360 Desktop uses it](#how-immuch360-desktop-uses-it)
* [The protocol](#the-protocol)
* [Limits](#limits)
* [Credits and licences](#credits-and-licences)

## Why it exists

AV2 is the video codec of the Alliance for Open Media that follows AV1: its specification 1.0.0 came out on 28 May
2026. The players of today cannot play it yet: FFmpeg, and so mpv, have no AV2 decoder (none in the main branch of
FFmpeg at the end of October 2026). dav2d, the AV2 decoder of VideoLAN, already decodes it well on the processor, but
it is a library, and a player would have to be rebuilt around it.

This program closes that gap without touching the player. It decodes with dav2d and hands the player raw pictures in a
format every FFmpeg reads (yuv4mpegpipe), over a local HTTP address the player can seek in. A player that can open a
URL can play AV2, today, and will switch to its own decoder the day FFmpeg has one.

## Play an AV2 file with mpv

You need Python 3.9 or later, mpv, and the archive of your computer from the
[latest release](https://github.com/immuch360/av2-companion/releases/latest):

| Computer | Archive |
|---|---|
| Windows x64 | `Immuch360-av2-decoder-30140b5-1-windows-x64.zip` |
| Linux x64 (glibc 2.35 or later) | `Immuch360-av2-decoder-30140b5-1-linux-x64.zip` |
| macOS 12 or later, Apple silicon and Intel | `Immuch360-av2-decoder-30140b5-1-macos-universal.zip` |

On Linux, for example:

```
curl -LO https://github.com/immuch360/av2-companion/releases/latest/download/Immuch360-av2-decoder-30140b5-1-linux-x64.zip
unzip Immuch360-av2-decoder-30140b5-1-linux-x64.zip -d av2
git clone https://github.com/immuch360/av2-companion
python3 av2-companion/tools/feed_ivf.py video.ivf --program av2/immuch360-av2 --play
```

On Windows, `py av2-companion\tools\feed_ivf.py video.ivf --program av2\immuch360-av2.exe --play`, with `mpv.exe` on the
PATH. On macOS, an archive downloaded by a browser is in quarantine: `xattr -d com.apple.quarantine av2/immuch360-av2`
lets the program run (it is signed ad hoc, not by an Apple developer account).

`tools/feed_ivf.py` does what a player has to do: it reads the container, starts the program, feeds it the video and
gives mpv the address with the right options. Without `--play` it prints the address and waits, so that you can open it
in any player:

```
$ python3 tools/feed_ivf.py clips/lighthouse-1080p30-8bit-60f.ivf
http://127.0.0.1:41267/ZBq6p0f8u3m2Yt1vQh9aXw0c3J5LrN2e/video.y4m
mpv --demuxer-lavf-format=yuv4mpegpipe http://127.0.0.1:41267/ZBq6p0f8u3m2Yt1vQh9aXw0c3J5LrN2e/video.y4m
```

Options after `--` go to mpv. The test the CI runs, 30 pictures with no window and no sound:

```
python3 tools/feed_ivf.py clips/lighthouse-1080p30-8bit-60f.ivf --play -- --no-config --vo=null --ao=null --frames=30
```

Ctrl+C, closing mpv or ending the script ends the program too.

## Use it from your own player

A player that embeds libmpv (or calls mpv) needs no change to its decoding: it starts the program, writes the video to
its standard input, and opens the URL it gets. The steps, which `tools/feed_ivf.py` follows:

1. **Probe the file**: `immuch360-av2 probe FILE` prints one line of JSON: the size, depth, colour range and colour
   description of the first picture, and `"playable": true` or the reason it is not.
2. **Index the container**: cut the file into temporal units (from one temporal delimiter OBU to the next) and note the
   random access units among them (those holding a `CLOSED_LOOP_KEY` OBU).
3. **Start the server**: `immuch360-av2 serve --width W --height H --bits 8|10 --fps NUM/DEN --frames N --range
   limited|full --parent PID`, with a random token of 16 to 128 letters, digits, `-` or `_` in the environment variable
   `IMMUCH360_AV2_TOKEN`. The program answers `listening PORT ...` on its standard output.
4. **Feed it**: write the index record, then answer each `need G U` line with the units from U on (see
   [the protocol](#the-protocol)).
5. **Play** `http://127.0.0.1:PORT/TOKEN/video.y4m` with these options:

| Option | Value | Why |
|---|---|---|
| `demuxer-lavf-format` | `yuv4mpegpipe` | the stream is y4m: no probe, the first picture comes sooner |
| `demuxer-max-bytes` | `268435456` or more | raw pictures are large: 256 MiB hold 86 pictures of 1080p in 8 bits |
| `cache-pause-wait` | `1` | a decoder sends its pictures steadily, one second of them is enough to start again |
| `vf` | `format=colormatrix=bt.709:primaries=bt.709:gamma=bt.1886` | only when the probe gives a colour description: a y4m does not carry one |

With the client API of libmpv, in C:

```c
mpv_set_option_string(mpv, "demuxer-lavf-format", "yuv4mpegpipe");
mpv_set_option_string(mpv, "demuxer-max-bytes", "268435456");
mpv_set_option_string(mpv, "cache-pause-wait", "1");
const char *command[] = {"loadfile", "http://127.0.0.1:41267/ZBq6p0f8u3m2Yt1vQh9aXw0c3J5LrN2e/video.y4m", NULL};
mpv_command(mpv, command);
```

6. **Stop**: close the standard input of the program. It ends within a second, whatever the player is doing.

The player seeks as in any file: mpv opens a new connection with a `Range` header, the program maps it to a picture,
starts again at the random access point before it when it has to, and asks you for those units with a new `need` line.

## Build it

The CI builds every archive of a release (`.github/workflows/build.yml`). To build by hand you need a C compiler
(gcc, clang or Visual Studio 2022), Python 3.9 or later, Meson 1.7.0, Ninja 1.13.0 and nasm 2.16.03, the versions of
`tools/av2.lock.json` (the archive check refuses a program another nasm assembled). On Linux, as these commands were
run for this README, without root:

```
git clone https://github.com/immuch360/av2-companion
cd av2-companion
python3 -m venv .venv
.venv/bin/python -m pip install meson==1.7.0 ninja==1.13.0
curl -sSfLO https://www.nasm.us/pub/nasm/releasebuilds/2.16.03/nasm-2.16.03.tar.xz
echo "1412a1c760bbd05db026b6c0d1657affd6631cd0a63cddb6f73cc6d4aa616148  nasm-2.16.03.tar.xz" | sha256sum -c -
tar -xJf nasm-2.16.03.tar.xz
(cd nasm-2.16.03 && ./configure --quiet && make -s -j4 nasm)
export PATH="$PWD/.venv/bin:$PWD/nasm-2.16.03:$PATH"
python3 tools/av2_build.py sources
python3 tools/av2_build.py build --system linux-x64 --build build --jobs 4
gcc -O2 -Wall -Wextra -Werror -o build/immuch360-run launcher/immuch360_run.c
python3 tools/av2_build.py pack --system linux-x64 --program build/immuch360-av2 --launcher build/immuch360-run --out dist
python3 tools/av2_build.py check --system linux-x64 --archive dist/Immuch360-av2-decoder-30140b5-1-linux-x64.zip --run
python3 tools/feed_ivf.py clips/lighthouse-1080p30-8bit-60f.ivf --play -- --no-config --vo=null --ao=null --frames=30
```

`sources` fetches dav2d at the commit of the lock into `subprojects/dav2d` and checks its licence files against the
SHA-256 of the lock. `build` runs Meson with the options of the lock (a static dav2d without its tools, release, no
asserts); a plain `meson setup build && meson compile -C build` works too. `check --run` checks the archive (its sums,
its licences, the libraries its program needs, no GPL code in it), then runs its program through the checks of
`tools/av2_serve_check.py`: the MD5 of every picture of the fixtures against those of the reference decoder of AVM
v1.0.0, ranges, seeks, the token, 127.0.0.1 only, a paused player, the ends of the program. With 4 jobs the build
took 36 s for this README and the checks 18 s.

On Windows the build runs in a Visual Studio 2022 developer environment found by Meson (`--system windows-x64`, the
static C runtime); on macOS `--system macos-universal` builds arm64 and x86_64 and joins them with `lipo`. The jobs of
`.github/workflows/build.yml` give the exact steps for each.

## How Immuch360 Desktop uses it

[Immuch360](https://github.com/freeKC/Immuch360) is a free app that plays flat, 360° and 3D photos and videos from
your device, your NAS or your Immich server, on phones, TVs, headsets and computers. Its desktop app plays videos with
libmpv, and AV2 videos through this program:

* the app downloads the archive of its computer the first time an AV2 video plays and checks it by its SHA-256
  (`av2-companion-VERSION.json` of each release gives the sums of each archive and of every file in it); it works
  offline afterwards;
* it reads the container itself, from a local file or a share, and feeds the units as `tools/feed_ivf.py` does;
* it starts the program under its launcher, a job object on Windows and `immuch360-run` on Linux, with `--parent`, so
  that the program never outlives the app, and keeps the token out of the command line;
* it opens the URL in its libmpv with the options above, and reads the `stat` lines to tell whether this computer
  decodes the video fast enough.

## The protocol

### Commands

* `immuch360-av2 probe FILE|- [--threads N]`: what the start of an AV2 file holds, as one line of JSON (the sequence
  header, the first picture decoded: size, depth, layout, colour description and range; `playable`, and `format`, the
  words for a stream that cannot be played: "4:4:4 10-bit", "4:2:0 12-bit"...). It reads 1 MiB first, then four times
  more while no picture came out, up to 64 MiB; `-` reads the standard input until it closes. Exit code 3 and
  `{"error": "..."}` when it is not AV2 or not readable.
* `immuch360-av2 serve --width W --height H --bits 8|10 --fps NUM/DEN --frames N [--threads N] [--range limited|full]
  [--depth 8] [--frame-bytes B] [--port P] [--parent PID] [--token TOKEN]`: decodes the units of its standard input and
  serves them, below. The token comes from the environment variable `IMMUCH360_AV2_TOKEN`: every account of the
  computer can read the command line of a process (`/proc/PID/cmdline` on Linux, `ps` on macOS), only its own account
  its environment. `--token` is for runs by hand, and wins when both are given.
* `immuch360-av2 --version`: `immuch360-av2 protocol 1, source <commit>`, `dav2d <commit> (<describe>)`,
  `simd <levels the processor has>, uses <level dav2d uses>`. A player checks the protocol number before it starts
  `serve`.
* `immuch360-av2 --help`: its text is frozen in `tests/fixtures/immuch360_av2_help.txt`.

### The HTTP stream

The program listens on 127.0.0.1 only (a free port unless `--port`) and serves `http://127.0.0.1:PORT/TOKEN/video.y4m`:
a y4m header of its own (`YUV4MPEG2 W.. H.. F<fps> Ip A1:1 C420|C420p10 XCOLORRANGE=LIMITED|FULL`, with the frame rate
given by `--fps`), then `--frames` records of `FRAME\n` and the picture (4:2:0, 8 bits, or 10 bits in 16 bit little
endian words; `--depth 8` serves 10-bit pictures as 8-bit samples). Its length is known before any decoding:
`header + frames x (6 + frame bytes)`.

A request (GET or HEAD, `Range: bytes=a-`, `a-b` or `-n`) maps to picture k and an offset in it. The decoder goes on
from where it is when the random access unit before k is not after its position; otherwise it starts again there: a new
generation, and the line `need G U`. The pictures before k are dropped without being written. Another token gets 403,
another path 404, a range past the end 416; a request from an address other than 127.0.0.1 is closed. Each connection
is read by a short thread of its own, within 2 s of its arrival (then 400), and beyond 8 such connections at once the
next ones are closed (`refused busy`). One request is served at a time: a new one ends the one being served at once,
even while its write is blocked on a player that stopped reading. A player that stops reading (pause) blocks the write,
then the decoder, then the writes on the standard input: no processor time is spent.

### Standard input: records

Every record is a 16-byte header, little endian, then `length` bytes:

| Bytes | Field |
|---|---|
| 0 to 3 | `AV2R` |
| 4 to 7 | generation |
| 8 to 11 | unit |
| 12 to 15 | length |

1. First, the index: generation 0, unit 4294967295 (`0xFFFFFFFF`), the numbers of the temporal units that are random
   access points (a `CLOSED_LOOP_KEY` OBU), as 32-bit numbers, in order, the first one 0.
2. Then, for each line `need G U` of the program, the temporal units U, U+1... with generation G, each record holding
   one whole unit (from its temporal delimiter to the next one), and after the last unit of the file a record of length
   0 (unit = the number of units) so that the decoder gives its last pictures. A new `need` line stops the feeding of
   the previous generation: records of another generation are read and dropped. The first `need` comes with the first
   request of the player, so units sent before it are dropped too: a one way pipe cannot feed the program.

Unit k gives picture k of the y4m: each temporal unit of the streams of avmenc shows one picture, in the order of
display after the decoder. The program keeps 64 units (or 32 MiB) in its queue and stops reading when it is full, which
is how a paused player holds the feeder back.

### Standard output: lines

| Line | When |
|---|---|
| `listening PORT address=127.0.0.1 length=L header=H frame=F frames=N` | once, when the socket listens |
| `need G U` | the decoder starts again at unit U, generation G |
| `request bytes=A-B frame=K` | a request is served from picture K |
| `stat decoded=D served=S fps=X simd=LEVEL gen=G frame=K serving=0\|1` | every half second, always |
| `refused token\|path\|method\|range\|address\|request\|busy` | a request was refused |
| `error KIND MESSAGE` | then the program ends with the code below |
| `exit input-closed` | the standard input closed |
| `exit parent-gone` | the process of `--parent` ended |

`fps` is the speed at which this computer decodes and serves the video, measured on runs of pictures that the decoder
alone paced (the start of playback, after each seek, and all the time on a computer too slow for the video); `0.0`
until the first one. A player that reads at the pace of the video from the start may keep it at `0.0`: count `decoded`
against the clock then.

### The end

* **Closing the standard input ends the program** within a second (`exit input-closed`, code 0), however full its
  queue: it asks the pipe itself every 100 ms, on the monotonic clock, whether its writer left.
* **The player gone**: a line that cannot be written (the `stat` line comes every half second) ends the program too,
  code 0.
* **`--parent PID`**: the program also ends (`exit parent-gone`, code 0) when that process ends (`OpenProcess` on
  Windows, `pidfd_open` on Linux, `kqueue` on macOS).

### Exit codes

0 the standard input or output closed, or the `--parent` process ended; 1 internal error (memory, threads); 2 bad
arguments (and a `--parent` process that cannot be watched); 3 the video cannot be decoded (a dav2d error,
`error decode`, or a picture of another size or depth than `serve` was told, `error format`); 4 no socket on
127.0.0.1; 5 records that are not the protocol's.

## Limits

* **Software decoding.** dav2d decodes on the processor, with its SIMD code (SSE2 to AVX2 on x86, NEON on ARM); no
  graphics card is used. Measured with dav2d alone on 2 November 2026, in multiples of real time:

| Processor | 1080p30 8-bit | 4K30 8-bit | 1080p30 10-bit | 4K24 10-bit |
|---|---:|---:|---:|---:|
| Intel Core i5-1145G7 (laptop, 4 cores) | 4.0x | 1.24x | 1.20x | 0.31x |
| Intel Core i9-13980HX (laptop, 24 cores) | 7.7x | | | 0.99x |

  8-bit video plays up to 4K30 on a recent 4 core laptop. 10-bit video is three to four times slower: the SIMD code of
  the loop filters of dav2d exists in 8 bits only for now, so 10-bit 4K needs a high end processor and is at its limit
  there. Below 1.0x the video stalls; the `stat` lines tell a player when that happens.
* **The AV2 version.** dav2d is pinned at `30140b5` (`0.0.1-525-g30140b56`, 2 November 2026), a development version of
  dav2d that decodes the final AV2 bitstream, specification v1.0.0 of 28 May 2026. The test streams decode to the same
  pictures, bit for bit, as with the reference decoder of AVM v1.0.0. Streams made by encoders older than AVM v1.0.0
  (its research branches) may not decode. A newer dav2d comes with a new lock id and a new release.
* **Formats.** 4:2:0 in 8 and 10 bits; 4:0:0, 4:2:2, 4:4:4 and 12 bits are refused by the probe, with their name.
  One picture per temporal unit, as encoders write it. No audio: the program serves the video track only.
* **One video, one player.** Each program serves one file to one connection at a time; run one per video.
* **Windows signature.** The Windows program of the releases of this repository is not signed yet; Smart App Control,
  where it is on, may block it.

## Credits and licences

* **dav2d** by VideoLAN and the dav2d authors, BSD 2-Clause: <https://code.videolan.org/videolan/dav2d>. This program is
  a thin layer around it; the decoding is theirs.
* **AV2** by the Alliance for Open Media, whose patent licence travels with every AV2 implementation:
  <https://aomedia.org>. The reference decoder of AVM checks the test streams.
* `x86inc.asm` of the x264 project, part of dav2d, ISC.
* The test streams are cut from "Orbit around Fremantle South Mole Lighthouse at sunset" by JamesBromberger, Wikimedia
  Commons, CC0 1.0 (`tests/fixtures/FIXTURES.md`).

`immuch360-av2`, its scripts and `immuch360-run` are under the [MIT licence](LICENSE), Copyright (c) 2026 FreeKC. dav2d
is linked statically: its BSD 2-Clause licence, the ISC licence of `x86inc.asm` and the Alliance for Open Media Patent
License 1.0 are in [`licenses/`](licenses) and in every archive (`licenses/README.md` tells what each covers). No GPL
code is built in, and the CI checks it.

Made for [Immuch360](https://immuch360.com) ([source](https://github.com/freeKC/Immuch360)). Changes:
[CHANGELOG.md](CHANGELOG.md).

"""Fetch the most popular static (non-animated) BetterTTV emotes.

Writes, mirroring the Discord emoji layout at the repo root:
    bttv/bttv_emotes.json   [{"asset", "id", "names", "strings"}], most popular first
    bttv/png_urls.txt       one asset URL per line
    bttv/png/<id>.png       36x36
    bttv/png128x128/<id>.png 128x128

BTTV's largest size (3x) is at most 112px tall, so images are scaled to fit
the square (keeping aspect ratio) and centered on a transparent canvas.

Usage:
    python scripts/fetch_bttv.py [--count 300] [--no-globals] [--ffmpeg PATH] [--prune]
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

API = "https://api.betterttv.net/3"
CDN = "https://cdn.betterttv.net/emote/{id}/3x.png"
# The "top" endpoint returns 401 without a betterttv.com origin.
HEADERS = {
    "Origin": "https://betterttv.com",
    "Referer": "https://betterttv.com/",
    "User-Agent": "Mozilla/5.0 (emoji-repo fetch_bttv.py)",
}
SIZES = {"png": 36, "png128x128": 128}
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def get(url):
    req = urllib.request.Request(url, headers=HEADERS)
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read()
        except Exception:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)


def top_static(count):
    """Most-shared static emotes, deduplicated by code (highest usage wins)."""
    emotes, seen, before = [], set(), None
    while len(emotes) < count:
        url = f"{API}/emotes/shared/top?limit=100" + (f"&before={before}" if before else "")
        rows = json.loads(get(url))
        if not rows:
            break
        for row in rows:
            e = row["emote"]
            if e["animated"] or e["code"] in seen:
                continue
            seen.add(e["code"])
            emotes.append({"id": e["id"], "code": e["code"]})
            if len(emotes) == count:
                break
        before = rows[-1]["id"]
        time.sleep(0.3)
    return emotes


def global_static():
    rows = json.loads(get(f"{API}/cached/emotes/global"))
    return [{"id": e["id"], "code": e["code"]} for e in rows if not e["animated"] and not e["modifier"]]


def convert(ffmpeg, src, dst, size):
    vf = (
        f"format=rgba,scale={size}:{size}:force_original_aspect_ratio=decrease:flags=lanczos,"
        f"pad={size}:{size}:(ow-iw)/2:(oh-ih)/2:color=0x00000000"
    )
    subprocess.run(
        [ffmpeg, "-v", "error", "-y", "-i", str(src), "-vf", vf, "-frames:v", "1", str(dst)],
        check=True,
    )


def process(emote, ffmpeg, tmp, out):
    data = get(CDN.format(id=emote["id"]))
    if not data.startswith(PNG_MAGIC):
        raise ValueError(f"{emote['code']} ({emote['id']}): not a PNG")
    raw = tmp / f"{emote['id']}.png"
    raw.write_bytes(data)
    for folder, size in SIZES.items():
        convert(ffmpeg, raw, out / folder / f"{emote['id']}.png", size)


def main():
    root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--count", type=int, default=300, help="number of top shared static emotes (default 300)")
    ap.add_argument("--no-globals", action="store_true", help="skip BTTV's global static emotes")
    ap.add_argument("--ffmpeg", default=os.environ.get("FFMPEG") or shutil.which("ffmpeg"), help="path to ffmpeg")
    ap.add_argument("--out", type=Path, default=root / "bttv")
    ap.add_argument("--prune", action="store_true", help="delete PNGs in the output folders that are no longer in the list")
    args = ap.parse_args()
    if not args.ffmpeg:
        sys.exit("ffmpeg not found: pass --ffmpeg PATH or set FFMPEG")

    emotes = top_static(args.count)
    print(f"top shared static: {len(emotes)}")
    if not args.no_globals:
        codes = {e["code"] for e in emotes}
        extra = [g for g in global_static() if g["code"] not in codes]
        emotes += extra
        print(f"globals added: {len(extra)}")

    for folder in SIZES:
        (args.out / folder).mkdir(parents=True, exist_ok=True)

    failed = []
    with tempfile.TemporaryDirectory() as tmp, ThreadPoolExecutor(8) as pool:
        futures = {pool.submit(process, e, args.ffmpeg, Path(tmp), args.out): e for e in emotes}
        for i, (fut, e) in enumerate(futures.items(), 1):
            try:
                fut.result()
            except Exception as exc:
                failed.append(e)
                print(f"  failed {e['code']}: {exc}", file=sys.stderr)
            if i % 50 == 0:
                print(f"  {i}/{len(emotes)}")

    emotes = [e for e in emotes if e not in failed]
    entries = [
        {"asset": CDN.format(id=e["id"]), "id": e["id"], "names": [e["code"]], "strings": []}
        for e in emotes
    ]
    (args.out / "bttv_emotes.json").write_text(json.dumps(entries, indent=4) + "\n", encoding="utf-8")
    (args.out / "png_urls.txt").write_text("".join(x["asset"] + "\n" for x in entries), encoding="utf-8")

    if args.prune:
        keep = {f"{e['id']}.png" for e in emotes}
        for folder in SIZES:
            for f in (args.out / folder).glob("*.png"):
                if f.name not in keep:
                    f.unlink()

    print(f"done: {len(entries)} emotes, {len(failed)} failed -> {args.out}")


if __name__ == "__main__":
    main()

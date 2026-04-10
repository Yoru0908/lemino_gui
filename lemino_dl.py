#!/usr/bin/env python3
"""
Lemino DRM Video Downloader
Downloads and decrypts Widevine-protected DASH content from Lemino.

Usage:
    # First time: provide URL + token (token gets saved for reuse)
    python3 lemino_dl.py "https://lemino.docomo.ne.jp/contents/00mm8ihs0g" --token <x-service-token>

    # Subsequent times: just the URL (token auto-loaded)
    python3 lemino_dl.py "https://lemino.docomo.ne.jp/contents/00mm8ihs0g"

    # Direct mode (bypass API):
    python3 lemino_dl.py --mpd <MPD_URL> --la-url <LA_URL> --custom-data <DATA>
"""

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs

import requests
from pywidevine.cdm import Cdm
from pywidevine.device import Device, DeviceTypes
from pywidevine.pssh import PSSH

SCRIPT_DIR = Path(__file__).parent
CDM_DIR = SCRIPT_DIR / "cdm"
TOKEN_FILE = SCRIPT_DIR / ".token"

LEMINO_API_BASE = "https://if.lemino.docomo.ne.jp"
LEMINO_API_WATCH = f"{LEMINO_API_BASE}/v1/user/delivery/watch/ready"
LEMINO_API_SESSION = f"{LEMINO_API_BASE}/v1/session/init"
LEMINO_API_META_LIST = f"{LEMINO_API_BASE}/v1/meta/contents/list"
LEMINO_API_META_CONTENTS = f"{LEMINO_API_BASE}/v1/meta/contents"
HEADERS = {
    "Origin": "https://lemino.docomo.ne.jp",
    "Referer": "https://lemino.docomo.ne.jp/",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
}

NS = {
    "mpd": "urn:mpeg:dash:schema:mpd:2011",
    "cenc": "urn:mpeg:cenc:2013",
}


# ─── URL parsing ───────────────────────────────────────────

def parse_lemino_url(url: str) -> tuple:
    """Extract content ID and/or crid from Lemino URL.
    Supports:
        https://lemino.docomo.ne.jp/contents/00mm8ihs0g
        https://lemino.docomo.ne.jp/?crid=<base64_encoded_vod_crid>
        Just a raw content ID like '00mm8ihs0g'
    Returns (cid_or_None, crid_or_None).
    """
    # Raw content ID
    if re.match(r'^[a-z0-9]{10,}$', url):
        return url, None
    # URL with ?crid=base64 parameter (VOD crid encoded)
    from urllib.parse import unquote
    m = re.search(r'[?&]crid=([A-Za-z0-9+/=%]+)', url)
    if m:
        try:
            decoded = base64.b64decode(unquote(m.group(1))).decode()
            if decoded.startswith('crid://'):
                return None, decoded
        except Exception:
            pass
    # /contents/<cid> path
    m = re.search(r'/contents/([a-z0-9]+)', url)
    if m:
        return m.group(1), None
    m = re.search(r'cid=([a-z0-9]+)', url)
    if m:
        return m.group(1), None
    return url, None


def resolve_crid_to_cid(crid: str, token: str) -> str:
    """Look up CID from a VOD crid using meta/contents API."""
    from urllib.parse import quote
    resp = requests.get(
        f"{LEMINO_API_META_CONTENTS}?crid={quote(crid)}",
        headers={**HEADERS, "x-service-token": token,
                 "x-trace-id": str(uuid.uuid4())},
        timeout=10,
    )
    if resp.status_code == 200:
        data = resp.json()
        if data.get("result") == "0":
            for meta in data.get("meta_list", []):
                for co in meta.get("cid_obj", []):
                    if co.get("cid"):
                        print(f"    Title: {meta.get('title', '?')}")
                        return co["cid"]
    return None


# ─── Token management ──────────────────────────────────────

def save_token(token: str):
    """Persist token to file for reuse."""
    TOKEN_FILE.write_text(json.dumps({"x-service-token": token}))

def load_token() -> str:
    """Load saved token. Returns None if not found."""
    if TOKEN_FILE.exists():
        try:
            data = json.loads(TOKEN_FILE.read_text())
            return data.get("x-service-token")
        except (json.JSONDecodeError, KeyError):
            pass
    return None

def read_token_from_chrome() -> str:
    """Read x-service-token from Chrome's localStorage via AppleScript (macOS only)."""
    if sys.platform != "darwin":
        return None
    try:
        result = subprocess.run(
            ["osascript", "-e", """
tell application "Google Chrome"
    repeat with w in windows
        repeat with t in tabs of w
            if URL of t contains "lemino.docomo.ne.jp" then
                set tokenVal to execute t javascript "localStorage.getItem('X-Service-Token')"
                return tokenVal
            end if
        end repeat
    end repeat
end tell
"""],
            capture_output=True, text=True, timeout=5,
        )
        token = result.stdout.strip()
        if token and token != "missing value" and len(token) == 32:
            return token
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None

def refresh_token_from_response(resp: requests.Response, current_token: str) -> str:
    """Check API response headers for refreshed token."""
    new_token = resp.headers.get("X-Service-Token") or resp.headers.get("x-service-token")
    if new_token and new_token != current_token:
        print(f"    [token refreshed]")
        save_token(new_token)
        return new_token
    return current_token


def get_content_crid(cid: str, token: str, group_crid: str = None) -> tuple:
    """Get VOD crid and group crid for a content ID.
    Strategy: query meta/member API with group_crid to find CID -> VOD crid.
    If group_crid not provided, try to find it from meta/contents/list.
    Returns (vod_crid, group_crid) or (None, None).
    """
    api_headers = {**HEADERS, "x-service-token": token,
                   "x-trace-id": str(uuid.uuid4())}

    # If group_crid provided, look up directly
    if group_crid:
        vod = _find_vod_crid_in_group(group_crid, cid, api_headers)
        if vod:
            return vod, group_crid

    # Try meta/contents/list to find candidate group crids
    resp = requests.get(
        f"{LEMINO_API_META_LIST}?filter="
        + requests.utils.quote(json.dumps({
            "avail_status": [1], "search": [1],
            "target_age": ["G", "R-12", "R-15", "R-18"],
        })),
        headers=api_headers, timeout=10,
    )
    if resp.status_code == 200:
        data = resp.json()
        crids = data.get("crid_list", [])
        groups = [c for c in crids if "/group/" in c]
        # Try first 10 group crids
        for gc in groups[:10]:
            api_headers["x-trace-id"] = str(uuid.uuid4())
            vod = _find_vod_crid_in_group(gc, cid, api_headers)
            if vod:
                return vod, gc

    return None, None


def _find_vod_crid_in_group(group_crid: str, cid: str, headers: dict) -> str:
    """Search meta/member API for a VOD crid matching the given CID within a group.
    Uses brute-force JSON text search: find "cid":"<cid>" then look for nearby vod crid.
    """
    try:
        resp = requests.get(
            f"{LEMINO_API_BASE}/v1/meta/member?parent_filter="
            + requests.utils.quote(json.dumps({"crid": [group_crid]})),
            headers=headers, timeout=10,
        )
        if resp.status_code != 200 or cid not in resp.text:
            return None
        # Brute force: find vod crid near the CID in raw JSON
        text = resp.text
        idx = text.find(f'"cid":"{cid}"')
        if idx < 0:
            idx = text.find(cid)
        if idx < 0:
            return None
        # Search within 500 chars before/after for vod crid
        region = text[max(0, idx - 500):idx + 500]
        import re
        m = re.search(r'crid://plala\.iptvf\.jp/vod/[^"]+', region)
        if m:
            return m.group(0)
    except Exception:
        pass
    return None


def get_playback_info(cid: str, token: str, crid: str = None, group_crid: str = None) -> dict:
    """Call Lemino watch/ready API to get MPD URL, license URL, custom_data."""
    if not crid:
        print(f"    Looking up crid for {cid}...")
        found_crid, found_group = get_content_crid(cid, token, group_crid=group_crid)
        if found_crid:
            crid = found_crid
            if not group_crid and found_group:
                group_crid = found_group
            print(f"    Found crid: {crid}")
        else:
            print(f"    No crid found, using None (provide --crid manually)")

    payload = {
        "inflow_flows": [None, group_crid],
        "play_type": 1,
        "key_download_only": None,
        "avail_status": "1",
        "content_list": [
            {
                "kind": "main",
                "service_id": None,
                "cid": cid,
                "lid": "000000svod",
                "crid": crid,
                "preview": 0,
                "trailer": 0,
                "auto_play": 0,
                "stop_position": 0,
            }
        ],
        "groupcast": None,
        "quality": None,
        "terminal_type": 3,
        "test_account": 0,
    }
    resp = requests.post(
        LEMINO_API_WATCH,
        json=payload,
        headers={
            **HEADERS,
            "Content-Type": "application/json",
            "x-service-token": token,
            "x-trace-id": str(uuid.uuid4()),
        },
    )

    # Auto-refresh token from response
    refresh_token_from_response(resp, token)

    if resp.status_code != 200:
        print(f"[!] API error {resp.status_code}: {resp.text[:300]}")
        print(f"[!] Token may have expired. Re-run with --token <new_token>")
        print(f"    Get token: browser Console → localStorage.getItem('x-service-token')")
        # Clear stale token
        if TOKEN_FILE.exists():
            TOKEN_FILE.unlink()
        sys.exit(1)
    data = resp.json()

    if data.get("result") != "0":
        print(f"[!] API error: {data}")
        sys.exit(1)

    play = data["play_list"][0]
    return {
        "mpd_url": play["play_url"],
        "la_url": play["la_url"],
        "custom_data": play["custom_data"],
        "content_id": play["contentid"],
        "play_token": data["play_token"],
    }


def parse_mpd(mpd_url: str) -> dict:
    """Download and parse MPD manifest."""
    resp = requests.get(mpd_url, headers=HEADERS)
    resp.raise_for_status()
    root = ET.fromstring(resp.text)

    base_url = mpd_url.rsplit("/", 1)[0] + "/"
    duration_str = root.attrib.get("mediaPresentationDuration", "")

    result = {"base_url": base_url, "duration": duration_str, "video": [], "audio": [], "pssh": None}

    for period in root.findall("mpd:Period", NS):
        for adapt in period.findall("mpd:AdaptationSet", NS):
            mime = adapt.attrib.get("mimeType", "")
            is_video = "video" in mime
            is_audio = "audio" in mime

            # Extract PSSH
            for cp in adapt.findall("mpd:ContentProtection", NS):
                scheme = cp.attrib.get("schemeIdUri", "")
                if "edef8ba9" in scheme.lower():
                    pssh_el = cp.find("cenc:pssh", NS)
                    if pssh_el is not None and pssh_el.text:
                        result["pssh"] = pssh_el.text.strip()

            # Parse SegmentTemplate
            seg_tmpl = adapt.find("mpd:SegmentTemplate", NS)
            if seg_tmpl is None:
                continue

            timescale = int(seg_tmpl.attrib.get("timescale", "1"))
            media_tmpl = seg_tmpl.attrib.get("media", "")
            init_tmpl = seg_tmpl.attrib.get("initialization", "")

            # Build segment timeline
            segments = []
            timeline = seg_tmpl.find("mpd:SegmentTimeline", NS)
            if timeline is not None:
                t = 0
                for s in timeline.findall("mpd:S", NS):
                    if "t" in s.attrib:
                        t = int(s.attrib["t"])
                    d = int(s.attrib["d"])
                    r = int(s.attrib.get("r", "0"))
                    for _ in range(r + 1):
                        segments.append(t)
                        t += d

            for rep in adapt.findall("mpd:Representation", NS):
                bw = rep.attrib.get("bandwidth", "0")
                track = {
                    "bandwidth": int(bw),
                    "codecs": rep.attrib.get("codecs", ""),
                    "init_url": base_url + init_tmpl.replace("$Bandwidth$", bw),
                    "segment_urls": [
                        base_url + media_tmpl.replace("$Bandwidth$", bw).replace("$Time$", str(t))
                        for t in segments
                    ],
                }
                if is_video:
                    track["width"] = int(rep.attrib.get("width", "0"))
                    track["height"] = int(rep.attrib.get("height", "0"))
                    track["id"] = rep.attrib.get("id", "")
                    result["video"].append(track)
                elif is_audio:
                    track["id"] = rep.attrib.get("id", "")
                    result["audio"].append(track)

    return result


def get_widevine_keys(pssh_b64: str, la_url: str, custom_data: str) -> list:
    """Get Widevine content keys using pywidevine."""
    device = Device(
        type_=DeviceTypes.ANDROID,
        security_level=3,
        flags=None,
        client_id=open(CDM_DIR / "client_id.bin", "rb").read(),
        private_key=open(CDM_DIR / "private_key.pem", "rb").read(),
    )
    cdm = Cdm.from_device(device)
    session_id = cdm.open()

    pssh = PSSH(pssh_b64)
    challenge = cdm.get_license_challenge(session_id, pssh)

    resp = requests.post(
        la_url,
        data=challenge,
        headers={
            **HEADERS,
            "Content-Type": "application/octet-stream",
            "AcquireLicenseAssertion": custom_data,
        },
    )
    if resp.status_code != 200:
        print(f"[!] License request failed: {resp.status_code} {resp.text[:200]}")
        cdm.close(session_id)
        return []

    cdm.parse_license(session_id, resp.content)

    keys = []
    for key in cdm.get_keys(session_id):
        if key.type == "CONTENT":
            keys.append({"kid": key.kid.hex, "key": key.key.hex()})

    cdm.close(session_id)
    return keys


def download_segments(init_url: str, segment_urls: list, output_path: Path, label: str):
    """Download init + segments and concatenate into a single encrypted file."""
    print(f"[*] Downloading {label}: {len(segment_urls)} segments")

    with open(output_path, "wb") as out:
        # Download init segment
        resp = requests.get(init_url, headers=HEADERS, stream=True)
        resp.raise_for_status()
        for chunk in resp.iter_content(8192):
            out.write(chunk)

        # Download media segments
        for i, url in enumerate(segment_urls):
            if (i + 1) % 20 == 0 or i == len(segment_urls) - 1:
                print(f"    [{i+1}/{len(segment_urls)}]", end="\r")
            resp = requests.get(url, headers=HEADERS, stream=True)
            resp.raise_for_status()
            for chunk in resp.iter_content(8192):
                out.write(chunk)

    print(f"    Done: {output_path.stat().st_size / 1024 / 1024:.1f} MB")


def decrypt_file(input_path: Path, output_path: Path, keys: list):
    """Decrypt encrypted MP4 using mp4decrypt."""
    cmd = ["mp4decrypt"]
    for k in keys:
        cmd += ["--key", f"{k['kid']}:{k['key']}"]
    cmd += [str(input_path), str(output_path)]

    print(f"[*] Decrypting: {input_path.name}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[!] mp4decrypt error: {result.stderr}")
        sys.exit(1)
    print(f"    Done: {output_path.stat().st_size / 1024 / 1024:.1f} MB")


def mux_output(video_path: Path, audio_path: Path, output_path: Path):
    """Mux decrypted video and audio with ffmpeg."""
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-i", str(audio_path),
        "-c", "copy",
        "-movflags", "+faststart",
        str(output_path),
    ]
    print(f"[*] Muxing to: {output_path.name}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[!] ffmpeg error: {result.stderr[:500]}")
        sys.exit(1)
    print(f"    Done: {output_path.stat().st_size / 1024 / 1024:.1f} MB")


def select_track(tracks: list, target_height: int = None) -> dict:
    """Select best matching track by resolution."""
    if not tracks:
        return None
    if target_height:
        matches = [t for t in tracks if t.get("height", 0) == target_height]
        if matches:
            return matches[0]
    return max(tracks, key=lambda t: t["bandwidth"])


def main():
    parser = argparse.ArgumentParser(
        description="Lemino DRM Video Downloader",
        epilog="Examples:\n"
               "  %(prog)s 'https://lemino.docomo.ne.jp/contents/00mm8ihs0g' --token abc123\n"
               "  %(prog)s 'https://lemino.docomo.ne.jp/contents/00mm8ihs0g'  # reuses saved token\n"
               "  %(prog)s 00mm8ihs0g  # content ID directly\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("url", nargs="?", help="Lemino URL or content ID")
    parser.add_argument("--token", help="x-service-token (saved for reuse)")
    parser.add_argument("--quality", type=int, default=1080, help="Video height (1080/720/540/480/180)")
    parser.add_argument("--output", "-o", help="Output filename (default: <cid>.mp4)")
    parser.add_argument("--keep-temp", action="store_true", help="Keep temporary files")
    parser.add_argument("--mpd", help="Direct MPD URL (bypass API)")
    parser.add_argument("--la-url", help="Direct license URL")
    parser.add_argument("--custom-data", help="Direct custom_data token")
    parser.add_argument("--crid", help="Content CRID (auto-detected if omitted)")
    parser.add_argument("--group-crid", help="Series group CRID for inflow_flows")
    args = parser.parse_args()

    # Resolve token: CLI arg > saved file > Chrome localStorage
    token = args.token
    if token:
        save_token(token)
        print(f"    [token saved to {TOKEN_FILE.name}]")
    else:
        token = load_token()
        if not token:
            print(f"[*] No saved token, trying to read from Chrome...")
            token = read_token_from_chrome()
            if token:
                save_token(token)
                print(f"    [token from Chrome: {token[:8]}...]")

    # Step 1: Get playback info
    if args.mpd and args.la_url and args.custom_data:
        print(f"[1/5] Using direct MPD/license URLs...")
        cid = parse_lemino_url(args.url)[0] if args.url else "direct"
        info = {
            "mpd_url": args.mpd,
            "la_url": args.la_url,
            "custom_data": args.custom_data,
            "content_id": cid,
        }
    elif args.url:
        cid, url_crid = parse_lemino_url(args.url)
        if not token:
            print(f"[!] No token found. Options:")
            print(f"    1. Open Lemino in Chrome and login, then re-run")
            print(f"    2. Provide with --token <token>")
            print(f"    3. Console: localStorage.getItem('X-Service-Token')")
            sys.exit(1)
        # If URL contained a base64 crid, resolve CID from it
        crid = args.crid or url_crid
        if crid and not cid:
            print(f"[1/5] Resolving CID from CRID...")
            print(f"    CRID: {crid}")
            cid = resolve_crid_to_cid(crid, token)
            if not cid:
                print(f"[!] Could not resolve CID from CRID")
                sys.exit(1)
            print(f"    CID: {cid}")
        else:
            print(f"[1/5] Getting playback info for {cid}...")
        info = get_playback_info(cid, token, crid=crid, group_crid=args.group_crid)
    else:
        parser.error("Provide a Lemino URL/content ID, or use --mpd + --la-url + --custom-data")

    output_file = Path(args.output or f"{info.get('content_id', 'output')}.mp4")
    print(f"    MPD: {info['mpd_url'][:80]}...")
    print(f"    License: {info['la_url']}")

    # Step 2: Parse MPD
    print(f"[2/5] Parsing MPD manifest...")
    mpd = parse_mpd(info["mpd_url"])
    print(f"    Video tracks: {len(mpd['video'])} | Audio tracks: {len(mpd['audio'])}")
    for v in mpd["video"]:
        print(f"      {v['width']}x{v['height']} {v['codecs']} {v['bandwidth']//1000}kbps ({len(v['segment_urls'])} segs)")
    for a in mpd["audio"]:
        print(f"      audio {a['codecs']} {a['bandwidth']//1000}kbps ({len(a['segment_urls'])} segs)")

    if not mpd["pssh"]:
        print("[!] No PSSH found in MPD")
        sys.exit(1)

    # Step 3: Get Widevine keys
    print(f"[3/5] Getting Widevine decryption keys...")
    keys = get_widevine_keys(mpd["pssh"], info["la_url"], info["custom_data"])
    if not keys:
        print("[!] Failed to get keys")
        sys.exit(1)
    for k in keys:
        print(f"    KID:KEY = {k['kid']}:{k['key']}")

    # Step 4: Download & decrypt
    print(f"[4/5] Downloading encrypted segments...")
    video_track = select_track(mpd["video"], args.quality)
    audio_track = select_track(mpd["audio"])

    if not video_track or not audio_track:
        print("[!] Could not find video/audio tracks")
        sys.exit(1)

    print(f"    Selected: {video_track.get('width','?')}x{video_track.get('height','?')} + {audio_track['codecs']}")

    tmp_dir = Path(tempfile.mkdtemp(prefix="lemino_"))
    enc_video = tmp_dir / "enc_video.mp4"
    enc_audio = tmp_dir / "enc_audio.mp4"
    dec_video = tmp_dir / "dec_video.mp4"
    dec_audio = tmp_dir / "dec_audio.mp4"

    try:
        download_segments(video_track["init_url"], video_track["segment_urls"], enc_video, "video")
        download_segments(audio_track["init_url"], audio_track["segment_urls"], enc_audio, "audio")

        # Decrypt
        decrypt_file(enc_video, dec_video, keys)
        decrypt_file(enc_audio, dec_audio, keys)

        # Step 5: Mux
        print(f"[5/5] Muxing final output...")
        mux_output(dec_video, dec_audio, output_file)

        print(f"\n=== DONE: {output_file} ({output_file.stat().st_size / 1024 / 1024:.1f} MB) ===")

    finally:
        if not args.keep_temp:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        else:
            print(f"[*] Temp files kept at: {tmp_dir}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Step-by-step test for Lemino DRM download pipeline."""
import sys, os, tempfile, shutil, subprocess
from pathlib import Path

# Step 1: Test MPD parsing
print("=== STEP 1: Parse MPD ===")
from lemino_dl import parse_mpd, HEADERS
MPD_URL = "https://vod-cdn0.lemino.docomo.ne.jp/video/00mm/8i/00mm8ihs0g/20260401182159/manifest.mpd?resolution=1080&fixed=0"
mpd = parse_mpd(MPD_URL)
print(f"  Video tracks: {len(mpd['video'])}")
for v in mpd["video"]:
    print(f"    {v['width']}x{v['height']} {v['codecs']} ({len(v['segment_urls'])} segs)")
print(f"  Audio tracks: {len(mpd['audio'])}")
print(f"  PSSH found: {bool(mpd['pssh'])}")
if not mpd["pssh"]:
    print("FAIL: no PSSH"); sys.exit(1)

# Step 2: Get Widevine keys
print("\n=== STEP 2: Get Widevine keys ===")
from lemino_dl import get_widevine_keys
LA_URL = "https://drm.lemino.docomo.ne.jp/widevine_license"
CUSTOM_DATA = "ORmpaZCnbEWlc0TmmEadRqJktCPkEij2pVdNRILhGKqkGTRNz8TIZFbi/zRj1jXrUkM5MZlCL0wNh0mkwMBMkjPxtgERAJIUeBf75gb67IjpWMiRomL70YvwkzEDPRiQKt+8gL716UbDuh61/9xoDRrBqUsOMCqnlg58szEt9dMfp4ximoGi5Lx4US8HbQ/C4Y5WClqOzq1pzb1krCmfEORMu3w73GCSEmNloen4xqK+/2pYwwuwqHmzPuj6Cm3l/bPLS03bMiNg2x/xE3SYCRfDYiXxk1oBoSJBL8logKUte2uncZJfbTKEi9I9ylwyrgKXckzWasVRLKN/K7bds2mfLAn452un70NCY6ujH8eANLHrhvyQxPK42MFGKuWfoTjrpnC95YqsrFUxD3tUdYPNk49PzqSAB6KDJ+GD6pxNBcGv/aWPYd4uyxEZLZsJNqCOrlp8wKmH//olaj7O6z3fs6MpnjAn7HYs4aW2Xt5qbe5vgWuTHAeI2tdTqLYLSjedU33gpiXhb8ZtOKae/DfLdEr85X8nu6eMgKw3GrXVlkNYdRzp7xDBp2gxT/EyHPglnaYN0160A/4aHHcg01hiKFWrHt/bxVLXwTRsi0Loom1ha8ZfNgUALnLJMHOFadchM1XiGDMKagXWLAVCM5+aQaVfmfQuBJdOOIDHPH0ANMi6MuDMN4dm/EfE5QIpOyoN1DwlNrFcJxchqCh2ic4/qjdPaXQkEISgrLfQx6CFBLADQuZtChY9MUuIcX5lp4OmnzUvw7+8y1zkfxzfzwfVyjjXdsYnOozs7EyNMdOboawKjU7JgstitIL6rOAIZ51tBRAJRgzXA6/kmw5Wb+9M5aq1Tw1wM266eFLRbXzwO7PTb7q2VnR/XOlKO5ZkHWaOnUh6Ke/XVt0xoiWAXbL7+mv5ieGNRBrJsd0HAaYcjGlsj+246XvRjz0/6s/jtsZWdCYlfg3Z+F9rRt358uCAcWBHg+gtY4md9gsyRc8OJJVZk5Rpj13iaJKDA3NrQtX+CcatO9AUWH4qdA3H7uI6TaC0OgamO50UyXmplMHnCOU3tvLj56Ayl3K6AJ9YrKIAkoUjn0XpehMuSGBV9bPImF5baIsqSTo43uoncVUTDJsO6YELOhXVmmprC8rTuKBCxIpbKvTx4j+gcclM1uyuiSzpJivdX+3nMbSN40euVIN1RznNWB73tTF4TH83+fVR6RVFTkqdXFo0iDoq1Q=="
keys = get_widevine_keys(mpd["pssh"], LA_URL, CUSTOM_DATA)
if not keys:
    print("FAIL: no keys"); sys.exit(1)
for k in keys:
    print(f"  KEY: {k['kid']}:{k['key']}")

# Step 3: Download only 3 video segments + all audio (quick test)
print("\n=== STEP 3: Download test segments (480p, first 3 segs) ===")
import requests
video = min(mpd["video"], key=lambda t: abs(t.get("height", 0) - 480))
audio = mpd["audio"][0]
print(f"  Video: {video['width']}x{video['height']}")

tmp = Path(tempfile.mkdtemp(prefix="lemino_test_"))
enc_v = tmp / "enc_v.mp4"

# Download init + 3 segments only
with open(enc_v, "wb") as f:
    print("  Downloading video init...")
    r = requests.get(video["init_url"], headers=HEADERS)
    r.raise_for_status()
    f.write(r.content)
    for i in range(min(3, len(video["segment_urls"]))):
        print(f"  Downloading video seg {i+1}/3...")
        r = requests.get(video["segment_urls"][i], headers=HEADERS)
        r.raise_for_status()
        f.write(r.content)
print(f"  enc_v: {enc_v.stat().st_size / 1024:.0f} KB")

enc_a = tmp / "enc_a.mp4"
with open(enc_a, "wb") as f:
    print("  Downloading audio init...")
    r = requests.get(audio["init_url"], headers=HEADERS)
    r.raise_for_status()
    f.write(r.content)
    for i in range(min(3, len(audio["segment_urls"]))):
        print(f"  Downloading audio seg {i+1}/3...")
        r = requests.get(audio["segment_urls"][i], headers=HEADERS)
        r.raise_for_status()
        f.write(r.content)
print(f"  enc_a: {enc_a.stat().st_size / 1024:.0f} KB")

# Step 4: Decrypt
print("\n=== STEP 4: Decrypt ===")
dec_v = tmp / "dec_v.mp4"
dec_a = tmp / "dec_a.mp4"
for src, dst, label in [(enc_v, dec_v, "video"), (enc_a, dec_a, "audio")]:
    cmd = ["mp4decrypt"]
    for k in keys:
        cmd += ["--key", f"{k['kid']}:{k['key']}"]
    cmd += [str(src), str(dst)]
    print(f"  Decrypting {label}...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  FAIL: {result.stderr}"); sys.exit(1)
    print(f"  {dst.name}: {dst.stat().st_size / 1024:.0f} KB")

# Step 5: Mux
print("\n=== STEP 5: Mux with ffmpeg ===")
out = Path("/tmp/lemino_quick_test.mp4")
cmd = ["ffmpeg", "-y", "-i", str(dec_v), "-i", str(dec_a), "-c", "copy", "-movflags", "+faststart", str(out)]
result = subprocess.run(cmd, capture_output=True, text=True)
if result.returncode != 0:
    print(f"  FAIL: {result.stderr[:300]}"); sys.exit(1)
print(f"  Output: {out} ({out.stat().st_size / 1024:.0f} KB)")

# Cleanup
shutil.rmtree(tmp, ignore_errors=True)
print(f"\n=== ALL STEPS PASSED === Output: {out}")

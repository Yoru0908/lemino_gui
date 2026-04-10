"""Test Widevine key acquisition for Lemino"""
import base64
import requests
from pywidevine.cdm import Cdm
from pywidevine.device import Device, DeviceTypes
from pywidevine.pssh import PSSH

# CDM files
CDM_DIR = "./cdm"
CLIENT_ID = f"{CDM_DIR}/client_id.bin"
PRIVATE_KEY = f"{CDM_DIR}/private_key.pem"

# From Lemino API response
LICENSE_URL = "https://drm.lemino.docomo.ne.jp/widevine_license"
CUSTOM_DATA = "ORmpaZCnbEWlc0TmmEadRo3f4YeUKyFFuTEcYy0xguOcSqRO/zGxuLB3VhC5qCDZUenNbkpTO3dN0DyeYco9aq2vMmZwmRVp+4gFP69ZM0oNK4L2iN/Q+FZUhrsJYCuxp0Rptr4Ny6WeMkfAEWfSAa9F1TjksKcF2AzlV2dXcL4kxXBqtq5ZIu3s+BmxZb5I41w7K43jY17AJCMQXtO0D2Em5GcRMH8sg3CWpulbsGTG9fbJHi4c6jxxYoy93lElDY5LTDmQmAwQ3e3Fy0jpfdw1+994n0DDzs8rwI1h483613n0anW32HDuE/i37TCCH97HSw1Z8TNSifz2fHEhAmTqUwZpH7cXSy8VGOalBoL52kuxPq6z6m3xyoX35itawm8VHAlQtKSD0KYooSR1UGJIS/Jyrn++cHa4Jb9araMQiFS1B0pawvlgpqOVwXiB/C/omD9o15whGZ0Z/Sd1bE8ftev0kNpfrpVt4RSwqntmEmKM53lWU3bvip7rUpH2nopFP8JszL2/+HPSu2N92KwNBhVqHWqErfqaHQTaxib4cOW2PNYqkJNCePELByxH2sjG4Bbw05K4oI5xu81HsqgaNpiRDSBBghUfWq8jOqjMd4bTRiybWmjulFjblT9P5fq2Rt+oNGXxGCbvgtRw261FXDdqjqLVlC38ZDNnms1po9YAkNd90Q932IvTvSLLhfrfeBDN94v8yeDvXe65kF4O2v93rRFyN1cq1PWpvJP3ii3rjEzPXVTnicCl+N4uODF/SogPqXVjuLSvNrDbZR8GNwYeKt5hrzTfhrclfHiQDel1J1bMUJ744wF4Kn1cqKiMPxVLhHiy4CfKfYblo+QiDUySF0dmzLkW8AJt6Pc15DF4iOxmQ9xq9moFZhftOATfM7HtBK5Oj2cKAblmWfR6pb4lGas9GfO5zwDtzx+yyyJc70wIl7vWb/TkY6kquZYAJBrb26GwacXP5AqNGUcOliI/9efh6zat6AW34BAGifIlQz7dnvg0mHdWdnRXKprC9u/aoJmGb1U0sodVJk0EPVJtpNNJmBNAtCBVNmXvOka5HhnhxTAkkWpO2U2vqwgDNdamMFD0YkFx0fLPZs1pLnE0/YY02qI+Nu9YfmChrztZg3h9tWSrxVkownLO1euT8vBCEgAFGGfGRAr17bq2cu0MTY37zNp7Eus+jnzUjtGMGfmC388u9iQWoPN4TA9kscwnZu18f8psKTFIVw=="

# PSSH from MPD manifest
PSSH_B64 = "AAAAWXBzc2gAAAAA7e+LqXnWSs6jyCfc1R0h7QAAADkSEBKaflDvnk86vFiiCox19zEaDXdpZGV2aW5lX3Rlc3QiEH/TR/38LUc3ofHaxZIPWj1I49yVmwY="


def get_keys():
    # Load device
    device = Device(
        type_=DeviceTypes.ANDROID,
        security_level=3,
        flags=None,
        client_id=open(CLIENT_ID, "rb").read(),
        private_key=open(PRIVATE_KEY, "rb").read(),
    )
    cdm = Cdm.from_device(device)
    session_id = cdm.open()

    # Parse PSSH
    pssh = PSSH(PSSH_B64)

    # Generate challenge
    challenge = cdm.get_license_challenge(session_id, pssh)

    # Send challenge to license server
    # Error "no assertion in payload" means DRM Today format: JSON with assertion field
    challenge_b64 = base64.b64encode(challenge).decode()
    common_headers = {
        "Origin": "https://lemino.docomo.ne.jp",
        "Referer": "https://lemino.docomo.ne.jp/",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
    }

    # Header name from DASH player JS: customDataHeaderKey || "AcquireLicenseAssertion"
    print(f"[*] Sending license challenge to {LICENSE_URL}")
    resp = requests.post(
        LICENSE_URL,
        data=challenge,
        headers={
            **common_headers,
            "Content-Type": "application/octet-stream",
            "AcquireLicenseAssertion": CUSTOM_DATA,
        },
    )
    print(f"[*] License response: {resp.status_code} ({len(resp.content)} bytes)")

    if resp.status_code != 200:
        print(f"[!] Error: {resp.text[:200]}")
        return

    # Parse license
    cdm.parse_license(session_id, resp.content)

    # Get keys
    keys = []
    for key in cdm.get_keys(session_id):
        if key.type == "CONTENT":
            kid_hex = key.kid.hex
            key_hex = key.key.hex()
            print(f"[+] KEY: {kid_hex}:{key_hex}")
            keys.append(f"{kid_hex}:{key_hex}")

    cdm.close(session_id)
    return keys


if __name__ == "__main__":
    keys = get_keys()
    if keys:
        print(f"\n=== SUCCESS: Got {len(keys)} content key(s) ===")
        for k in keys:
            print(f"  {k}")
    else:
        print("\n=== FAILED: No keys obtained ===")

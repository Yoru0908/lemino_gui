#!/usr/bin/env python3
"""找到系列的第一集 CRID，通过 group children API 或 next_content 链式追踪"""
import sys, json, uuid, time, requests
from urllib.parse import quote
sys.path.insert(0, __import__('pathlib').Path(__file__).parent.__str__())
from lemino_watcher import LEMINO_API_BASE, HEADERS, load_token, query_meta

token = load_token()
headers = {**HEADERS, "x-service-token": token, "x-trace-id": str(uuid.uuid4())}

# === 方法1: 尝试各种 group API ===
group_crids = [
    "crid://plala.iptvf.jp/group/b101acb",   # シリーズ parent
    "crid://plala.iptvf.jp/group/b1049d0",   # 2026年4月-6月 season
]
endpoints = [
    "/v1/meta/group/children",
    "/v1/meta/children",
    "/v1/group/contents",
    "/v1/contents/group",
    "/v1/meta/series",
]

print("=== 探测 group API ===")
for gc in group_crids:
    for ep in endpoints:
        url = f"{LEMINO_API_BASE}{ep}?crid={quote(gc)}&offset=0&limit=5&sort=old"
        try:
            r = requests.get(url, headers=headers, timeout=10)
            d = r.json()
            status = f"code={r.status_code} result={d.get('result')}"
            total = d.get('total_cnt', '?')
            items = d.get('meta_list') or d.get('content_list') or d.get('children') or []
            if items:
                print(f"  HIT! {ep} group={gc.split('/')[-1]} total={total} items={len(items)}")
                for m in items[:3]:
                    t = m.get('title', m.get('crid', '?'))
                    print(f"    -> {t}")
            else:
                print(f"  miss {ep} group={gc.split('/')[-1]} {status}")
        except Exception as e:
            print(f"  err {ep}: {e}")
        time.sleep(0.2)

# === 方法2: 从已知集 chain forward 找最早的 ===
print("\n=== chain forward from known early ep ===")
# 现有3集是 230-232，试从 230 对应 CRID 往前找
# 但我们没有 230 的 CRID。先检查 280 的 meta 看全部 keys
meta280 = query_meta("crid://plala.iptvf.jp/vod/0000000000_00mm8i4x3s", token)
if meta280:
    print(f"ep280 keys: {sorted(meta280.keys())}")
    # Check if there's episode_number or similar
    for k in ['episode_number', 'ep_number', 'episode_no', 'content_no', 'sort_key', 'episode_order']:
        if k in meta280:
            print(f"  {k}: {meta280[k]}")
    # Check series_parent children
    sp = meta280.get('series_parent', [])
    print(f"  series_parent: {sp}")
    mo = meta280.get('member_of', [])
    print(f"  member_of: {mo}")

# === 方法3: 猜测 CRID 模式 ===
# 280 = 00mm8i4x3s, 试试规律
print("\n=== CRID pattern guess ===")
crid280 = "crid://plala.iptvf.jp/vod/0000000000_00mm8i4x3s"
# 试查 #1 附近的 CRID (2019年)
# dTV/Lemino 通常用递增的 ID。让我们获取已知的3集对应的文件名来反推
print("已知: ep280 -> 00mm8i4x3s")
print("需要: 查 Alist 上现有文件的 ep 号")

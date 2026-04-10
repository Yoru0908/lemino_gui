# Lemino GUI Downloader

Lemino (docomo) 的 Widevine DRM 保护视频下载工具 — **GUI 版本**。

浏览器界面操作：输入账号密码自动登录，粘贴 URL，点击下载。

## 快速开始

### 一键安装依赖 (macOS)

```bash
bash setup.sh
```

### 启动 GUI

```bash
streamlit run lemino_gui.py
```

浏览器会自动打开 `http://localhost:8501`。

### 手动安装依赖

```bash
# 系统工具
brew install ffmpeg bento4

# Python 包
pip3 install pywidevine requests streamlit playwright
playwright install chromium
```

### 使用流程

1. 在「账号登录」区域输入 **d-account 邮箱和密码**，点击登录
2. 登录成功后 Token 自动保存，下次无需再登录
3. 在 [Lemino 网站](https://lemino.docomo.ne.jp) 找到视频，**复制浏览器地址栏 URL**
4. 粘贴到「Lemino 视频 URL」输入框，选择画质，点击开始下载

---

## CLI 方式 (高级用法)

```bash
# 方式 1: 直接粘贴 URL (推荐 - 零配置)
python3 lemino_dl.py "https://lemino.docomo.ne.jp/?crid=Y3JpZDovL3BsYWxhL..." --quality 1080

# 方式 2: 用 CID + group-crid
python3 lemino_dl.py 00mm8ihs0g \
    --group-crid "crid://plala.iptvf.jp/group/b1049d1" \
    --quality 1080

# 方式 3: 直接模式 (从 DevTools 抓 watch/ready 响应)
python3 lemino_dl.py \
    --mpd "<play_url>" \
    --la-url "https://drm.lemino.docomo.ne.jp/widevine_license" \
    --custom-data "<custom_data>" \
    -o output.mp4
```

### 参数说明

| 参数 | 说明 |
|------|------|
| `url` | Lemino URL (支持 `?crid=base64` 和 `/contents/<CID>` 两种格式) |
| `--group-crid` | 系列 group CRID (使用 `?crid=` URL 时不需要) |
| `--crid` | 直接指定 VOD CRID (跳过自动查找) |
| `--token` | x-service-token (首次使用后自动保存) |
| `--quality` | 视频高度: 1080/720/540/480/180 (默认 1080) |
| `-o` | 输出文件名 (默认 `<CID>.mp4`) |
| `--keep-temp` | 保留临时文件 |

### 如何获取 URL

最简单的方式：在 Lemino 网页上找到要下载的视频，复制浏览器地址栏的 URL 即可。

URL 中 `?crid=` 参数包含 base64 编码的 VOD CRID，脚本会自动解码并查找 CID。

### 如何获取 Token

Token 首次需要，之后自动保存：
1. Chrome 打开 https://lemino.docomo.ne.jp 并登录
2. F12 → Console → `localStorage.getItem('X-Service-Token')`
3. 用 `--token <value>` 传入，或脚本自动从 Chrome 读取 (macOS)
4. 或使用 `python3 auto_login.py` 自动登录获取

### 实际示例

```bash
# 直接粘贴 URL 下载 そこ曲がったら、櫻坂？
python3 lemino_dl.py "https://lemino.docomo.ne.jp/?crid=Y3JpZDovL3BsYWxhLmlwdHZmLmpwL3ZvZC8wMDAwMDAwMDAwXzAwbWltaWh1bHY%3D" \
    --quality 1080 -o sokosaku.mp4

# 用 CID 下载 ちょこさく
python3 lemino_dl.py 00mm8ihs0g \
    --group-crid "crid://plala.iptvf.jp/group/b1049d1" \
    --quality 1080 -o chokosaku.mp4
```

## Token 管理

- Token 首次通过 `--token` 或 Chrome 自动读取后保存到 `.token` 文件
- API 响应会自动刷新 token (通过 `X-Service-Token` 响应头)
- Token 过期后需重新从 Chrome 获取
- 自动登录: `python3 auto_login.py` (Playwright OAuth 自动化)

## 内容结构 (API 逆向发现 2026-04-08)

Lemino 的内容按三层结构组织：

```
WIZARD (系列聚合页, 有封面图, 不含实际集数)
  └─ SERIES (季度分组, 3个月一换, prev/next 链表连接)
       └─ PIT (单集 VOD, prev/next 链表连接, 跨季度无缝衔接)
```

### 已知节目结构

**そこ曲がったら、櫻坂？**

| 层级 | CRID | 说明 |
|------|------|------|
| WIZARD | `b101acb` | シリーズ）そこ曲がったら、櫻坂？ |
| SERIES | `b10312d` | 2025年4月-6月 ← 最早季度 (API可见) |
| SERIES | `b103959` | 2025年7月-9月 |
| SERIES | `b103dec` | 2025年10月-12月 |
| SERIES | `b10442c` | 2026年1月-3月 |
| SERIES | `b1049d0` | 2026年4月-6月 ← 当前 |

**ちょこさく**

| 層級 | CRID | 说明 |
|------|------|------|
| WIZARD | `b101b65` | シリーズ）ちょこさく |
| SERIES | `b10312e` | 2025年4月-6月 ← 最早季度 (API可见) |
| SERIES | `b10395a` | 2025年7月-9月 |
| SERIES | `b103ded` | 2025年10月-12月 |
| SERIES | `b10442d` | 2026年1月-3月 |
| SERIES | `b1049d1` | 2026年4月-6月 ← 当前 |

### 关键发现

- **季度切换**: 每3个月新建一个 SERIES group，`next_content` 指向新季度
- **VOD 链表**: 单集之间通过 `previous_content`/`next_content` 链表连接，**跨季度无缝**
- **API 最早集**: 两节目均从 **#230** (2025/4/14配信) 开始可见，更早的集 `meta_list` 返回空 (已下架)
- **WIZARD 封面图**: `/cms/<wizard_id>/<wizard_id>_h1.jpg` (竖图) / `_w1.jpg` (横图)
- **meta/member API**: 对这两个节目返回 `member_meta_list: []`，但 response 原文含 VOD CRID (需 regex 提取)

### Watcher 策略 (自动追新)

不依赖季度 group，直接追踪 VOD 链表尾部：

```python
# 每次检查: 查询最后已知集的 next_content
state = {
    "そこ曲がったら": "crid://plala.iptvf.jp/vod/0000000000_00mm8i4x3s",  # #280 (26/4/6)
    "ちょこさく":     "crid://plala.iptvf.jp/vod/0000000000_00mm8ihrno",  # #280 (26/4/6)
}
# GET /v1/meta/contents?crid=<last_vod_crid>
# → if next_content != None → 新集发现 → 下载
# 只需 1 次 API 调用/节目/次检查
```

**优势**: 季度切换自动无缝，无需更新 group CRID。

### 运行环境

| 组件 | 运行位置 | 说明 |
|------|---------|------|
| `lemino_dl.py` | Homeserver | 下载+解密，仅需 Python + mp4decrypt + ffmpeg |
| `token_refresh.sh` | M1 Mac (cron) | 从 Chrome 读取 token → SSH 推送到 Homeserver |
| `auto_login.py` | M1 Mac (备用) | Playwright 自动登录，token 完全过期时使用 |

## CRID 查找机制

Lemino 的 `watch/ready` API 要求精确的 VOD CRID，格式为 `crid://plala.iptvf.jp/vod/0000000000_<vod_id>`。

**查找链:**
1. 提供 `--group-crid` (系列的 group CRID)
2. 脚本调用 `meta/member` API 获取该系列的所有集
3. 在响应中搜索匹配 CID 的 VOD CRID
4. 自动填入 `watch/ready` payload

如果不提供 `--group-crid`，脚本会尝试从 `meta/contents/list` 自动搜索（较慢，可能失败）。

## DRM 链路分析

### 播放流程

```
POST /v1/user/delivery/watch/ready (x-service-token + crid)
  → MPD URL + license URL + custom_data
  → 下载 MPD manifest → 提取 PSSH
  → pywidevine 生成 challenge
  → POST challenge → widevine_license (Header: AcquireLicenseAssertion)
  → content key
  → 下载加密 m4s → mp4decrypt 解密 → ffmpeg 合并
```

### 关键端点

| 端点 | 说明 |
|------|------|
| `POST /v1/user/delivery/watch/ready` | 获取播放信息 |
| `GET /v1/meta/member?parent_filter=...` | 查找系列成员的 VOD CRID |
| `POST drm.lemino.docomo.ne.jp/widevine_license` | Widevine license |
| `GET vod-cdn0.lemino.docomo.ne.jp/video/...` | CDN 视频/音频段 |

### watch/ready API Payload

```json
{
    "inflow_flows": [null, "crid://plala.iptvf.jp/group/<group_id>"],
    "play_type": 1,
    "key_download_only": null,
    "avail_status": "1",
    "content_list": [{
        "kind": "main",
        "service_id": null,
        "cid": "<content_id>",
        "lid": "000000svod",
        "crid": "crid://plala.iptvf.jp/vod/0000000000_<vod_id>",
        "preview": 0,
        "trailer": 0,
        "auto_play": 0,
        "stop_position": 0
    }],
    "groupcast": null,
    "quality": null,
    "terminal_type": 3,
    "test_account": 0
}
```

### DRM 参数

| 项 | 值 |
|----|-----|
| **DRM** | Widevine + PlayReady (CENC) |
| **License Header** | `AcquireLicenseAssertion: <custom_data>` |
| **CDN** | CloudFront → `vod-cdn0.lemino.docomo.ne.jp` |
| **API 鉴权** | `x-service-token` header |
| **视频** | H.264 最高 1080p, AAC-LC Stereo |

### custom_data 生命周期

- 每次 `watch/ready` 生成新的 `custom_data`
- 有效期约 172 秒
- 过期后 license 请求被拒绝

## 文件结构

```
lemino/
├── README.md                       # 本文档
├── lemino_dl.py                    # 主下载脚本 (含 --play-only 模式)
├── lemino_watcher.py               # 自動追跡器 (PM2 長驻, 新集検出→DL→Alist→QQ推送)
├── watcher_config.example.json     # watcher 配置模板
├── watcher_config.json             # watcher 配置 (git ignored)
├── watcher_state.json              # watcher 状態 (自動生成, 各番組の最終VOD CRID)
├── auto_login.py                   # Playwright 自動登録 (d-account OAuth)
├── token_refresh.sh                # M1 cron: Chrome → Homeserver token 同期
├── step_test.py                    # 分步テスト脚本
├── .token                          # x-service-token (自動生成)
├── .credentials                    # 登録凭据 (自動生成)
├── logs/                           # watcher ログ
└── cdm/
    ├── client_id.bin               # Widevine L3 CDM
    └── private_key.pem             # Widevine L3 Private Key
```

### CDM 文件

Widevine L3 CDM 通过 Android 模拟器 + KeyDive 提取:
- 提取源: Android 13 (API 33) emulator, `google_apis;arm64-v8a`
- L3 CDM 可能被 Google 吊销，需要时重新提取
- 提取工具: [KeyDive](https://github.com/hyugogirubato/KeyDive) + Frida

---

*创建: 2026-04-07 | 更新: 2026-04-08 (API结构逆向, watcher策略)*

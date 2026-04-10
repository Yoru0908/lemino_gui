#!/bin/bash
# Lemino GUI - 环境初始化脚本 (macOS)
set -e

echo "=== Lemino GUI 环境初始化 ==="
echo ""

# 检查 Homebrew
if ! command -v brew &>/dev/null; then
    echo "[!] 未检测到 Homebrew，请先安装: https://brew.apple.com"
    exit 1
fi

# 系统工具
echo "[1/3] 安装系统工具 (ffmpeg, bento4)..."
brew install ffmpeg bento4

# Python 依赖
echo "[2/3] 安装 Python 依赖..."
pip3 install pywidevine requests streamlit playwright

# Playwright 浏览器
echo "[3/3] 安装 Playwright Chromium..."
playwright install chromium

echo ""
echo "=== 完成！启动方式 ==="
echo ""
echo "  streamlit run lemino_gui.py"
echo ""

#!/bin/bash
# 发布资讯到 GitHub Pages
# 前提：已在此目录执行过 git init + git remote add origin <你的仓库地址>

set -e
cd "$(dirname "$0")"

echo "📦 准备提交..."
git add news_cache.json docs/ index.html

# 如果没有任何改动就退出
if git diff --cached --quiet; then
  echo "✅ 没有新内容，无需发布"
  exit 0
fi

TIMESTAMP=$(date '+%Y-%m-%d %H:%M')
git commit -m "资讯更新：$TIMESTAMP"

echo "🚀 推送到 GitHub..."
git push origin main

echo "✅ 发布成功！GitHub Pages 通常 1-2 分钟后生效"

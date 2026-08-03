#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
机器人行业资讯自动采集脚本 v1.1（纯标准库版）
Robot Industry News Auto-Collector — zero external dependencies

用法：python3 scraper.py
输出：
  docs/YYYY-MM-DD.md   每日资讯文档（追加写入）
  news_cache.json      最近 150 条资讯，供界面展示
  cache.json           已采集 URL 哈希集合，用于去重
"""

import json
import hashlib
import sys
import socket
import ssl
import re
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
from urllib.parse import urlparse
import xml.etree.ElementTree as ET
from html.parser import HTMLParser


# ── 路径配置 ──────────────────────────────────────────────────
BASE_DIR        = Path(__file__).parent
CACHE_FILE      = BASE_DIR / 'cache.json'
NEWS_CACHE_FILE = BASE_DIR / 'news_cache.json'
DOCS_DIR        = BASE_DIR / 'docs'
DOCS_DIR.mkdir(exist_ok=True)

MAX_PER_SOURCE  = 20
NEWS_CACHE_SIZE = 150
FETCH_TIMEOUT   = 15          # 秒
USER_AGENT      = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                   'AppleWebKit/537.36 (KHTML, like Gecko) '
                   'Chrome/126.0 Safari/537.36')


# ── 数据源 ────────────────────────────────────────────────────
RSS_SOURCES = [
    # ── 英文 ──
    {
        'name': 'The Robot Report',
        'url':  'https://www.therobotreport.com/feed/',
        'lang': 'en',
    },
    {
        'name': 'TechCrunch · Robotics',
        'url':  'https://techcrunch.com/tag/robotics/feed/',
        'lang': 'en',
    },
    {
        'name': 'IEEE Spectrum · Automaton',
        'url':  'https://spectrum.ieee.org/feeds/blog/automaton.rss',
        'lang': 'en',
    },
    {
        'name': 'Robotics Business Review',
        'url':  'https://www.roboticsbusinessreview.com/feed/',
        'lang': 'en',
    },
    # ── 中文 ──
    {
        'name': 'OFweek 机器人网',
        'url':  'https://robot.ofweek.com/rss.xml',
        'lang': 'zh',
    },
    {
        'name': '机器人圈',
        'url':  'https://www.jiqirenquan.com/feed',
        'lang': 'zh',
    },
]


# ── 分类规则 ──────────────────────────────────────────────────
RULES = [
    (
        '新产品发布',
        {
            'zh': ['发布', '推出', '上市', '亮相', '首发', '发布会', '新品',
                   '量产', '开售', '揭晓', '上线', '首款', '全新', '新型'],
            'en': ['launch', 'release', 'debut', 'unveil', 'introduce',
                   'new robot', 'announced', 'reveal', 'ships', 'new model',
                   'new product', 'first look', 'just announced'],
        }
    ),
    (
        '新功能发布',
        {
            'zh': ['功能', '升级', '更新', '版本', '迭代', '改进', '优化',
                   '新增', '提升', '增强', '能力', '软件'],
            'en': ['feature', 'update', 'upgrade', 'capability', 'version',
                   'improvement', 'enhanced', 'next-gen', 'new ability',
                   'adds', 'software', 'firmware'],
        }
    ),
    (
        '重大合作',
        {
            'zh': ['合作', '战略合作', '融资', '收购', '投资', '签约',
                   '联合', '入股', '并购', '战略投资', '轮融资', '亿元'],
            'en': ['partnership', 'collaboration', 'acquisition', 'investment',
                   'funding', 'deal', 'joint venture', 'series a', 'series b',
                   'series c', 'raises', 'backed', 'acquires', 'million'],
        }
    ),
    (
        '营销策略',
        {
            'zh': ['营销', '市场', '定价', '活动', '展会', '展览',
                   '品牌', '销售', '促销', 'ces', 'imo'],
            'en': ['marketing', 'strategy', 'pricing', 'campaign',
                   'exhibition', 'trade show', 'brand', 'commercial',
                   'ces', 'imo', 'market share'],
        }
    ),
]

MODULE_ICONS = {
    '新产品发布': '🤖',
    '新功能发布': '⚡',
    '重大合作':  '🤝',
    '营销策略':  '📣',
    '其他':     '📰',
}
MODULE_ORDER = ['新产品发布', '新功能发布', '重大合作', '营销策略', '其他']


# ── HTML 纯文本提取 ───────────────────────────────────────────
class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self._parts = []
        self._skip  = False

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self._skip = True

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self._skip = False

    def handle_data(self, data):
        if not self._skip:
            self._parts.append(data)

    def text(self):
        return ' '.join(self._parts).strip()


def strip_html(raw: str, max_len: int = 250) -> str:
    if not raw:
        return ''
    try:
        p = _TextExtractor()
        p.feed(raw)
        t = p.text()
        # Collapse whitespace
        t = re.sub(r'\s+', ' ', t)
        return t[:max_len]
    except Exception:
        return re.sub(r'<[^>]+>', ' ', raw)[:max_len]


# ── 工具函数 ──────────────────────────────────────────────────
def url_hash(url: str) -> str:
    return hashlib.md5(url.strip().encode()).hexdigest()


def load_cache() -> dict:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {'seen': []}


def save_cache(cache: dict):
    CACHE_FILE.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2),
        encoding='utf-8'
    )


def fetch_url(url: str) -> bytes:
    """抓取 URL 内容，返回原始字节。失败抛出异常。"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode    = ssl.CERT_NONE
    req = Request(url, headers={'User-Agent': USER_AGENT,
                                'Accept': '*/*'})
    with urlopen(req, timeout=FETCH_TIMEOUT, context=ctx) as resp:
        return resp.read()


def decode_bytes(raw: bytes) -> str:
    for enc in ('utf-8', 'gbk', 'gb2312', 'latin-1'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', errors='replace')


def classify(title: str, summary: str, lang: str) -> str:
    text = (title + ' ' + (summary or '')).lower()
    for module, kw_map in RULES:
        for kw in kw_map.get(lang, []):
            if kw.lower() in text:
                return module
    return '其他'


# ── RSS 解析（支持 RSS 2.0 和 Atom）────────────────────────────
# XML namespace 映射
_NS = {
    'atom':    'http://www.w3.org/2005/Atom',
    'content': 'http://purl.org/rss/1.0/modules/content/',
    'dc':      'http://purl.org/dc/elements/1.1/',
}


def _text(el, *tags) -> str:
    """从 XML 元素依次尝试多个子标签，返回第一个非空文本。"""
    for tag in tags:
        child = el.find(tag)
        if child is not None and child.text:
            return child.text.strip()
    return ''


def parse_rss(xml_text: str, source_name: str, lang: str) -> list:
    """解析 RSS 2.0 或 Atom feed，返回统一格式的列表。"""
    items = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        print(f"  ⚠  {source_name}: XML 解析失败 — {e}")
        return []

    # 去掉命名空间前缀，统一处理
    tag = root.tag.lower()

    # ── Atom ──
    if 'atom' in tag or root.tag == '{http://www.w3.org/2005/Atom}feed':
        ns = 'http://www.w3.org/2005/Atom'
        for entry in root.findall(f'{{{ns}}}entry')[:MAX_PER_SOURCE]:
            title = strip_html(
                (entry.findtext(f'{{{ns}}}title') or '').strip()
            )
            link_el = entry.find(f'{{{ns}}}link[@rel="alternate"]') \
                   or entry.find(f'{{{ns}}}link')
            url = ''
            if link_el is not None:
                url = link_el.get('href', '') or (link_el.text or '')
            summary = strip_html(
                entry.findtext(f'{{{ns}}}summary') or
                entry.findtext(f'{{{ns}}}content') or ''
            )
            if title and url:
                items.append({
                    'title': title, 'url': url.strip(),
                    'summary': summary, 'source': source_name, 'lang': lang,
                    'fetched_at': datetime.now().strftime('%Y-%m-%d'),
                })
        return items

    # ── RSS 2.0 ──
    channel = root.find('channel') or root
    for item in channel.findall('item')[:MAX_PER_SOURCE]:
        title   = strip_html(_text(item, 'title'))
        url     = _text(item, 'link', 'guid')
        # <description> might contain HTML
        summary = strip_html(
            _text(item, '{http://purl.org/rss/1.0/modules/content/}encoded',
                  'description', 'summary')
        )
        if title and url:
            items.append({
                'title': title, 'url': url,
                'summary': summary, 'source': source_name, 'lang': lang,
                'fetched_at': datetime.now().strftime('%Y-%m-%d'),
            })
    return items


def fetch_source(source: dict) -> list:
    """采集单个源，失败时静默跳过。"""
    try:
        raw  = fetch_url(source['url'])
        text = decode_bytes(raw)
        items = parse_rss(text, source['name'], source['lang'])
        if items:
            print(f"  ✅ {source['name']}: {len(items)} 条")
        else:
            print(f"  ⚠  {source['name']}: 解析后无条目")
        return items
    except (URLError, HTTPError, socket.timeout, OSError) as e:
        print(f"  ❌ {source['name']}: 网络错误 — {e}")
        return []
    except Exception as e:
        print(f"  ❌ {source['name']}: {e}")
        return []


# ── Markdown 文档写入 ─────────────────────────────────────────
def append_to_daily_doc(new_items: list):
    today    = datetime.now().strftime('%Y-%m-%d')
    doc_path = DOCS_DIR / f'{today}.md'

    grouped: dict[str, list] = {}
    for item in new_items:
        grouped.setdefault(item['module'], []).append(item)

    if not grouped:
        return

    lines = []
    if not doc_path.exists():
        lines.append(f'# 机器人行业资讯 — {today}\n')
    else:
        lines.append(f'\n\n---\n*更新于 {datetime.now().strftime("%H:%M")}*\n')

    for module in MODULE_ORDER:
        if module not in grouped:
            continue
        icon = MODULE_ICONS[module]
        lines.append(f'\n## {icon} {module}\n')
        for item in grouped[module]:
            lines.append(
                f'【{module}】{item["title"]}  \n'
                f'来源：{item["source"]} | {item["url"]}\n'
            )

    with open(doc_path, 'a', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    print(f"  📄 已写入 docs/{today}.md")


# ── 主流程 ────────────────────────────────────────────────────
def main() -> int:
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"\n{'='*54}")
    print(f"  🤖 机器人资讯采集  {ts}")
    print(f"{'='*54}\n")

    cache    = load_cache()
    seen_set = set(cache.get('seen', []))

    print("📡 正在采集各资讯源...\n")
    raw_items: list = []
    for source in RSS_SOURCES:
        raw_items.extend(fetch_source(source))

    print(f"\n共抓取原始条目：{len(raw_items)} 条")

    # 去重 + 分类
    new_items = []
    for item in raw_items:
        h = url_hash(item['url'])
        if h in seen_set:
            continue
        seen_set.add(h)
        item['module'] = classify(item['title'], item['summary'], item['lang'])
        item['hash']   = h
        new_items.append(item)

    print(f"🔍 去重后新增：{len(new_items)} 条\n")

    # 保存去重缓存（最多保留 5000 条哈希）
    cache['seen']         = list(seen_set)[-5000:]
    cache['last_updated'] = datetime.now().isoformat()
    save_cache(cache)

    # 更新 news_cache.json
    existing = []
    if NEWS_CACHE_FILE.exists():
        try:
            data     = json.loads(NEWS_CACHE_FILE.read_text(encoding='utf-8'))
            existing = data.get('items', [])
        except Exception:
            pass

    combined = (new_items + existing)[:NEWS_CACHE_SIZE]
    NEWS_CACHE_FILE.write_text(
        json.dumps({
            'last_updated': datetime.now().isoformat(),
            'new_count':    len(new_items),
            'total':        len(combined),
            'items':        combined,
        }, ensure_ascii=False, indent=2),
        encoding='utf-8'
    )

    # 写入每日文档
    if new_items:
        append_to_daily_doc(new_items)

    # 统计
    from collections import Counter
    counts = Counter(item['module'] for item in new_items)
    if counts:
        print("\n本次新增分类统计：")
        for module in MODULE_ORDER:
            if module in counts:
                print(f"  {MODULE_ICONS[module]} {module}: {counts[module]} 条")

    print(f"\n✅ 完成！新增 {len(new_items)} 条资讯")
    print(f"{'='*54}\n")
    return len(new_items)


if __name__ == '__main__':
    sys.exit(0 if main() >= 0 else 1)

import feedparser
import webbrowser
import os
import re
import time
import html
import json
import sys
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timezone

# ── 設定 ──────────────────────────────────────────────────────────────
MAX_PER_FEED = 5

FEEDS = {
    "台灣本地": [
        ("Yahoo 奇摩新聞", "https://tw.news.yahoo.com/rss/"),
        ("Google News 台灣", "https://news.google.com/rss?hl=zh-TW&gl=TW&ceid=TW:zh-Hant"),
        ("ETtoday 即時", "https://feeds.feedburner.com/ettoday/realtime"),
    ],
    "國際時事": [
        ("BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml"),
        ("The Guardian", "https://www.theguardian.com/world/rss"),
        ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml"),
        ("France 24", "https://www.france24.com/en/rss"),
        ("Deutsche Welle", "https://rss.dw.com/xml/rss-en-top"),
    ],
    "科技 / AI": [
        ("TechCrunch", "https://techcrunch.com/feed/"),
        ("The Verge", "https://www.theverge.com/rss/index.xml"),
        ("MIT Technology Review", "https://www.technologyreview.com/feed/"),
        ("Ars Technica", "https://feeds.arstechnica.com/arstechnica/index"),
    ],
    "財經市場": [
        ("Yahoo Finance", "https://finance.yahoo.com/news/rssindex"),
        ("CNBC Finance", "https://www.cnbc.com/id/10000664/device/rss/rss.html"),
        ("Bloomberg Markets", "https://feeds.bloomberg.com/markets/news.rss"),
        ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    ],
}

CATEGORY_ICONS = {
    "台灣本地": "🇹🇼",
    "國際時事": "🌍",
    "科技 / AI": "💻",
    "財經市場": "📈",
}

CATEGORY_COLORS = {
    "台灣本地": "#2563eb",
    "國際時事": "#16a34a",
    "科技 / AI": "#7c3aed",
    "財經市場": "#dc2626",
}

# ── 工具函式 ──────────────────────────────────────────────────────────

def parse_time(entry):
    for attr in ("published_parsed", "updated_parsed"):
        t = getattr(entry, attr, None)
        if t:
            try:
                return datetime(*t[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    return None


def is_english(text):
    """判斷文字是否主要為英文（需要翻譯）。"""
    if not text or len(text) < 4:
        return False
    cjk = sum(1 for c in text if '一' <= c <= '鿿' or '㐀' <= c <= '䶿')
    ascii_alpha = sum(1 for c in text if c.isascii() and c.isalpha())
    total = cjk + ascii_alpha
    if total == 0:
        return False
    return ascii_alpha / total > 0.65 and cjk < 3


def translate_batch(texts):
    """批次翻譯英文文字為繁體中文，出錯時回傳原文。"""
    if not texts:
        return texts
    try:
        from deep_translator import GoogleTranslator
        translator = GoogleTranslator(source="en", target="zh-TW")
        result = []
        chunk_size = 20
        for i in range(0, len(texts), chunk_size):
            chunk = texts[i:i + chunk_size]
            try:
                translated = translator.translate_batch(chunk)
                result.extend(t if t else chunk[j] for j, t in enumerate(translated))
            except Exception:
                # 單批次失敗時逐條翻譯，再失敗就保留原文
                for text in chunk:
                    try:
                        t = translator.translate(text[:400])
                        result.append(t if t else text)
                    except Exception:
                        result.append(text)
            if i + chunk_size < len(texts):
                time.sleep(0.3)
        return result
    except Exception as e:
        print(f"  翻譯失敗：{e}")
        return texts


# ── 抓取 ──────────────────────────────────────────────────────────────

def fetch_feed(name, url):
    try:
        feed = feedparser.parse(url, request_headers={"User-Agent": "Mozilla/5.0"})
        items = []
        for entry in feed.entries[:MAX_PER_FEED]:
            title = getattr(entry, "title", "（無標題）").strip()
            # 移除 Google News 標題末尾的「 - 媒體名稱」
            title = re.sub(r'\s*-\s*[^-]{1,30}$', '', title).strip() if "google" in url else title
            link = getattr(entry, "link", "#")
            summary_raw = getattr(entry, "summary", "") or ""
            summary = re.sub(r"<[^>]+>", "", summary_raw).strip()[:150]
            if len(summary) == 150:
                summary += "…"
            pub_time = parse_time(entry)
            time_str = pub_time.strftime("%m/%d %H:%M") if pub_time else ""
            items.append({
                "title": title,
                "link": link,
                "summary": summary,
                "time": time_str,
            })
        return items
    except Exception:
        return []


def translate_all(all_data):
    """掃描所有 item，將英文標題與摘要翻譯為繁體中文。"""
    locations = []
    texts = []

    for cat, sources in all_data.items():
        for src_idx, (src_name, items) in enumerate(sources):
            for item_idx, item in enumerate(items):
                for field in ("title", "summary"):
                    text = item[field]
                    if is_english(text):
                        locations.append((cat, src_idx, item_idx, field))
                        texts.append(text)

    if not texts:
        print("  無英文內容需翻譯")
        return all_data

    print(f"  翻譯 {len(texts)} 則英文內容...")
    translated = translate_batch(texts)

    for (cat, src_idx, item_idx, field), tr_text in zip(locations, translated):
        if tr_text:
            all_data[cat][src_idx][1][item_idx][field] = tr_text

    return all_data


# ── 產生 HTML ─────────────────────────────────────────────────────────

def build_html(all_data, generated_at):
    cat_keys = list(all_data.keys())
    nav_links = "".join(
        f'<a href="#cat-{i}">{CATEGORY_ICONS.get(c, "📰")} {c}</a>'
        for i, c in enumerate(cat_keys)
    )

    category_sections = ""
    for cat, sources in all_data.items():
        icon = CATEGORY_ICONS.get(cat, "📰")
        color = CATEGORY_COLORS.get(cat, "#333")
        cat_id = f"cat-{cat_keys.index(cat)}"
        cards = ""
        for src_name, items in sources:
            news_items = ""
            for item in items:
                time_badge = f'<span class="time">{item["time"]}</span>' if item["time"] else ""
                summary_html = (
                    f'<p class="summary">{html.escape(item["summary"])}</p>'
                    if item["summary"] else ""
                )
                news_items += f"""
                <li>
                  <a href="{item['link']}" target="_blank" rel="noopener">{html.escape(item['title'])}</a>
                  {time_badge}
                  {summary_html}
                </li>"""
            cards += f"""
            <div class="card">
              <div class="card-header" style="border-left:4px solid {color};">
                <span class="src-name">{src_name}</span>
              </div>
              <ul class="news-list">{news_items}</ul>
            </div>"""

        category_sections += f"""
        <section class="category" id="{cat_id}">
          <h2 class="cat-title" style="color:{color};">{icon} {cat}</h2>
          <div class="cards-grid">{cards}</div>
        </section>"""

    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>新聞摘要</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, "Segoe UI", "PingFang TC", "Microsoft JhengHei", sans-serif;
      background: #f1f5f9;
      color: #1e293b;
      line-height: 1.6;
    }}
    header {{
      background: #0f172a;
      color: #f8fafc;
      padding: 20px 32px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
    }}
    header h1 {{ font-size: 1.4rem; letter-spacing: 0.05em; }}
    header .meta {{ font-size: 0.8rem; color: #94a3b8; white-space: nowrap; }}
    nav {{
      background: #1e293b;
      padding: 8px 32px;
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
    }}
    nav a {{
      color: #cbd5e1;
      text-decoration: none;
      font-size: 0.85rem;
      padding: 4px 12px;
      border-radius: 4px;
      transition: background 0.15s;
    }}
    nav a:hover {{ background: #334155; color: #f1f5f9; }}
    main {{ max-width: 1400px; margin: 0 auto; padding: 24px 24px 48px; }}
    .category {{ margin-bottom: 40px; scroll-margin-top: 12px; }}
    .cat-title {{
      font-size: 1.15rem;
      font-weight: 700;
      margin-bottom: 14px;
      padding-bottom: 6px;
      border-bottom: 2px solid currentColor;
    }}
    .cards-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
      gap: 14px;
    }}
    .card {{
      background: #fff;
      border-radius: 8px;
      box-shadow: 0 1px 4px rgba(0,0,0,.08);
      overflow: hidden;
    }}
    .card-header {{
      padding: 8px 12px;
      background: #f8fafc;
    }}
    .src-name {{ font-size: 0.8rem; font-weight: 600; color: #475569; }}
    .news-list {{ list-style: none; }}
    .news-list li {{
      padding: 10px 14px;
      border-bottom: 1px solid #f1f5f9;
    }}
    .news-list li:last-child {{ border-bottom: none; }}
    .news-list a {{
      color: #1e293b;
      text-decoration: none;
      font-weight: 500;
      font-size: 0.875rem;
      display: block;
      line-height: 1.4;
    }}
    .news-list a:hover {{ color: #2563eb; text-decoration: underline; }}
    .time {{
      display: inline-block;
      font-size: 0.7rem;
      color: #94a3b8;
      margin-top: 3px;
    }}
    .summary {{
      font-size: 0.75rem;
      color: #64748b;
      margin-top: 4px;
      line-height: 1.5;
    }}
    footer {{
      text-align: center;
      padding: 20px;
      color: #94a3b8;
      font-size: 0.78rem;
    }}
  </style>
</head>
<body>
  <header>
    <h1>📰 每日新聞摘要</h1>
    <span class="meta">更新時間：{generated_at}</span>
  </header>
  <nav>{nav_links}</nav>
  <main>{category_sections}</main>
  <footer>資料來源：各媒體 RSS Feed（中立聚合）&nbsp;|&nbsp; 英文內容已自動翻譯為繁體中文</footer>
</body>
</html>"""


# ── Email ─────────────────────────────────────────────────────────────

def load_config():
    """讀取 config.json；不存在時回傳 None。"""
    cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    if not os.path.exists(cfg_path):
        return None
    with open(cfg_path, encoding="utf-8") as f:
        return json.load(f)


def build_email_html(all_data, generated_at):
    """產生適合 email client 的內嵌樣式 HTML（不依賴 CSS Grid）。"""
    category_blocks = ""
    for cat, sources in all_data.items():
        icon  = CATEGORY_ICONS.get(cat, "📰")
        color = CATEGORY_COLORS.get(cat, "#333")

        source_blocks = ""
        for src_name, items in sources:
            rows = ""
            for item in items:
                time_str = f' <span style="color:#94a3b8;font-size:11px;">({item["time"]})</span>' if item["time"] else ""
                rows += (
                    f'<li style="margin:5px 0;">'
                    f'<a href="{item["link"]}" style="color:#1e293b;text-decoration:none;font-size:13px;line-height:1.5;">'
                    f'{html.escape(item["title"])}</a>{time_str}'
                    f'</li>\n'
                )
            source_blocks += (
                f'<div style="margin-bottom:14px;">'
                f'<p style="margin:0 0 4px;font-size:11px;font-weight:700;color:#64748b;'
                f'text-transform:uppercase;letter-spacing:.04em;">{src_name}</p>'
                f'<ul style="margin:0;padding-left:18px;list-style:disc;">{rows}</ul>'
                f'</div>\n'
            )

        category_blocks += (
            f'<div style="padding:16px 28px;border-bottom:1px solid #e2e8f0;">'
            f'<h2 style="margin:0 0 12px;font-size:14px;font-weight:700;color:{color};'
            f'border-bottom:2px solid {color};padding-bottom:5px;">{icon} {cat}</h2>'
            f'{source_blocks}'
            f'</div>\n'
        )

    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>每日新聞摘要 {generated_at}</title>
</head>
<body style="margin:0;padding:0;background:#f1f5f9;font-family:Arial,'Microsoft JhengHei',sans-serif;">
  <div style="max-width:680px;margin:24px auto;background:#fff;border-radius:8px;
              overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.1);">
    <div style="background:#0f172a;padding:20px 28px;">
      <h1 style="margin:0;color:#f8fafc;font-size:18px;">📰 每日新聞摘要</h1>
      <p style="margin:5px 0 0;color:#94a3b8;font-size:12px;">{generated_at}</p>
    </div>
    {category_blocks}
    <div style="padding:14px 28px;background:#f8fafc;text-align:center;
                color:#94a3b8;font-size:11px;">
      資料來源：各媒體 RSS Feed（中立聚合）｜英文內容已自動翻譯為繁體中文
    </div>
  </div>
</body>
</html>"""


def send_email(email_html, generated_at, config):
    """透過 Gmail SMTP 寄出 HTML 信件。"""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"📰 每日新聞摘要 {generated_at}"
    msg["From"]    = config["gmail_user"]
    msg["To"]      = config["recipient"]
    msg.attach(MIMEText(email_html, "html", "utf-8"))

    print(f"正在寄送到 {config['recipient']}...")
    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.ehlo()
        server.starttls()
        server.login(config["gmail_user"], config["gmail_app_password"])
        server.send_message(msg)
    print("[OK] 寄送成功！")


# ── 主程式 ────────────────────────────────────────────────────────────

def main():
    send_mode = "--send" in sys.argv

    print("正在抓取新聞...")
    all_data = {}
    for cat, feeds in FEEDS.items():
        sources = []
        for name, url in feeds:
            print(f"  [{cat}] {name}...", end=" ", flush=True)
            items = fetch_feed(name, url)
            print(f"{len(items)} 則")
            if items:
                sources.append([name, items])
        all_data[cat] = sources

    print("\n正在翻譯英文內容...")
    all_data = translate_all(all_data)

    generated_at = datetime.now().strftime("%Y/%m/%d %H:%M")

    if send_mode:
        # ── 排程模式：寄 email，不開瀏覽器 ──
        config = load_config()
        if not config:
            print("❌ 找不到 config.json，請先建立設定檔。")
            sys.exit(1)
        email_html = build_email_html(all_data, generated_at)
        send_email(email_html, generated_at, config)
    else:
        # ── 手動模式：產生 HTML 並開瀏覽器 ──
        html_content = build_html(all_data, generated_at)
        out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "news.html")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        print(f"\n已產生：{out_path}")
        webbrowser.open(f"file:///{out_path}")


if __name__ == "__main__":
    main()

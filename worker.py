import concurrent.futures
import json
import os
import re
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import feedparser
import requests
from bs4 import BeautifulSoup
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


def split_keys(raw_val: Optional[str]) -> List[str]:
    if not raw_val:
        return []
    return [x.strip() for x in re.split(r"[\n,;]+", raw_val.strip()) if x.strip()]


CLIENT_ID = os.environ.get("BLOGGER_CLIENT_ID", "").strip()
CLIENT_SECRET = os.environ.get("BLOGGER_CLIENT_SECRET", "").strip()
REFRESH_TOKEN = (
    os.environ.get("BLOGGER_REFRESH_TOKEN", "")
    .replace("\n", "")
    .replace("\r", "")
    .replace(" ", "")
    .strip()
)

GEMINI_KEYS = split_keys(os.environ.get("GEMINI_API_KEY", ""))
GROQ_KEYS = split_keys(os.environ.get("GROQ_API_KEY", ""))
HF_KEYS = split_keys(os.environ.get("HF_API_KEY", ""))

TELEGRAM_API_TOKEN = os.environ.get("TELEGRAM_API_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

FB_PAGE_ID = os.environ.get("FB_PAGE_ID", "").strip()
FB_PAGE_ACCESS_TOKEN = os.environ.get("FB_PAGE_ACCESS_TOKEN", "").strip()

CUSTOM_TOPIC_ENV = os.environ.get("CUSTOM_TOPIC", "").strip()
GITHUB_REPO = os.environ.get("GITHUB_REPOSITORY", "").strip()

SITE_NAME = "The Daily Insight"
HISTORY_FILE = "posted_history.json"
IMAGES_DIR = "images"
TOPIC_QUEUE_FILE = "topic_queue.txt"

RSS_FEEDS = [
    ("TechCrunch", "https://techcrunch.com/feed/"),
    ("The Verge", "https://www.theverge.com/rss/index.xml"),
    ("Hacker News", "https://hnrss.org/frontpage"),
    ("Dev.to", "https://dev.to/feed"),
    ("ScienceDaily", "https://www.sciencedaily.com/rss/top/science.xml"),
    ("Wired", "https://www.wired.com/feed/rss"),
    ("Ars Technica", "https://feeds.arstechnica.com/arstechnica/index"),
    ("MIT Tech Review", "https://www.technologyreview.com/feed/"),
    ("GitHub Blog", "https://github.blog/feed/"),
    ("Phys.org", "https://phys.org/rss-feed/"),
]

GEMINI_MODELS = [
    "gemini-3.1-pro-preview",
    "gemini-3-flash-preview",
    "gemini-3.5-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
]

GROQ_MODELS = [
    "openai/gpt-oss-120b",
    "llama-3.3-70b-versatile",
    "openai/gpt-oss-20b",
    "llama-3.1-8b-instant",
]

FALLBACK_IMAGES = {
    "Technology": "https://images.unsplash.com/photo-1518770660439-4636190af475?w=1200&q=80",
    "Developer Tools": "https://images.unsplash.com/photo-1555066931-4365d14bab8c?w=1200&q=80",
    "Science": "https://images.unsplash.com/photo-1507413245164-6160d8298b31?w=1200&q=80",
    "World News": "https://images.unsplash.com/photo-1451187580459-43490279c0fa?w=1200&q=80",
}


def send_telegram_alert(step: str, detail: str, ctx: str = "", skipped: bool = False) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    tag = "⏭️ SKIPPED" if skipped else "⚠️ ALERT"
    msg = (
        f"{tag} | {SITE_NAME}\n"
        f"Time: {ts}\n"
        f"Step: {step}\n"
        f"Context: {ctx[:200] if ctx else 'N/A'}\n"
        f"Info: {str(detail)[:1100]}"
    )
    print(f"[TELEGRAM] {msg}")
    if not TELEGRAM_API_TOKEN or not TELEGRAM_CHAT_ID:
        print("[TELEGRAM_SKIP] Missing TELEGRAM_API_TOKEN or TELEGRAM_CHAT_ID in workflow env.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_API_TOKEN}/sendMessage"
    for _ in range(2):
        try:
            r = requests.post(
                url,
                json={"chat_id": TELEGRAM_CHAT_ID, "text": msg[:3900], "disable_web_page_preview": True},
                timeout=20,
            )
            if r.ok:
                return
            print(f"[TELEGRAM_RETRY] HTTP {r.status_code}")
        except Exception as e:
            print(f"[TELEGRAM_RETRY] {e}")
        time.sleep(2)


def load_history() -> List[Dict[str, Any]]:
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, list) else []
    except Exception:
        return []


def save_history(hist: List[Dict[str, Any]]) -> None:
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(hist[-120:], f, ensure_ascii=False, indent=2)


def pop_custom_topic() -> Optional[str]:
    if CUSTOM_TOPIC_ENV:
        return CUSTOM_TOPIC_ENV
    if not os.path.exists(TOPIC_QUEUE_FILE):
        return None
    try:
        with open(TOPIC_QUEUE_FILE, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
        out: List[str] = []
        picked: Optional[str] = None
        for raw in lines:
            s = raw.strip()
            if not s or s.startswith("#") or s.startswith("[DONE]"):
                out.append(raw)
            elif picked is None:
                picked = s
                out.append(f"[DONE] {s}")
            else:
                out.append(raw)
        if picked:
            with open(TOPIC_QUEUE_FILE, "w", encoding="utf-8") as f:
                f.write("\n".join(out) + "\n")
            return picked
    except Exception as e:
        send_telegram_alert("Custom Topic Queue", f"Queue read failed, falling back to RSS: {e}", TOPIC_QUEUE_FILE, skipped=True)
    return None


def call_groq_chain(prompt: str, temp: float = 0.55) -> Tuple[str, Optional[str]]:
    if not GROQ_KEYS:
        return "Groq", None
    endpoint = "https://api.groq.com/openai/v1/chat/completions"
    for idx, key in enumerate(GROQ_KEYS, start=1):
        hdrs = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        for m in GROQ_MODELS:
            try:
                r = requests.post(
                    endpoint,
                    headers=hdrs,
                    json={"model": m, "messages": [{"role": "user", "content": prompt}], "temperature": temp},
                    timeout=55,
                )
                if r.status_code == 200:
                    txt = (((r.json().get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
                    if txt:
                        print(f"[AI_OK] Groq ({m}, Key #{idx})")
                        return "Groq", txt
            except Exception as e:
                print(f"[AI_WARN] Groq Key#{idx} ({m}): {e}")
    return "Groq", None


def call_gemini_chain(prompt: str, temp: float = 0.65) -> Tuple[str, Optional[str]]:
    if not GEMINI_KEYS:
        return "Gemini", None
    hdrs = {"Content-Type": "application/json"}
    body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": temp}}
    for idx, key in enumerate(GEMINI_KEYS, start=1):
        for m in GEMINI_MODELS:
            u = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={key}"
            try:
                r = requests.post(u, headers=hdrs, json=body, timeout=65)
                if r.status_code == 200:
                    parts = (((r.json().get("candidates") or [{}])[0].get("content") or {}).get("parts")) or []
                    txt = "".join(p.get("text", "") for p in parts if isinstance(p, dict)).strip()
                    if txt:
                        print(f"[AI_OK] Gemini ({m}, Key #{idx})")
                        return "Gemini", txt
            except Exception as e:
                print(f"[AI_WARN] Gemini Key#{idx} ({m}): {e}")
    return "Gemini", None


def run_parallel_two_ai(
    prompt: str,
    step: str,
    temp: float = 0.6,
    prefer_merge: bool = False,
    required: bool = True,
) -> str:
    results: Dict[str, str] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        f_groq = pool.submit(call_groq_chain, prompt, temp)
        f_gem = pool.submit(call_gemini_chain, prompt, temp)
        for fut in concurrent.futures.as_completed([f_groq, f_gem]):
            provider, out = fut.result()
            if out:
                results[provider] = out

    if len(results) == 2 and prefer_merge:
        return f"--- Primary ---\n{results['Groq']}\n\n--- Parallel ---\n{results['Gemini']}"
    if "Groq" in results:
        return results["Groq"]
    if "Gemini" in results:
        return results["Gemini"]

    err = f"All Groq & Gemini keys/models failed at {step}"
    send_telegram_alert(step, err, skipped=not required)
    if required:
        raise RuntimeError(err)
    return ""


def scrape_news(seen_urls: set) -> List[Dict[str, str]]:
    found: List[Dict[str, str]] = []
    for src, feed_url in RSS_FEEDS:
        try:
            parsed = feedparser.parse(feed_url)
            for item in parsed.entries[:4]:
                link = (item.get("link") or "").strip()
                title = (item.get("title") or "").strip()
                raw_s = item.get("summary") or item.get("description") or ""
                clean_s = re.sub(r"\s+", " ", re.sub(r"<[^<]+?>", " ", raw_s)).strip()[:380]
                if link and title and link not in seen_urls:
                    found.append({"source": src, "title": title, "link": link, "summary": clean_s})
        except Exception as e:
            print(f"[FEED_WARN] {src}: {e}")
    return found


def fetch_article_text(url: str, fallback: str) -> str:
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            paras = [p.get_text(" ", strip=True) for p in soup.find_all("p") if len(p.get_text(" ", strip=True)) > 40]
            joined = "\n".join(paras)[:3800]
            if len(joined) > 250:
                return joined
    except Exception as e:
        print(f"[FETCH_WARN] {e}")
    return fallback


def save_img(data_bytes: bytes, ext: str = "jpg") -> Optional[str]:
    os.makedirs(IMAGES_DIR, exist_ok=True)
    rand_tag = int(time.time() * 1000) % 100000
    fname = f"img_{int(time.time())}_{rand_tag}.{ext}"
    fpath = os.path.join(IMAGES_DIR, fname)
    with open(fpath, "wb") as f:
        f.write(data_bytes)
    if GITHUB_REPO:
        return f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/{IMAGES_DIR}/{fname}"
    return None


def make_image(prompt_txt: str, category: str, seed_num: int) -> str:
    smart_prompt = (
        f"Unique editorial visual #{seed_num} for '{prompt_txt}' ({category}). "
        "Distinct scene, fresh angle, realistic photography, clean modern lighting, "
        "no text, no logo, no watermark"
    )
    hf_models = ["black-forest-labs/FLUX.1-schnell", "stabilityai/stable-diffusion-xl-base-1.0"]
    hf_errs: List[str] = []

    if HF_KEYS:
        for idx, key in enumerate(HF_KEYS, start=1):
            for m in hf_models:
                try:
                    u = f"https://router.huggingface.co/hf-inference/models/{m}"
                    hdrs = {"Authorization": f"Bearer {key}", "Content-Type": "application/json", "Accept": "image/jpeg"}
                    payload = {
                        "inputs": smart_prompt,
                        "parameters": {"negative_prompt": "watermark, text, logo, signature", "seed": seed_num},
                    }
                    r = requests.post(u, headers=hdrs, json=payload, timeout=60)
                    ctype = (r.headers.get("Content-Type") or "").lower()
                    if r.status_code == 200 and ("image/" in ctype or len(r.content) > 5000):
                        ext = "png" if "png" in ctype else "jpg"
                        saved = save_img(r.content, ext=ext)
                        if saved:
                            return saved
                    hf_errs.append(f"HF Key#{idx} [{m}] {r.status_code}")
                except Exception as e:
                    hf_errs.append(f"HF Key#{idx} [{m}]: {e}")

    try:
        enc = urllib.parse.quote(smart_prompt[:420])
        p_url = f"https://image.pollinations.ai/prompt/{enc}?width=1200&height=675&nologo=true&model=flux&seed={seed_num}"
        r = requests.get(p_url, timeout=45)
        if r.status_code == 200 and len(r.content) > 5000:
            saved = save_img(r.content, ext="jpg")
            return saved or p_url
    except Exception as e:
        hf_errs.append(f"Pollinations: {e}")

    send_telegram_alert(
        "AI Image Generation",
        "All AI image backups failed; using category fallback image.\n" + "\n".join(hf_errs[-4:]),
        category,
        skipped=True,
    )
    return FALLBACK_IMAGES.get(category, FALLBACK_IMAGES["Technology"])
    def share_fb(title: str, url: str, category: str) -> None:
    if not FB_PAGE_ID or not FB_PAGE_ACCESS_TOKEN:
        send_telegram_alert(
            "Facebook Auto-Share",
            "FB_PAGE_ID or FB_PAGE_ACCESS_TOKEN missing in workflow env. Step skipped.",
            url,
            skipped=True,
        )
        return

    msg = f"{title}\n\n{SITE_NAME} ({category})\nবিস্তারিত পড়ুন: {url}"
    g_url = f"https://graph.facebook.com/v21.0/{FB_PAGE_ID}/feed"
    last_err = ""
    for attempt in range(1, 3):
        try:
            r = requests.post(
                g_url,
                data={"message": msg[:1800], "link": url, "access_token": FB_PAGE_ACCESS_TOKEN},
                timeout=30,
            )
            if r.status_code == 200:
                print(f"[FB_OK] Shared to {FB_PAGE_ID} (Attempt {attempt})")
                return
            last_err = f"Attempt {attempt} HTTP {r.status_code}: {r.text[:250]}"
        except Exception as e:
            last_err = f"Attempt {attempt} Exception: {e}"
        time.sleep(2)

    send_telegram_alert("Facebook Auto-Share", f"All retries failed; skipped. {last_err}", url, skipped=True)


def main() -> None:
    ctx = "Init"
    try:
        history = load_history()
        seen_urls = {x.get("source_url") for x in history if x.get("source_url")}
        recent_titles = [x.get("bn_title", "") for x in history[-10:] if x.get("bn_title")]
        last_cat = history[-1].get("category", "") if history else ""
        seed_num = (int(time.time()) + len(history) * 997) % 999999 + 1

        creds = Credentials(
            token=None,
            refresh_token=REFRESH_TOKEN,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
        )
        service = build("blogger", "v3", credentials=creds)
        blog_id = service.blogs().listByUser(userId="self").execute()["items"][0]["id"]

        custom_topic = pop_custom_topic()
        if custom_topic:
            ctx = f"Custom: {custom_topic}"
            words = [w.lower() for w in re.findall(r"[A-Za-z0-9\u0980-\u09FF]{3,}", custom_topic)]
            notes = [
                f"- [{c['source']}] {c['title']}: {c['summary']}"
                for c in scrape_news(set())
                if any(w in f"{c['title']} {c['summary']}".lower() for w in words)
            ][:5]

            cat_prompt = (
                f'Pick best category from [Technology, Developer Tools, Science, World News] for "{custom_topic}". '
                'Also give clean English image_prompt without text. '
                'JSON only: {"category":"Technology","image_prompt":"..."}'
            )
            cat_raw = run_parallel_two_ai(cat_prompt, "Custom Category", temp=0.4, required=False)
            category = "Technology"
            img_prompt = custom_topic
            if cat_raw:
                m = re.search(r"\{.*\}", cat_raw, re.DOTALL)
                if m:
                    try:
                        obj = json.loads(m.group())
                        category = obj.get("category") or "Technology"
                        img_prompt = obj.get("image_prompt") or custom_topic
                    except Exception:
                        pass

            res_prompt = (
                f"Research '{custom_topic}' across multiple sources and angles for '{SITE_NAME}'. "
                "Include ONLY verified facts; never invent numbers or fake claims. "
                "Include key facts, plain explanation of companies/terms, importance, and 2 Q&As.\n"
                f"Extra notes:\n{chr(10).join(notes) if notes else 'None'}"
            )
            research = run_parallel_two_ai(res_prompt, "Custom Research", temp=0.5, prefer_merge=True, required=True)
            selected = {
                "source": f"{SITE_NAME} Desk",
                "title": custom_topic,
                "link": f"https://thedailyinsightbangla.blogspot.com/?q={urllib.parse.quote(custom_topic)}",
            }
        else:
            ctx = "RSS Selection"
            candidates = scrape_news(seen_urls)
            if not candidates:
                send_telegram_alert("RSS Scrape", "No fresh unseen items; using backup recent pool.", ctx, skipped=True)
                candidates = scrape_news(set())[:10]
                if not candidates:
                    send_telegram_alert("RSS Scrape", "All RSS feeds failed; run skipped.", ctx, skipped=True)
                    return

            shortlist = candidates[:25]
            listed = "\n".join([f"{i}. [{a['source']}] {a['title']}" for i, a in enumerate(shortlist)])
            avoid_txt = "\n".join([f"- {t}" for t in recent_titles]) if recent_titles else "None"

            sel_prompt = (
                f"Chief editor for '{SITE_NAME}'. Pick a fresh, diverse story from the list. "
                f"Avoid topics similar to:\n{avoid_txt}\n"
                f"Last category was '{last_cat}'. Prefer variety across categories.\n"
                f"Items:\n{listed}\n"
                'Return JSON only: {"index":0,"category":"Technology","image_prompt":"..."}'
            )
            sel_raw = run_parallel_two_ai(sel_prompt, "Select Story", temp=0.55, required=True)
            choice = json.loads(re.search(r"\{.*\}", sel_raw, re.DOTALL).group())
            selected = shortlist[int(choice.get("index", 0)) % len(shortlist)]
            category = choice.get("category") or "Technology"
            img_prompt = choice.get("image_prompt") or selected["title"]
            ctx = selected["title"]

            full_txt = fetch_article_text(selected["link"], selected["summary"])
            res_prompt = (
                "Create a factual research brief from the details below. Never invent claims. "
                "Include core facts, perspectives, plain explanation of new companies/terms, impact, and 2 Q&As.\n"
                f"Title: {selected['title']}\nSource: {selected['source']}\nDetails:\n{full_txt}"
            )
            research = run_parallel_two_ai(res_prompt, "Story Research", temp=0.5, prefer_merge=True, required=True)

        write_lines = [
            f"Write a warm, natural, deep-insight human Bengali article for '{SITE_NAME}'.",
            "TERM FORMAT RULE: For main, hard, or unfamiliar technical terms and company names, write Bengali immediately followed by English in brackets with NO extra brackets or space, like: কৃত্রিম বুদ্ধিমত্তা(Artificial Intelligence) or রিফ্রেশ রেট(Refresh Rate).",
            "Explain new companies or hard concepts simply right inside the text so any reader understands easily.",
            "Stick strictly to verified facts; never invent statistics, prices, or fake claims.",
            "Use clean HTML tags (<p>, <h2>, <blockquote>, <table>, <ul>, <strong>), never **markdown** or CJK chars.",
            "Include: (1) A summary box titled 'এক নজরে মূল বিষয়', (2) Clear H2 sections, (3) One stylish blockquote, (4) A clean comparison/spec table if relevant, (5) A 'দ্রুত প্রশ্নোত্তর' section with 2 Q&As.",
            "First line MUST be <TITLE>Bengali Headline</TITLE>, followed by clean article HTML only.",
            f"Topic: {selected['title']}",
            f"Category: {category}",
            f"Research:\n{research}",
        ]
        raw_art = run_parallel_two_ai("\n".join(write_lines), "Write Article", temp=0.65, required=True)

        t_match = re.search(r"<TITLE>(.*?)</TITLE>", raw_art, re.DOTALL)
        bn_title = (t_match.group(1) if t_match else selected["title"]).strip()
        body_html = re.sub(r"<TITLE>.*?</TITLE>", "", raw_art, flags=re.DOTALL).strip()
        body_html = body_html.replace("```html", "").replace("```", "").strip()
        body_html = re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", body_html)
        body_html = re.sub(r"[\u4e00-\u9fff\u3040-\u30ff]+", "", body_html)
        bn_title = re.sub(r"[\u4e00-\u9fff\u3040-\u30ff]+", "", bn_title).strip()

        # Deep Black normal text + Rich Color Combination for headings, quotes, tables
        body_html = re.sub(r"<p(?![^>]*style=)", '<p style="color:#09090b;line-height:1.85;font-size:16px;margin:0 0 14px 0;"', body_html)
        body_html = re.sub(r"<li(?![^>]*style=)", '<li style="color:#09090b;line-height:1.8;font-size:15.5px;margin-bottom:6px;"', body_html)
        body_html = re.sub(r"<h2(?![^>]*style=)", '<h2 style="color:#1e3a8a;font-weight:700;font-size:21px;margin:26px 0 12px;padding-bottom:6px;border-bottom:2px solid #bfdbfe;"', body_html)
        body_html = re.sub(r"<blockquote(?![^>]*style=)", '<blockquote style="background:#f0fdfa;border-left:4px solid #0d9488;padding:15px 18px;margin:20px 0;border-radius:0 8px 8px 0;color:#0f172a;font-style:italic;font-size:16.5px;"', body_html)
        body_html = re.sub(r"<table(?![^>]*style=)", '<table style="width:100%;border-collapse:collapse;margin:18px 0;font-size:15px;color:#09090b;"', body_html)
        body_html = re.sub(r"<th(?![^>]*style=)", '<th style="background:#1e3a8a;color:#ffffff;padding:10px;border:1px solid #cbd5e1;text-align:left;"', body_html)
        body_html = re.sub(r"<td(?![^>]*style=)", '<td style="padding:10px;border:1px solid #cbd5e1;color:#09090b;background:#ffffff;"', body_html)

        img_url = make_image(img_prompt, category, seed_num)
        badge = (
            '<div style="margin-bottom:14px;"><span style="background:#dbeafe;color:#1e40af;'
            f'border:1px solid #93c5fd;padding:4px 12px;border-radius:20px;font-size:12px;font-weight:700;">{category}</span></div>'
        )
        img_box = (
            '<div style="text-align:center;margin:0 0 24px 0;">'
            f'<img src="{img_url}" alt="{bn_title}" '
            'style="width:100%;max-width:800px;border-radius:12px;box-shadow:0 6px 18px rgba(0,0,0,0.08);" /></div>'
        )

        past = [h for h in history if h.get("blog_url") and h.get("bn_title")][-2:]
        related = ""
        if past:
            lis = "".join(
                f'<li style="margin-bottom:8px;"><a href="{h["blog_url"]}" style="color:#1d4ed8;font-weight:700;text-decoration:none;">{h["bn_title"]}</a></li>'
                for h in reversed(past)
            )
            related = (
                '<div style="background:#eff6ff;border:1px solid #bfdbfe;padding:16px 18px;margin-top:26px;border-radius:10px;">'
                '<div style="font-weight:700;font-size:16px;color:#1e3a8a;margin-bottom:10px;">📌 আরও পড়ুন</div>'
                f'<ul style="padding-left:20px;margin:0;">{lis}</ul></div>'
            )

        credit = (
            '<p style="margin-top:22px;font-size:13.5px;color:#334155;border-top:1px solid #e2e8f0;padding-top:12px;">'
            f'<em>তথ্যসূত্র: <a href="{selected["link"]}" target="_blank" rel="noopener noreferrer" '
            f'style="color:#1d4ed8;font-weight:600;">{selected["source"]}</a></em></p>'
        )
        schema = {
            "@context": "https://schema.org",
            "@type": "NewsArticle",
            "headline": bn_title,
            "image": [img_url],
            "datePublished": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "author": {"@type": "Organization", "name": SITE_NAME},
        }
        schema_tag = f'<script type="application/ld+json">{json.dumps(schema, ensure_ascii=False)}</script>'

        final_html = f'<div style="color:#09090b;">{badge}{img_box}{body_html}{related}{credit}</div>{schema_tag}'
        created = (
            service.posts()
            .insert(
                blogId=blog_id,
                body={"title": bn_title, "content": final_html, "labels": [category, SITE_NAME]},
                isDraft=False,
            )
            .execute()
        )
        post_url = created.get("url")
        print(f"Published: {post_url}")

        share_fb(bn_title, post_url, category)

        history.append(
            {
                "source_url": selected["link"],
                "bn_title": bn_title,
                "blog_url": post_url,
                "category": category,
                "date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
        save_history(history)
        print("History saved.")

    except Exception as e:
        print(f"Pipeline error: {e}")
        send_telegram_alert("Pipeline Error", str(e), ctx, skipped=False)
        raise


if __name__ == "__main__":
    main()

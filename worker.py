import json
import os
import re
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

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


def send_telegram_alert(step: str, detail: str, ctx: str = "") -> None:
    if not TELEGRAM_API_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram alert skipped: missing secrets")
        return
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    parts = [
        f"Alert: {SITE_NAME}",
        f"Time: {ts}",
        f"Step: {step}",
        f"Context: {ctx[:200] if ctx else 'N/A'}",
        f"Error: {str(detail)[:1100]}",
    ]
    url = f"https://api.telegram.org/bot{TELEGRAM_API_TOKEN}/sendMessage"
    try:
        r = requests.post(
            url,
            json={"chat_id": TELEGRAM_CHAT_ID, "text": "\n".join(parts)[:3900]},
            timeout=20,
        )
        if not r.ok:
            print(f"Telegram failed: {r.status_code}")
    except Exception as e:
        print(f"Telegram exception: {e}")


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
        send_telegram_alert("Topic Queue", str(e), TOPIC_QUEUE_FILE)
    return None


def call_groq(prompt: str, temp: float = 0.55) -> Optional[str]:
    if not GROQ_KEYS:
        return None
    endpoint = "https://api.groq.com/openai/v1/chat/completions"
    for idx, key in enumerate(GROQ_KEYS, start=1):
        hdrs = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        for m in GROQ_MODELS:
            try:
                r = requests.post(
                    endpoint,
                    headers=hdrs,
                    json={"model": m, "messages": [{"role": "user", "content": prompt}], "temperature": temp},
                    timeout=50,
                )
                if r.status_code == 200:
                    txt = (((r.json().get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
                    if txt:
                        print(f"Groq OK: {m} (Key #{idx})")
                        return txt
            except Exception as e:
                print(f"Groq err ({m}): {e}")
    return None


def call_gemini(prompt: str, temp: float = 0.65) -> Optional[str]:
    if not GEMINI_KEYS:
        return None
    hdrs = {"Content-Type": "application/json"}
    body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": temp}}
    for idx, key in enumerate(GEMINI_KEYS, start=1):
        for m in GEMINI_MODELS:
            u = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={key}"
            try:
                r = requests.post(u, headers=hdrs, json=body, timeout=60)
                if r.status_code == 200:
                    parts = (((r.json().get("candidates") or [{}])[0].get("content") or {}).get("parts")) or []
                    txt = "".join(p.get("text", "") for p in parts if isinstance(p, dict)).strip()
                    if txt:
                        print(f"Gemini OK: {m} (Key #{idx})")
                        return txt
            except Exception as e:
                print(f"Gemini err ({m}): {e}")
    return None


def run_two_ai(prompt: str, step: str, temp: float = 0.6, required: bool = True) -> Optional[str]:
    res = call_groq(prompt, temp=temp)
    if res:
        return res
    res = call_gemini(prompt, temp=temp)
    if res:
        return res
    err = f"Both Groq and Gemini failed at {step}"
    send_telegram_alert(step, err)
    if required:
        raise RuntimeError(err)
    return None


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
            print(f"Feed err ({src}): {e}")
    return found


def fetch_article_text(url: str, fallback: str) -> str:
    try:
        hdrs = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=hdrs, timeout=15)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            paras = [p.get_text(" ", strip=True) for p in soup.find_all("p") if len(p.get_text(" ", strip=True)) > 40]
            joined = "\n".join(paras)[:3800]
            if len(joined) > 250:
                return joined
    except Exception as e:
        print(f"Page fetch fallback ({e})")
    return fallback


def save_img(data_bytes: bytes, ext: str = "jpg") -> Optional[str]:
    os.makedirs(IMAGES_DIR, exist_ok=True)
    fname = f"img_{int(time.time())}.{ext}"
    fpath = os.path.join(IMAGES_DIR, fname)
    with open(fpath, "wb") as f:
        f.write(data_bytes)
    if GITHUB_REPO:
        return f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/{IMAGES_DIR}/{fname}"
    return None


def make_image(prompt_txt: str, category: str) -> str:
    smart_prompt = (
        f"Editorial news visual for '{prompt_txt}' ({category}). "
        "Clean modern composition, natural lighting, no text, no logo, no watermark"
    )
    hf_models = ["black-forest-labs/FLUX.1-schnell", "stabilityai/stable-diffusion-xl-base-1.0"]
    hf_errs: List[str] = []

    if HF_KEYS:
        for idx, key in enumerate(HF_KEYS, start=1):
            for m in hf_models:
                try:
                    u = f"https://router.huggingface.co/hf-inference/models/{m}"
                    hdrs = {"Authorization": f"Bearer {key}", "Content-Type": "application/json", "Accept": "image/jpeg"}
                    payload = {"inputs": smart_prompt, "parameters": {"negative_prompt": "watermark, text, logo"}}
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

    if hf_errs:
        send_telegram_alert("HF Image Fallback", "\n".join(hf_errs[-4:]), category)

    try:
        enc = urllib.parse.quote(smart_prompt[:420])
        p_url = f"https://image.pollinations.ai/prompt/{enc}?width=1200&height=675&nologo=true&model=flux"
        r = requests.get(p_url, timeout=45)
        if r.status_code == 200 and len(r.content) > 5000:
            saved = save_img(r.content, ext="jpg")
            return saved or p_url
    except Exception as e:
        send_telegram_alert("Pollinations Image", str(e), category)

    return FALLBACK_IMAGES.get(category, FALLBACK_IMAGES["Technology"])
def share_fb(title: str, url: str, category: str) -> None:
    if not FB_PAGE_ID or not FB_PAGE_ACCESS_TOKEN:
        print("FB secrets missing, skip share")
        return
    msg = f"{title}\n\n{SITE_NAME} ({category})\n{url}"
    g_url = f"https://graph.facebook.com/v21.0/{FB_PAGE_ID}/feed"
    try:
        r = requests.post(
            g_url,
            data={"message": msg[:1800], "link": url, "access_token": FB_PAGE_ACCESS_TOKEN},
            timeout=30,
        )
        if r.status_code != 200:
            send_telegram_alert("Facebook Share", f"HTTP {r.status_code}: {r.text[:250]}", url)
    except Exception as e:
        send_telegram_alert("Facebook Share Exception", str(e), url)


def main() -> None:
    ctx = "Init"
    try:
        history = load_history()
        seen_urls = {x.get("source_url") for x in history if x.get("source_url")}
        recent_titles = [x.get("bn_title", "") for x in history[-10:] if x.get("bn_title")]
        last_cat = history[-1].get("category", "") if history else ""

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
            cat_raw = run_two_ai(cat_prompt, "Custom Category", temp=0.4, required=False)
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
                "Cover key facts, plain-language explanation of any companies/terms, importance, and 2 Q&As.\n"
                f"Extra notes:\n{chr(10).join(notes) if notes else 'None'}"
            )
            research = run_two_ai(res_prompt, "Custom Research", temp=0.5, required=True)
            selected = {
                "source": f"{SITE_NAME} Desk",
                "title": custom_topic,
                "link": f"https://thedailyinsightbangla.blogspot.com/?q={urllib.parse.quote(custom_topic)}",
            }
        else:
            ctx = "RSS Selection"
            candidates = scrape_news(seen_urls)
            if not candidates:
                send_telegram_alert("RSS Scrape", "No new unique items found", ctx)
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
            sel_raw = run_two_ai(sel_prompt, "Select Story", temp=0.55, required=True)
            choice = json.loads(re.search(r"\{.*\}", sel_raw, re.DOTALL).group())
            selected = shortlist[int(choice.get("index", 0))]
            category = choice.get("category") or "Technology"
            img_prompt = choice.get("image_prompt") or selected["title"]
            ctx = selected["title"]

            full_txt = fetch_article_text(selected["link"], selected["summary"])
            res_prompt = (
                "Create a factual research brief from the details below. Never invent claims. "
                "Include core facts, perspectives, simple explanation of new companies/terms, impact, and 2 Q&As.\n"
                f"Title: {selected['title']}\nSource: {selected['source']}\nDetails:\n{full_txt}"
            )
            research = run_two_ai(res_prompt, "Story Research", temp=0.5, required=True)

        write_lines = [
            f"Write a warm, natural, human Bengali article for '{SITE_NAME}'.",
            "Explain any new company, organization, or unfamiliar term in simple Bengali right inside the text.",
            "Keep language clear and accessible; stick strictly to verified facts; never invent statistics.",
            "Use HTML tags (<strong>, <h2>, <blockquote>, <table>, <p>, <ul>), never **markdown** or foreign CJK chars.",
            "First line must be <TITLE>Bengali Headline</TITLE>, followed by clean article HTML only.",
            f"Topic: {selected['title']}",
            f"Category: {category}",
            f"Research:\n{research}",
        ]
        raw_art = run_two_ai("\n".join(write_lines), "Write Article", temp=0.65, required=True)

        t_match = re.search(r"<TITLE>(.*?)</TITLE>", raw_art, re.DOTALL)
        bn_title = (t_match.group(1) if t_match else selected["title"]).strip()
        body_html = re.sub(r"<TITLE>.*?</TITLE>", "", raw_art, flags=re.DOTALL).strip()
        body_html = body_html.replace("```html", "").replace("```", "").strip()
        body_html = re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", body_html)
        body_html = re.sub(r"[\u4e00-\u9fff\u3040-\u30ff]+", "", body_html)
        bn_title = re.sub(r"[\u4e00-\u9fff\u3040-\u30ff]+", "", bn_title).strip()

        img_url = make_image(img_prompt, category)
        badge = (
            f'<div style="margin-bottom:14px;"><span style="background:#eff6ff;color:#1d4ed8;'
            f'padding:4px 12px;border-radius:20px;font-size:12px;font-weight:700;">{category}</span></div>'
        )
        img_box = (
            f'<div style="text-align:center;margin:0 0 24px 0;"><img src="{img_url}" alt="{bn_title}" '
            f'style="width:100%;max-width:800px;border-radius:12px;" /></div>'
        )

        past = [h for h in history if h.get("blog_url") and h.get("bn_title")][-2:]
        related = ""
        if past:
            lis = "".join(
                f'<li><a href="{h["blog_url"]}" style="color:#2563eb;font-weight:600;">{h["bn_title"]}</a></li>'
                for h in reversed(past)
            )
            related = f'<div style="margin-top:24px;"><div><strong>আরও পড়ুন</strong></div><ul>{lis}</ul></div>'

        credit = (
            f'<p style="margin-top:20px;font-size:13px;color:#64748b;">'
            f'তথ্যসূত্র: <a href="{selected["link"]}" target="_blank" rel="noopener noreferrer">{selected["source"]}</a></p>'
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

        final_html = badge + img_box + body_html + related + credit + schema_tag
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
        send_telegram_alert("Pipeline Error", str(e), ctx)
        raise


if __name__ == "__main__":
    main()
        

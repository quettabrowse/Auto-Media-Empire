import concurrent.futures
import io
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
from PIL import Image, ImageDraw, ImageFilter, ImageFont


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

TELEGRAM_API_TOKEN = os.environ.get("TELEGRAM_API_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

FB_PAGE_ACCESS_TOKEN = os.environ.get("FB_PAGE_ACCESS_TOKEN", "").strip()

CUSTOM_TOPIC_ENV = os.environ.get("CUSTOM_TOPIC", "").strip()
GITHUB_REPO = os.environ.get("GITHUB_REPOSITORY", "").strip()

SITE_NAME = "The Daily Insight"
HISTORY_FILE = "posted_history.json"
IMAGES_DIR = "images"
TOPIC_QUEUE_FILE = "topic_queue.txt"

RSS_FEEDS = [
    # Indian News, Science & Tech
    ("Indian Express Tech", "https://indianexpress.com/section/technology/feed/"),
    ("The Hindu Sci-Tech", "https://www.thehindu.com/sci-tech/feeder/default.rss"),
    ("Gadgets 360", "https://www.gadgets360.com/rss/news"),
    ("Indian Express India", "https://indianexpress.com/section/india/feed/"),
    # Hidden & Curious Science / Space
    ("ScienceDaily", "https://www.sciencedaily.com/rss/top/science.xml"),
    ("Live Science", "https://www.livescience.com/feeds/all"),
    ("Space.com", "https://www.space.com/feeds/all"),
    ("Phys.org", "https://phys.org/rss-feed/"),
    # International Tech & General
    ("The Verge", "https://www.theverge.com/rss/index.xml"),
    ("TechCrunch", "https://techcrunch.com/feed/"),
    ("Wired", "https://www.wired.com/feed/rss"),
    ("Ars Technica", "https://feeds.arstechnica.com/arstechnica/index"),
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
    "Science": "https://images.unsplash.com/photo-1507413245164-6160d8298b31?w=1200&q=80",
    "India News": "https://images.unsplash.com/photo-1524492412937-b28074a5d7da?w=1200&q=80",
    "World News": "https://images.unsplash.com/photo-1451187580459-43490279c0fa?w=1200&q=80",
}


def send_telegram_message(text: str) -> None:
    if not TELEGRAM_API_TOKEN or not TELEGRAM_CHAT_ID:
        print("[TELEGRAM_SKIP] Missing TELEGRAM_API_TOKEN or TELEGRAM_CHAT_ID.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_API_TOKEN}/sendMessage"
    for _ in range(2):
        try:
            r = requests.post(
                url,
                json={"chat_id": TELEGRAM_CHAT_ID, "text": text[:3900], "disable_web_page_preview": True},
                timeout=20,
            )
            if r.ok:
                return
        except Exception as e:
            print(f"[TELEGRAM_RETRY] {e}")
        time.sleep(2)


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
    send_telegram_message(msg)


def send_telegram_success(
    bn_title: str,
    category: str,
    post_url: str,
    fb_status: str,
    group_caption: str,
) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    msg = (
        f"✅ PUBLISHED | {SITE_NAME}\n"
        f"Time: {ts}\n"
        f"Category: {category}\n"
        f"Title: {bn_title}\n"
        f"Blog Link: {post_url}\n"
        f"Facebook: {fb_status}\n\n"
        f"📋 ফেসবুক গ্রুপে শেয়ার করার রেডি ক্যাপশন:\n"
        f"{group_caption}\n"
        f"লিংক: {post_url}"
    )
    print("[TELEGRAM_SUCCESS] Sent success report & group caption.")
    send_telegram_message(msg)


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
        send_telegram_alert(
            "Custom Topic Queue",
            f"Queue read failed, falling back to RSS: {e}",
            TOPIC_QUEUE_FILE,
            skipped=True,
        )
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
                clean_s = re.sub(r"\s+", " ", re.sub(r"<[^<]+?>", " ", raw_s)).strip()[:400]
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
            joined = "\n".join(paras)[:4200]
            if len(joined) > 250:
                return joined
    except Exception as e:
        print(f"[FETCH_WARN] {e}")
    return fallback
def process_and_save_image(raw_bytes: bytes, prefix: str = "img") -> Tuple[Optional[str], Optional[bytes]]:
    try:
        im = Image.open(io.BytesIO(raw_bytes))
        im = im.convert("RGB")
        w, h = im.size
        if w < 300 or h < 200:
            return None, None

        # Slight edge trim to clean border/screenshot margin, then smart crop to 16:9
        trim_x = int(w * 0.03)
        trim_y = int(h * 0.03)
        if w - 2 * trim_x > 400 and h - 2 * trim_y > 250:
            im = im.crop((trim_x, trim_y, w - trim_x, h - trim_y))
            w, h = im.size

        target_ratio = 16 / 9
        cur_ratio = w / h
        if cur_ratio > target_ratio:
            new_w = int(h * target_ratio)
            left = max(0, (w - new_w) // 2)
            im = im.crop((left, 0, left + new_w, h))
        elif cur_ratio < target_ratio:
            new_h = int(w / target_ratio)
            top = max(0, (h - new_h) // 2)
            im = im.crop((0, top, w, top + new_h))

        im = im.resize((1200, 675), Image.Resampling.LANCZOS)

        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=88, optimize=True)
        jpg_bytes = buf.getvalue()

        os.makedirs(IMAGES_DIR, exist_ok=True)
        rand_tag = int(time.time() * 1000) % 100000
        fname = f"{prefix}_{int(time.time())}_{rand_tag}.jpg"
        fpath = os.path.join(IMAGES_DIR, fname)
        with open(fpath, "wb") as f:
            f.write(jpg_bytes)

        raw_url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/{IMAGES_DIR}/{fname}" if GITHUB_REPO else None
        return raw_url, jpg_bytes
    except Exception as e:
        print(f"[IMG_PROC_WARN] {e}")
        return None, None


def search_wikimedia_image(query_txt: str) -> Optional[bytes]:
    clean_q = " ".join(re.findall(r"[A-Za-z0-9 ]+", query_txt))[:80].strip()
    if not clean_q:
        return None
    try:
        api_url = (
            "https://commons.wikimedia.org/w/api.php"
            f"?action=query&generator=search&gsrnamespace=6&gsrlimit=4"
            f"&prop=imageinfo&iiprop=url|mime&iiurlwidth=1200&format=json"
            f"&gsrsearch={urllib.parse.quote(clean_q)}"
        )
        r = requests.get(api_url, headers={"User-Agent": f"{SITE_NAME}Bot/1.0"}, timeout=20)
        if r.status_code == 200:
            pages = (r.json().get("query") or {}).get("pages") or {}
            for p in pages.values():
                infos = p.get("imageinfo") or []
                for info in infos:
                    mime = (info.get("mime") or "").lower()
                    img_u = info.get("thumburl") or info.get("url")
                    if "jpeg" in mime or "jpg" in mime or "png" in mime:
                        if img_u:
                            ir = requests.get(img_u, headers={"User-Agent": f"{SITE_NAME}Bot/1.0"}, timeout=25)
                            if ir.status_code == 200 and len(ir.content) > 8000:
                                return ir.content
    except Exception as e:
        print(f"[WIKI_IMG_WARN] {e}")
    return None


def make_post_image(prompt_txt: str, category: str, seed_num: int, article_url: str = "") -> Tuple[str, Optional[bytes]]:
    # 1. Real article og:image (copyright-respecting editorial source image)
    if article_url and "thedailyinsightbangla.blogspot.com" not in article_url:
        try:
            r = requests.get(article_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "html.parser")
                meta_img = soup.find("meta", property="og:image") or soup.find("meta", attrs={"name": "twitter:image"})
                if meta_img and meta_img.get("content"):
                    img_src = urllib.parse.urljoin(article_url, meta_img["content"].strip())
                    ir = requests.get(img_src, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
                    if ir.status_code == 200 and len(ir.content) > 5000:
                        raw_url, jpg_bytes = process_and_save_image(ir.content, prefix="news")
                        if raw_url and jpg_bytes:
                            print("[IMG_OK] Real news image processed & saved.")
                            return raw_url, jpg_bytes
        except Exception as e:
            print(f"[IMG_WARN] og:image failed: {e}")

    # 2. Free open-license Wikimedia Commons search
    wiki_bytes = search_wikimedia_image(prompt_txt)
    if wiki_bytes:
        raw_url, jpg_bytes = process_and_save_image(wiki_bytes, prefix="wiki")
        if raw_url and jpg_bytes:
            print("[IMG_OK] Wikimedia Commons image processed & saved.")
            return raw_url, jpg_bytes

    # 3. AI backup (Pollinations)
    try:
        short_p = urllib.parse.quote(f"{prompt_txt}, clean editorial visual, no text, no watermark"[:200])
        p_url = f"https://image.pollinations.ai/prompt/{short_p}?width=1200&height=675&nologo=true&seed={seed_num}"
        r = requests.get(p_url, timeout=45)
        if r.status_code == 200 and len(r.content) > 5000:
            raw_url, jpg_bytes = process_and_save_image(r.content, prefix="ai")
            if raw_url and jpg_bytes:
                print("[IMG_OK] Pollinations backup image processed & saved.")
                return raw_url, jpg_bytes
    except Exception as e:
        print(f"[IMG_WARN] Pollinations failed: {e}")

    # 4. Category fallback image + Telegram skip alert
    fb_url = FALLBACK_IMAGES.get(category, FALLBACK_IMAGES["Technology"])
    try:
        fr = requests.get(fb_url, timeout=20)
        if fr.status_code == 200 and len(fr.content) > 5000:
            raw_url, jpg_bytes = process_and_save_image(fr.content, prefix="fbk")
            send_telegram_alert("Image Generation", "Primary & AI image sources failed; used category fallback.", category, skipped=True)
            return raw_url or fb_url, jpg_bytes
    except Exception:
        pass

    send_telegram_alert("Image Generation", "All image sources failed; using fallback URL.", category, skipped=True)
    return fb_url, None


def build_fb_thumbnail_bytes(base_jpg: Optional[bytes], category: str) -> Optional[bytes]:
    if not base_jpg:
        return None
    try:
        im = Image.open(io.BytesIO(base_jpg)).convert("RGBA").resize((1200, 675), Image.Resampling.LANCZOS)
        overlay = Image.new("RGBA", im.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        # Bottom dark banner for clear visual cue to check first comment
        draw.rectangle([0, 565, 1200, 675], fill=(15, 23, 42, 225))
        draw.rectangle([0, 565, 1200, 571], fill=(56, 189, 248, 255))

        # Top-left clean site badge
        draw.rounded_rectangle([28, 24, 290, 66], radius=12, fill=(15, 23, 42, 200))
        font = ImageFont.load_default()
        draw.text((44, 38), f"{SITE_NAME} | {category}", fill=(255, 255, 255, 255), font=font)
        draw.text((36, 605), "DETAILS & LINK IN FIRST COMMENT  -->  READ MORE", fill=(255, 255, 255, 255), font=font)

        combined = Image.alpha_composite(im, overlay).convert("RGB")
        out_buf = io.BytesIO()
        combined.save(out_buf, format="JPEG", quality=90, optimize=True)
        return out_buf.getvalue()
    except Exception as e:
        print(f"[FB_THUMB_WARN] {e}")
        return base_jpg


def share_fb_photo_and_comment(
    fb_caption: str,
    post_url: str,
    thumb_bytes: Optional[bytes],
    fallback_img_url: str,
) -> str:
    if not FB_PAGE_ACCESS_TOKEN:
        send_telegram_alert("Facebook Share", "FB_PAGE_ACCESS_TOKEN missing; step skipped.", post_url, skipped=True)
        return "Skipped (Missing Token)"

    photo_endpoint = "https://graph.facebook.com/v21.0/me/photos"
    post_id: Optional[str] = None
    last_err = ""

    for attempt in range(1, 3):
        try:
            if thumb_bytes:
                r = requests.post(
                    photo_endpoint,
                    data={"message": fb_caption[:1800], "access_token": FB_PAGE_ACCESS_TOKEN},
                    files={"source": ("thumbnail.jpg", thumb_bytes, "image/jpeg")},
                    timeout=45,
                )
            else:
                r = requests.post(
                    photo_endpoint,
                    data={"message": fb_caption[:1800], "url": fallback_img_url, "access_token": FB_PAGE_ACCESS_TOKEN},
                    timeout=45,
                )
            if r.status_code == 200:
                data = r.json()
                post_id = data.get("post_id") or data.get("id")
                print(f"[FB_OK] Photo post published: {post_id}")
                break
            last_err = f"Attempt {attempt} HTTP {r.status_code}: {r.text[:250]}"
        except Exception as e:
            last_err = f"Attempt {attempt} Exception: {e}"
        time.sleep(2)

    if not post_id:
        send_telegram_alert("Facebook Photo Post", f"All retries failed; skipped. {last_err}", post_url, skipped=True)
        return f"Skipped ({last_err[:80]})"

    # First comment with blog link
    comment_txt = f"বিস্তারিত পড়ুন এখানে: {post_url}\nআপনার মতামত কী? নিচে কমেন্টে জানান!"
    c_endpoint = f"https://graph.facebook.com/v21.0/{post_id}/comments"
    try:
        cr = requests.post(
            c_endpoint,
            data={"message": comment_txt[:1500], "access_token": FB_PAGE_ACCESS_TOKEN},
            timeout=25,
        )
        if cr.status_code == 200:
            print("[FB_COMMENT_OK] First comment added.")
            return "Published (Photo + First Comment Link)"
        send_telegram_alert(
            "Facebook First Comment",
            f"Photo posted, first comment skipped (HTTP {cr.status_code}: {cr.text[:200]}).",
            post_url,
            skipped=True,
        )
        return "Published (Photo Only - Comment Skipped)"
    except Exception as e:
        send_telegram_alert("Facebook First Comment", f"First comment failed; skipped: {e}", post_url, skipped=True)
        return "Published (Photo Only - Comment Skipped)"
def main() -> None:
    ctx = "Init"
    try:
        history = load_history()
        seen_urls = {x.get("source_url") for x in history if x.get("source_url")}
        recent_titles = [x.get("bn_title", "") for x in history[-12:] if x.get("bn_title")]
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
                f'Pick best category from [Technology, Science, India News, World News] for "{custom_topic}". '
                'Give clean English image_query (2-5 words, no text) for image search/generation. '
                'JSON only: {"category":"Technology","image_query":"..."}'
            )
            cat_raw = run_parallel_two_ai(cat_prompt, "Custom Category", temp=0.4, required=False)
            category = "Technology"
            img_query = custom_topic
            if cat_raw:
                m = re.search(r"\{.*\}", cat_raw, re.DOTALL)
                if m:
                    try:
                        obj = json.loads(m.group())
                        category = obj.get("category") or "Technology"
                        img_query = obj.get("image_query") or custom_topic
                    except Exception:
                        pass

            res_prompt = (
                f"Research '{custom_topic}' thoroughly for '{SITE_NAME}'. "
                "Include ONLY verified facts; never invent statistics, dates, or fake claims. "
                "Explain new terms/organizations clearly, give real-life examples, key takeaways, and 2 Q&As.\n"
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
                send_telegram_alert("RSS Scrape", "No new unseen items; using backup recent pool.", ctx, skipped=True)
                candidates = scrape_news(set())[:12]
                if not candidates:
                    send_telegram_alert("RSS Scrape", "All RSS feeds failed; run skipped.", ctx, skipped=True)
                    return

            shortlist = candidates[:30]
            listed = "\n".join([f"{i}. [{a['source']}] {a['title']}" for i, a in enumerate(shortlist)])
            avoid_txt = "\n".join([f"- {t}" for t in recent_titles]) if recent_titles else "None"
            favor_india = (len(history) % 2 == 0)

            sel_prompt = (
                f"Chief editor for '{SITE_NAME}'. Pick a curious, engaging story from the list that sparks reader interest "
                "(such as hidden/surprising science, space, tech, or important stories). "
                f"{'Give special priority to strong Indian news or stories relevant to India when available. ' if favor_india else ''}"
                f"Avoid topics similar to:\n{avoid_txt}\n"
                f"Last category was '{last_cat}'. Prefer variety.\n"
                f"Items:\n{listed}\n"
                'Return JSON only: {"index":0,"category":"Science","image_query":"clean 3-4 word visual subject"}'
            )
            sel_raw = run_parallel_two_ai(sel_prompt, "Select Story", temp=0.55, required=True)
            choice = json.loads(re.search(r"\{.*\}", sel_raw, re.DOTALL).group())
            selected = shortlist[int(choice.get("index", 0)) % len(shortlist)]
            category = choice.get("category") or "Technology"
            img_query = choice.get("image_query") or selected["title"]
            ctx = selected["title"]

            full_txt = fetch_article_text(selected["link"], selected["summary"])
            res_prompt = (
                "Create a detailed, accurate research brief from the details below. Never invent facts or numbers. "
                "Include core facts, easy everyday analogies, plain explanation of new terms/organizations, real impact, and 2 Q&As.\n"
                f"Title: {selected['title']}\nSource: {selected['source']}\nDetails:\n{full_txt}"
            )
            research = run_parallel_two_ai(res_prompt, "Story Research", temp=0.5, prefer_merge=True, required=True)

        write_lines = [
            f"Write a rich, detailed, warm, and easy-to-understand Bengali article for '{SITE_NAME}'.",
            "LANGUAGE & AUDIENCE RULE: Write in natural, clear Bengali with a smooth Indian Bengali editorial flow, yet completely neutral and accessible so both readers in India and Bangladesh can read effortlessly without noticing regional slang or alien local words.",
            "TERM FORMAT RULE: Whenever a main, hard, or unfamiliar technical term, concept, or organization appears, write Bengali immediately followed by English in brackets with NO extra brackets or space, like: কৃত্রিম বুদ্ধিমত্তা(Artificial Intelligence) or মহাকাশ দূরবীন(Space Telescope), and explain it simply with everyday examples.",
            "Do NOT include reading time at the start.",
            "Stick strictly to verified facts; never invent numbers, prices, or fake claims.",
            "Use clean HTML tags (<p>, <h2>, <blockquote>, <table>, <ul>, <strong>), never **markdown** or CJK chars.",
            "Structure: (1) Summary box titled 'এক নজরে মূল বিষয়', (2) Detailed, well-explained H2 sections with everyday examples, (3) One insightful blockquote, (4) A clean table if helpful, (5) 'দ্রুত প্রশ্নোত্তর' with 2 Q&As, (6) Near the very end, a box titled '💡 আপনি কি জানতেন?' with one surprising related fact.",
            "First line MUST be <TITLE>Curiosity-driven Bengali Headline</TITLE>, followed by clean article HTML only.",
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

        # Deep Black normal text + Rich Color Combination
        body_html = re.sub(r"<p(?![^>]*style=)", '<p style="color:#09090b;line-height:1.85;font-size:16px;margin:0 0 14px 0;"', body_html)
        body_html = re.sub(r"<li(?![^>]*style=)", '<li style="color:#09090b;line-height:1.8;font-size:15.5px;margin-bottom:6px;"', body_html)
        body_html = re.sub(r"<h2(?![^>]*style=)", '<h2 style="color:#1e3a8a;font-weight:700;font-size:21px;margin:26px 0 12px;padding-bottom:6px;border-bottom:2px solid #bfdbfe;"', body_html)
        body_html = re.sub(r"<blockquote(?![^>]*style=)", '<blockquote style="background:#f0fdfa;border-left:4px solid #0d9488;padding:15px 18px;margin:20px 0;border-radius:0 8px 8px 0;color:#09090b;font-style:italic;font-size:16.5px;"', body_html)
        body_html = re.sub(r"<table(?![^>]*style=)", '<table style="width:100%;border-collapse:collapse;margin:18px 0;font-size:15px;color:#09090b;"', body_html)
        body_html = re.sub(r"<th(?![^>]*style=)", '<th style="background:#1e3a8a;color:#ffffff;padding:10px;border:1px solid #cbd5e1;text-align:left;"', body_html)
        body_html = re.sub(r"<td(?![^>]*style=)", '<td style="padding:10px;border:1px solid #cbd5e1;color:#09090b;background:#ffffff;"', body_html)

        img_url, base_jpg_bytes = make_post_image(img_query, category, seed_num, selected["link"])
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
            '<p style="margin-top:22px;font-size:13.5px;color:#09090b;border-top:1px solid #e2e8f0;padding-top:12px;">'
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

        # Generate curious FB hook caption + group caption via AI (with safe fallback)
        cap_prompt = (
            "Write two short Bengali captions for a news post. "
            "1) 'fb_post_caption': 2-3 curious lines with a hook, an engaging question for comments, "
            "and a clear line saying 'বিস্তারিত পড়ুন প্রথম কমেন্টে দেওয়া লিংকে 👇', plus 3 hashtags. "
            "2) 'group_caption': 2 engaging lines suitable for sharing in Bengali Facebook groups. "
            f"Headline: {bn_title}\nCategory: {category}\n"
            'Return JSON only: {"fb_post_caption":"...","group_caption":"..."}'
        )
        cap_raw = run_parallel_two_ai(cap_prompt, "Social Captions", temp=0.7, required=False)
        fb_caption = (
            f"{bn_title}\n\n"
            "বিষয়টি নিয়ে আপনার কী মতামত? নিচে কমেন্টে জানান!\n"
            "বিস্তারিত পড়ুন প্রথম কমেন্টে দেওয়া লিংকে 👇\n"
            f"#{SITE_NAME.replace(' ', '')} #{category.replace(' ', '')} #BanglaNews"
        )
        group_caption = f"{bn_title} — বিষয়টি সম্পর্কে জানলে অবাক হবেন! আপনার মতামত কী?"

        if cap_raw:
            m = re.search(r"\{.*\}", cap_raw, re.DOTALL)
            if m:
                try:
                    c_obj = json.loads(m.group())
                    if c_obj.get("fb_post_caption"):
                        fb_caption = str(c_obj["fb_post_caption"]).strip()
                    if c_obj.get("group_caption"):
                        group_caption = str(c_obj["group_caption"]).strip()
                except Exception:
                    pass

        thumb_bytes = build_fb_thumbnail_bytes(base_jpg_bytes, category)
        fb_status = share_fb_photo_and_comment(fb_caption, post_url, thumb_bytes, img_url)

        send_telegram_success(bn_title, category, post_url, fb_status, group_caption)

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
                            

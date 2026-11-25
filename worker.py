import os
import re
import json
import random
import hashlib
import datetime
import feedparser
import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO

# ==========================================
# CONFIGURATION & ENVIRONMENT VARIABLES
# ==========================================
GROQ_API_KEYS = [
    k.strip() for k in [
        os.environ.get("GROQ_API_KEY", ""),
        os.environ.get("GROQ_API_KEY_2", ""),
        os.environ.get("GROQ_API_KEY_3", ""),
    ] if k.strip()
]

GEMINI_API_KEYS = [
    k.strip() for k in [
        os.environ.get("GEMINI_API_KEY", ""),
        os.environ.get("GEMINI_API_KEY_2", ""),
        os.environ.get("GEMINI_API_KEY_3", ""),
    ] if k.strip()
]

GEMINI_MODELS = [
    "gemini-4-hydrogen",
    "gemini-3.8-flash",
    "gemini-3.5-flash-lite",
    "gemini-2.5-flash",
]

GROQ_MODELS = [
    "openai/gpt-oss-120b",
    "llama-3.3-70b-versatile",
]

BLOGGER_CLIENT_ID = os.environ.get("BLOGGER_CLIENT_ID", "").strip()
BLOGGER_CLIENT_SECRET = os.environ.get("BLOGGER_CLIENT_SECRET", "").strip()
BLOGGER_REFRESH_TOKEN = os.environ.get("BLOGGER_REFRESH_TOKEN", "").strip()
BLOGGER_BLOG_ID = os.environ.get("BLOGGER_BLOG_ID", "").strip()

FB_PAGE_ID = os.environ.get("FB_PAGE_ID", "").strip()
FB_PAGE_ACCESS_TOKEN = os.environ.get("FB_PAGE_ACCESS_TOKEN", "").strip()

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

STATE_FILE = "state/posted_history.json"
IMAGE_DIR = "assets/images"

RSS_FEEDS = [
    ("Technology", "https://www.thehindu.com/sci-tech/technology/feeder/default.rss"),
    ("Science", "https://www.thehindu.com/sci-tech/science/feeder/default.rss"),
    ("Gadgets", "https://gadgets360.com/rss/news"),
    ("India", "https://indianexpress.com/section/india/feed/"),
    ("World", "https://indianexpress.com/section/world/feed/"),
]

# ==========================================
# LOGGING & TELEGRAM HELPERS
# ==========================================
def now_utc_str():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

def send_telegram_message(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[TELEGRAM] Token or Chat ID missing; skipped.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "disable_web_page_preview": False,
    }
    try:
        r = requests.post(url, json=payload, timeout=20)
        if r.status_code == 200:
            return True
        print(f"[TELEGRAM_ERR] HTTP {r.status_code}: {r.text}")
        return False
    except Exception as e:
        print(f"[TELEGRAM_ERR] {e}")
        return False

def notify_step_status(status_icon, status_label, step, context="", info=""):
    msg = (
        f"{status_icon} {status_label} | The Daily Insight\n"
        f"Time: {now_utc_str()}\n"
        f"Step: {step}\n"
    )
    if context:
        msg += f"Context: {context}\n"
    if info:
        msg += f"Info: {info}\n"
    print(msg.strip())
    send_telegram_message(msg.strip())
# ==========================================
# STATE MANAGEMENT
# ==========================================
def load_history():
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception:
            pass
    return []

def save_history(history):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(history[-300:], f, ensure_ascii=False, indent=2)

def url_hash(url):
    return hashlib.sha256(url.strip().encode("utf-8")).hexdigest()[:16]

# ==========================================
# AI CLIENTS (GROQ + GEMINI 4 HYDROGEN FIRST)
# ==========================================
def call_groq(system_prompt, user_prompt, temperature=0.65):
    for idx, api_key in enumerate(GROQ_API_KEYS, start=1):
        for model in GROQ_MODELS:
            try:
                resp = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "temperature": temperature,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                    },
                    timeout=45,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    content = data["choices"][0]["message"]["content"].strip()
                    if content:
                        print(f"[AI_OK] Groq ({model}, Key #{idx})")
                        return content
                else:
                    print(f"[AI_WARN] Groq ({model}, Key #{idx}) HTTP {resp.status_code}")
            except Exception as e:
                print(f"[AI_WARN] Groq ({model}, Key #{idx}) error: {e}")
    return None

def call_gemini(system_prompt, user_prompt, temperature=0.65):
    for idx, api_key in enumerate(GEMINI_API_KEYS, start=1):
        for model in GEMINI_MODELS:
            endpoint = (
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{model}:generateContent?key={api_key}"
            )
            payload = {
                "system_instruction": {"parts": [{"text": system_prompt}]},
                "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
                "generationConfig": {"temperature": temperature},
            }
            try:
                resp = requests.post(endpoint, json=payload, timeout=50)
                if resp.status_code == 200:
                    data = resp.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        text = "".join(p.get("text", "") for p in parts).strip()
                        if text:
                            print(f"[AI_OK] Gemini ({model}, Key #{idx})")
                            return text
                else:
                    print(f"[AI_WARN] Gemini ({model}, Key #{idx}) HTTP {resp.status_code}")
            except Exception as e:
                print(f"[AI_WARN] Gemini ({model}, Key #{idx}) error: {e}")
    return None

def two_ai_pipeline(role_prompt, style_rules, negative_rules, example_text, task_instruction, source_text):
    system_prompt = (
        f"1. Role (তুমি কে): {role_prompt}\n"
        f"2. Style (কেমন করে লিখবে): {style_rules}\n"
        f"3. না-করার নিয়ম (কী করবে না): {negative_rules}\n"
        f"4. Example (এক লাইন কেমন হবে): {example_text}"
    )
    stage1_user = (
        f"{task_instruction}\n\n"
        f"প্রথম ধাপ: মূল তথ্যগুলো যাচাই করে একটি পরিষ্কার, সহজ ও স্বাভাবিক বাংলা খসড়া তৈরি করো।\n\n"
        f"Raw Source:\n{source_text}"
    )
    draft = call_groq(system_prompt, stage1_user, temperature=0.6)
    if not draft:
        draft = call_gemini(system_prompt, stage1_user, temperature=0.6)
    if not draft:
        return None

    stage2_user = (
        f"{task_instruction}\n\n"
        f"দ্বিতীয় ধাপ: নিচের খসড়াটিকে আরও মানবিক, সাবলীল, ঝরঝরে ও নির্ভুল বাংলায় চূড়ান্ত রূপ দাও।\n\n"
        f"Draft:\n{draft}"
    )
    final_out = call_gemini(system_prompt, stage2_user, temperature=0.65)
    if not final_out:
        final_out = call_groq(system_prompt, stage2_user, temperature=0.65)
    return final_out or draft

# ==========================================
# NEWS FETCHING & IMAGE EXTRACTION
# ==========================================
def clean_html_text(html_str):
    soup = BeautifulSoup(html_str or "", "html.parser")
    return " ".join(soup.get_text(" ").split())

def extract_article_details(entry, category):
    link = (entry.get("link") or "").strip()
    title = clean_html_text(entry.get("title") or "")
    summary = clean_html_text(entry.get("summary") or entry.get("description") or "")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Linux; Android 13; Mobile) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36"
        )
    }
    article_text = summary
    og_image = ""

    if link:
        try:
            r = requests.get(link, headers=headers, timeout=20)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "html.parser")
                meta_img = soup.find("meta", property="og:image") or soup.find("meta", attrs={"name": "twitter:image"})
                if meta_img and meta_img.get("content"):
                    og_image = meta_img["content"].strip()

                paragraphs = [
                    " ".join(p.get_text(" ").split())
                    for p in soup.find_all("p")
                    if len(" ".join(p.get_text(" ").split())) > 45
                ]
                if paragraphs:
                    article_text = "\n".join(paragraphs[:12])
        except Exception as e:
            print(f"[FETCH_WARN] Could not scrape full article ({link}): {e}")

    return {
        "category": category,
        "title": title,
        "link": link,
        "summary": summary,
        "text": article_text[:4000],
        "image_url": og_image,
    }

def pick_fresh_news(history):
    seen_hashes = {item.get("hash") for item in history if isinstance(item, dict)}
    feeds_shuffled = RSS_FEEDS[:]
    random.shuffle(feeds_shuffled)

    for category, feed_url in feeds_shuffled:
        try:
            parsed = feedparser.parse(feed_url)
            entries = parsed.entries[:8] if parsed and parsed.entries else []
            for entry in entries:
                link = (entry.get("link") or "").strip()
                if not link:
                    continue
                h = url_hash(link)
                if h in seen_hashes:
                    continue
                details = extract_article_details(entry, category)
                details["hash"] = h
                return details
        except Exception as e:
            print(f"[RSS_WARN] Feed error ({feed_url}): {e}")
    return None
# ==========================================
# CONTENT GENERATION (TITLE + ARTICLE + DID YOU KNOW)
# ==========================================
def strip_code_fences(text):
    text = re.sub(r"^```(?:html|json|markdown)?\s*", "", text.strip(), flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text.strip())
    return text.strip()

def generate_bengali_article(news):
    role_prompt = (
        "তুমি 'The Daily Insight' বাংলা নিউজ পোর্টালের অভিজ্ঞ ও মানবিক সাংবাদিক। "
        "তুমি ভারত ও বাংলাদেশ—উভয় অঞ্চলের সাধারণ পাঠকের জন্য খবর ও বিজ্ঞান-প্রযুক্তির বিষয় সহজভাবে ব্যাখ্যা করো।"
    )
    style_rules = (
        "লেখা হবে সাবলীল, মানবিক, প্রাঞ্জল ও গল্পের মতো সহজ বাংলায়। "
        "জটিল বিষয় দৈনন্দিন জীবনের সাধারণ উদাহরণ দিয়ে বোঝাবে। "
        "নতুন বা কঠিন ইংরেজি/প্রযুক্তি শব্দ এলে 'বাংলা(English)' ফরম্যাট ব্যবহার করবে, যেমন: কৃত্রিম বুদ্ধিমত্তা(Artificial Intelligence)। "
        "বড় অনুচ্ছেদ না লিখে পরিষ্কার সাব-হেডিং ও বুলেট পয়েন্ট রাখবে।"
    )
    negative_rules = (
        "আড়ষ্ট, যান্ত্রিক বা আক্ষরিক শব্দ-অনুবাদ (যেমন 'এক-উপর-অন্যান্যের' বা জড়ানো ইংরেজি-বাংলা মিশ্রণ) একদম ব্যবহার করবে না। "
        "শিরোনাম খুব লম্বা বা সরকারি রিপোর্টের মতো গম্ভীর করবে না। "
        "HTML কোডের বাইরে কোনো বাড়তি কথা বা ```html ব্লক দেবে না।"
    )
    example_text = "কেন বদলে যাচ্ছে কৃত্রিম বুদ্ধিমত্তা(Artificial Intelligence)-এর নিয়ম? সহজ কারণগুলো জেনে নিন"

    task_instruction = (
        "নিচের খবরের তথ্যের ভিত্তিতে একটি সম্পূর্ণ বাংলা প্রতিবেদন তৈরি করো। "
        "আউটপুট অবশ্যই নিচের নির্দিষ্ট ফরম্যাটে হতে হবে:\n"
        "TITLE: <ছোট, আকর্ষণীয় ও কৌতূহল জাগানো বাংলা শিরোনাম>\n"
        "BODY_HTML:\n"
        "<পঠনযোগ্য HTML অনুচ্ছেদ, সাব-হেডিং ও বুলেট পয়েন্ট>\n"
        "<div style='background:#f0f9ff;border-left:4px solid #0284c7;padding:14px 16px;margin-top:20px;border-radius:6px;'>"
        "<strong>💡 আপনি কি জানতেন?</strong><br>"
        "<বিষয়টির সাথে সম্পর্কিত একটি মজার ও সত্য তথ্য>"
        "</div>"
    )

    source_blob = (
        f"Category: {news['category']}\n"
        f"Original Headline: {news['title']}\n"
        f"Source URL: {news['link']}\n"
        f"Details:\n{news['text']}"
    )

    raw_output = two_ai_pipeline(
        role_prompt=role_prompt,
        style_rules=style_rules,
        negative_rules=negative_rules,
        example_text=example_text,
        task_instruction=task_instruction,
        source_text=source_blob,
    )
    if not raw_output:
        return None

    cleaned = strip_code_fences(raw_output)
    title_match = re.search(r"(?im)^TITLE:\s*(.+)$", cleaned)
    body_match = re.search(r"(?is)BODY_HTML:\s*(.+)$", cleaned)

    title = title_match.group(1).strip() if title_match else ""
    body_html = body_match.group(1).strip() if body_match else ""

    title = re.sub(r"^[\"']|[\"']$", "", title).strip()
    body_html = strip_code_fences(body_html)

    if not title:
        title = news["title"] or "আজকের বিশেষ প্রতিবেদন"
    if not body_html:
        body_html = f"<p>{news['summary'] or news['text']}</p>"

    if "আপনি কি জানতেন" not in body_html:
        body_html += (
            "\n<div style='background:#f0f9ff;border-left:4px solid #0284c7;padding:14px 16px;margin-top:20px;border-radius:6px;'>"
            "<strong>💡 আপনি কি জানতেন?</strong><br>"
            "সঠিক তথ্য যাচাই করে পড়লে জটিল প্রযুক্তি ও বৈশ্বিক খবরও সহজে বোঝা যায়।"
            "</div>"
        )

    return {"title": title, "body_html": body_html}

# ==========================================
# IMAGE PROCESSING & FB BANNER THUMBNAIL
# ==========================================
def find_font(size, bold=False):
    candidates = [
        "/usr/share/fonts/truetype/noto/NotoSansBengali-Bold.ttf" if bold else "/usr/share/fonts/truetype/noto/NotoSansBengali-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return ImageFont.load_default()

def build_images(news, title_text, post_hash):
    os.makedirs(IMAGE_DIR, exist_ok=True)
    blog_img_path = os.path.join(IMAGE_DIR, f"{post_hash}_blog.jpg")
    fb_img_path = os.path.join(IMAGE_DIR, f"{post_hash}_fb.jpg")

    img = None
    if news.get("image_url"):
        try:
            r = requests.get(news["image_url"], timeout=20)
            if r.status_code == 200:
                candidate = Image.open(BytesIO(r.content)).convert("RGB")
                if candidate.width >= 300 and candidate.height >= 180:
                    img = candidate
        except Exception as e:
            print(f"[IMG_WARN] Original image download failed: {e}")

    if img is None:
        img = Image.new("RGB", (1200, 675), (15, 23, 42))
        draw = ImageDraw.Draw(img)
        draw.rectangle([0, 0, 1200, 675], fill=(15, 23, 42))
        f_brand = find_font(44, bold=True)
        draw.text((60, 60), "The Daily Insight", fill=(125, 211, 252), font=f_brand)

    target_w, target_h = 1200, 675
    src_ratio = img.width / img.height
    dst_ratio = target_w / target_h
    if src_ratio > dst_ratio:
        new_w = int(img.height * dst_ratio)
        offset_x = (img.width - new_w) // 2
        cropped = img.crop((offset_x, 0, offset_x + new_w, img.height))
    else:
        new_h = int(img.width / dst_ratio)
        offset_y = (img.height - new_h) // 2
        cropped = img.crop((0, offset_y, img.width, offset_y + new_h))

    blog_img = cropped.resize((target_w, target_h), Image.Resampling.LANCZOS)
    blog_img.save(blog_img_path, "JPEG", quality=90)

    fb_img = blog_img.copy()
    banner_h = 84
    overlay = Image.new("RGBA", (target_w, banner_h), (10, 25, 47, 235))
    fb_rgba = fb_img.convert("RGBA")
    fb_rgba.paste(overlay, (0, target_h - banner_h), overlay)
    fb_final = fb_rgba.convert("RGB")

    draw_fb = ImageDraw.Draw(fb_final)
    banner_font = find_font(34, bold=True)
    banner_text = "বিস্তারিত লিংক প্রথম কমেন্টে 👇"
    bbox = draw_fb.textbbox((0, 0), banner_text, font=banner_font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    tx = (target_w - text_w) // 2
    ty = (target_h - banner_h) + (banner_h - text_h) // 2 - 2
    draw_fb.text((tx, ty), banner_text, fill=(255, 255, 255), font=banner_font)

    fb_final.save(fb_img_path, "JPEG", quality=90)
    print("[IMG_OK] Real news image processed & saved.")
    return blog_img_path, fb_img_path
# ==========================================
# BLOGGER PUBLISHING
# ==========================================
def get_blogger_access_token():
    if not (BLOGGER_CLIENT_ID and BLOGGER_CLIENT_SECRET and BLOGGER_REFRESH_TOKEN):
        return None
    r = requests.post(
        "[https://oauth2.googleapis.com/token](https://oauth2.googleapis.com/token)",
        data={
            "client_id": BLOGGER_CLIENT_ID,
            "client_secret": BLOGGER_CLIENT_SECRET,
            "refresh_token": BLOGGER_REFRESH_TOKEN,
            "grant_type": "refresh_token",
        },
        timeout=25,
    )
    if r.status_code == 200:
        return r.json().get("access_token")
    raise RuntimeError(f"Blogger OAuth failed ({r.status_code}): {r.text}")

def publish_to_blogger(title, body_html, category, source_url):
    token = get_blogger_access_token()
    if not token or not BLOGGER_BLOG_ID:
        raise RuntimeError("Blogger credentials or BLOGGER_BLOG_ID missing.")

    full_content = (
        f"{body_html}\n"
        f"<p style='margin-top:18px;font-size:14px;color:#475569;'>"
        f"সূত্র: <a href='{source_url}' target='_blank' rel='nofollow noopener'>{source_url}</a></p>"
    )
    url = f"[https://www.googleapis.com/blogger/v3/blogs/](https://www.googleapis.com/blogger/v3/blogs/){BLOGGER_BLOG_ID}/posts/"
    resp = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={
            "kind": "blogger#post",
            "title": title,
            "content": full_content,
            "labels": ["The Daily Insight", category],
        },
        timeout=30,
    )
    if resp.status_code in (200, 201):
        data = resp.json()
        return data.get("url", ""), data.get("id", "")
    raise RuntimeError(f"Blogger publish failed ({resp.status_code}): {resp.text}")

# ==========================================
# FACEBOOK PHOTO POST + FIRST COMMENT + CAPTION
# ==========================================
def generate_fb_caption_and_group_text(title, body_html, post_url, category):
    role_prompt = (
        "তুমি 'The Daily Insight' ফেসবুক পেজের সোশ্যাল মিডিয়া এডিটর।"
    )
    style_rules = (
        "ছোট, কৌতূহল জাগানো বাংলা হুক ও ১টি সহজ প্রশ্ন রাখবে যাতে মানুষ কমেন্টে মতামত দেয়। "
        "শেষে উল্লেখ করবে যে বিস্তারিত লিংক প্রথম কমেন্টে দেওয়া আছে। "
        "৩ থেকে ৫টি প্রাসঙ্গিক হ্যাশট্যাগ দেবে।"
    )
    negative_rules = (
        "আড়ষ্ট অনুবাদ (যেমন 'এক-উপর-অন্যান্যের') ব্যবহার করবে না। মূল পোস্টে সরাসরি লিংক বসাবে না।"
    )
    example_text = (
        "প্রযুক্তির এই নতুন বদল কি সবার জন্য সমান সুযোগ আনবে? 🤔\n"
        "আপনার মতে কোন বিষয়টি সবচেয়ে জরুরি? কমেন্টে জানান! 👇\n"
        "বিস্তারিত লিংক প্রথম কমেন্টে দেওয়া হলো।\n"
        "#TheDailyInsight #BanglaNews #TechBangla"
    )

    plain_summary = clean_html_text(body_html)[:450]
    out = two_ai_pipeline(
        role_prompt=role_prompt,
        style_rules=style_rules,
        negative_rules=negative_rules,
        example_text=example_text,
        task_instruction="নিচের তথ্যের ভিত্তিতে ফেসবুক পোস্টের ক্যাপশন তৈরি করো।",
        source_text=f"Title: {title}\nCategory: {category}\nSummary: {plain_summary}",
    )

    fb_caption = (out or example_text).strip()
    group_caption = (
        f"{title}\n\n"
        f"{plain_summary[:220]}...\n\n"
        f"আপনার মতামত কী? কমেন্টে জানান! 👇\n"
        f"লিংক: {post_url}"
    )
    return fb_caption, group_caption

def publish_fb_photo_and_comment(fb_img_path, caption, post_url):
    if not FB_PAGE_ID or not FB_PAGE_ACCESS_TOKEN:
        return None, "FB_PAGE_ID or FB_PAGE_ACCESS_TOKEN missing"

    photo_endpoint = f"[https://graph.facebook.com/v27.0/](https://graph.facebook.com/v27.0/){FB_PAGE_ID}/photos"
    with open(fb_img_path, "rb") as img_file:
        resp = requests.post(
            photo_endpoint,
            data={
                "caption": caption,
                "access_token": FB_PAGE_ACCESS_TOKEN,
            },
            files={"source": img_file},
            timeout=40,
        )

    if resp.status_code not in (200, 201):
        raise RuntimeError(f"FB photo upload failed ({resp.status_code}): {resp.text}")

    photo_data = resp.json()
    target_id = photo_data.get("post_id") or photo_data.get("id")
    print(f"[FB_OK] Photo post published: {target_id}")

    comment_text = f"বিস্তারিত পড়ুন এখানে 👇\n{post_url}"
    comment_endpoint = f"[https://graph.facebook.com/v27.0/](https://graph.facebook.com/v27.0/){target_id}/comments"
    c_resp = requests.post(
        comment_endpoint,
        data={
            "message": comment_text,
            "access_token": FB_PAGE_ACCESS_TOKEN,
        },
        timeout=25,
    )
    if c_resp.status_code in (200, 201):
        return target_id, None
    return target_id, f"HTTP {c_resp.status_code}: {c_resp.text}"

# ==========================================
# MAIN EXECUTION
# ==========================================
def main():
    history = load_history()
    news = pick_fresh_news(history)
    if not news:
        notify_step_status("⏭️", "SKIPPED", "Pick Fresh News", info="No new unseen news item found.")
        return

    article = generate_bengali_article(news)
    if not article:
        notify_step_status("❌", "FAILED", "Generate Bengali Article", context=news["title"], info="Both Groq and Gemini failed.")
        return

    title = article["title"]
    body_html = article["body_html"]

    blog_img_path, fb_img_path = build_images(news, title, news["hash"])

    post_url, post_id = publish_to_blogger(title, body_html, news["category"], news["link"])
    print(f"Published:\n{post_url}")

    fb_caption, group_caption = generate_fb_caption_and_group_text(
        title=title,
        body_html=body_html,
        post_url=post_url,
        category=news["category"],
    )

    fb_status_text = "Not configured"
    if FB_PAGE_ID and FB_PAGE_ACCESS_TOKEN:
        try:
            fb_post_id, comment_err = publish_fb_photo_and_comment(fb_img_path, fb_caption, post_url)
            if not comment_err:
                fb_status_text = f"Published (Photo + First Comment: {fb_post_id})"
            else:
                fb_status_text = "Published (Photo Only - Comment Skipped)"
                notify_step_status(
                    "⏭️",
                    "SKIPPED",
                    "Facebook First Comment",
                    context=post_url,
                    info=f"Photo posted, first comment skipped ({comment_err}).",
                )
        except Exception as e:
            fb_status_text = f"Photo Failed ({e})"
            notify_step_status("⚠️", "WARNING", "Facebook Photo Post", context=post_url, info=str(e))

    success_report = (
        f"✅ PUBLISHED | The Daily Insight\n"
        f"Time: {now_utc_str()}\n"
        f"Category: {news['category']}\n"
        f"Title: {title}\n"
        f"Blog Link: {post_url}\n"
        f"Facebook: {fb_status_text}\n\n"
        f"📋 ফেসবুক গ্রুপে শেয়ার করার রেডি ক্যাপশন:\n{group_caption}"
    )
    if send_telegram_message(success_report):
        print("[TELEGRAM_SUCCESS] Sent success report & group caption.")

    history.append({
        "hash": news["hash"],
        "url": news["link"],
        "post_url": post_url,
        "post_id": post_id,
        "title": title,
        "published_at": now_utc_str(),
    })
    save_history(history)
    print("History saved.")

if __name__ == "__main__":
    main()

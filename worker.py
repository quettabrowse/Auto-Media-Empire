import html
import json
import os
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests


# ==============================================================================
# ১. কনফিগারেশন ও GitHub Secrets লোড করা
# ==============================================================================
def split_keys(raw_value: Optional[str]) -> List[str]:
    if not raw_value:
        return []
    parts = re.split(r"[\n,;]+", raw_value.strip())
    return [p.strip() for p in parts if p.strip()]


BLOGGER_CLIENT_ID = os.getenv("BLOGGER_CLIENT_ID", "").strip()
BLOGGER_CLIENT_SECRET = os.getenv("BLOGGER_CLIENT_SECRET", "").strip()
BLOGGER_REFRESH_TOKEN = os.getenv("BLOGGER_REFRESH_TOKEN", "").strip()

GEMINI_KEYS = split_keys(os.getenv("GEMINI_API_KEY", ""))
GROQ_KEYS = split_keys(os.getenv("GROQ_API_KEY", ""))
HF_KEYS = split_keys(os.getenv("HF_API_KEY", ""))

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_API_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

FB_PAGE_ID = os.getenv("FB_PAGE_ID", "").strip()
FB_PAGE_ACCESS_TOKEN = os.getenv("FB_PAGE_ACCESS_TOKEN", "").strip()

CUSTOM_TOPIC_ENV = os.getenv("CUSTOM_TOPIC", "").strip()
BLOGGER_BLOG_ID_ENV = os.getenv("BLOGGER_BLOG_ID", "").strip()

SITE_NAME = "The Daily Insight"
SITE_URL = "https://thedailyinsightbangla.blogspot.com"

TOPIC_QUEUE_FILE = Path("topic_queue.txt")
USED_TOPICS_FILE = Path("used_topics.json")

GEMINI_MODELS = [
    "gemini-3.1-pro-preview",
    "gemini-3-flash-preview",
    "gemini-3.5-flash-lite",
    "gemini-2.5-flash",
]

GROQ_MODELS = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
]

HF_IMAGE_MODELS = [
    "black-forest-labs/FLUX.1-schnell",
    "stabilityai/stable-diffusion-xl-base-1.0",
]

RSS_FEEDS = [
    "https://feeds.arstechnica.com/arstechnica/index",
    "https://www.sciencedaily.com/rss/top/science.xml",
    "https://www.theverge.com/rss/index.xml",
    "https://techcrunch.com/feed/",
    "https://www.bbc.co.uk/mundo/index.xml",
    "https://feeds.bbci.co.uk/news/technology/rss.xml",
]


# ==============================================================================
# ২. Telegram নোটিফিকেশন হেল্পার
# ==============================================================================
def send_telegram_message(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[INFO] Telegram secrets set kora nei, message skip kora holo.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    trimmed = text[:3900]
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": trimmed,
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=20)
        if not resp.ok:
            print(f"[WARN] Telegram message failed: {resp.status_code} {resp.text[:200]}")
    except Exception as exc:
        print(f"[WARN] Telegram request error: {exc}")


def notify_error(step: str, error_msg: str, context: str = "") -> None:
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    msg = (
        f"⚠️ {SITE_NAME} - Error Alert\n"
        f"🕒 Time: {now_str}\n"
        f"📍 Step: {step}\n"
        f"📝 Context: {context[:250] if context else 'N/A'}\n"
        f"❌ Error: {str(error_msg)[:1200]}"
    )
    print(msg)
    send_telegram_message(msg)


# ==============================================================================
# ৩. কাস্টম টপিক ও ফিড থেকে নতুন বিষয় সংগ্রহ
# ==============================================================================
def load_used_topics() -> List[str]:
    if not USED_TOPICS_FILE.exists():
        return []
    try:
        data = json.loads(USED_TOPICS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [str(x).strip() for x in data if str(x).strip()]
    except Exception as exc:
        print(f"[WARN] used_topics.json read error: {exc}")
    return []


def save_used_topic(topic: str) -> None:
    used = load_used_topics()
    norm = topic.strip().lower()
    if norm and norm not in [u.lower() for u in used]:
        used.append(topic.strip())
        used = used[-150:]
        USED_TOPICS_FILE.write_text(
            json.dumps(used, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def pop_topic_from_queue_file() -> Optional[str]:
    if not TOPIC_QUEUE_FILE.exists():
        return None
    try:
        lines = TOPIC_QUEUE_FILE.read_text(encoding="utf-8").splitlines()
        active_topics: List[str] = []
        chosen: Optional[str] = None

        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#") or line.startswith("[DONE]"):
                active_topics.append(raw)
                continue
            if chosen is None:
                chosen = line
                active_topics.append(f"[DONE] {line}")
            else:
                active_topics.append(raw)

        if chosen:
            TOPIC_QUEUE_FILE.write_text(
                "\n".join(active_topics) + ("\n" if active_topics else ""),
                encoding="utf-8",
            )
            return chosen
    except Exception as exc:
        notify_error("Topic Queue Read", exc, str(TOPIC_QUEUE_FILE))
    return None


def fetch_rss_candidates() -> List[Dict[str, str]]:
    items: List[Dict[str, str]] = []
    headers = {"User-Agent": f"{SITE_NAME} Bot/1.0 (+{SITE_URL})"}

    for feed_url in RSS_FEEDS:
        try:
            resp = requests.get(feed_url, headers=headers, timeout=20)
            if not resp.ok:
                continue
            root = ET.fromstring(resp.content)
            for elem in root.iter():
                tag = elem.tag.split("}")[-1].lower()
                if tag in ("item", "entry"):
                    title = ""
                    summary = ""
                    link = ""
                    for child in elem:
                        ctag = child.tag.split("}")[-1].lower()
                        if ctag == "title" and child.text:
                            title = html.unescape(child.text.strip())
                        elif ctag in ("description", "summary") and child.text:
                            clean_txt = re.sub(r"<[^>]+>", " ", child.text)
                            summary = html.unescape(re.sub(r"\s+", " ", clean_txt).strip())
                        elif ctag == "link":
                            href = child.attrib.get("href", "").strip()
                            txt = (child.text or "").strip()
                            link = href or txt
                    if title:
                        items.append(
                            {
                                "title": title,
                                "summary": summary[:600],
                                "source": feed_url,
                                "url": link,
                            }
                        )
                    if len(items) >= 25:
                        break
        except Exception as exc:
            print(f"[WARN] RSS feed error ({feed_url}): {exc}")
            continue

    return items


def select_topic_and_research() -> Dict[str, Any]:
    used_set = {u.lower() for u in load_used_topics()}

    # ১) বাইরে থেকে পাঠানো কাস্টম টপিক থাকলে আগে সেটা নেওয়া হবে
    queue_topic = pop_topic_from_queue_file()
    custom_topic = CUSTOM_TOPIC_ENV or queue_topic

    if custom_topic:
        save_used_topic(custom_topic)
        # একই বিষয়ের একাধিক সোর্স থেকে তথ্য সংগ্রহের চেষ্টা
        rss_matches: List[str] = []
        keywords = [w.lower() for w in re.findall(r"[A-Za-z0-9\u0980-\u09FF]{3,}", custom_topic)]
        for entry in fetch_rss_candidates():
            hay = f"{entry['title']} {entry['summary']}".lower()
            if any(k in hay for k in keywords):
                rss_matches.append(f"- Source: {entry['source']} | Title: {entry['title']} | Notes: {entry['summary']}")

        research_notes = (
            f"Custom Topic Requested by Editor: {custom_topic}\n"
            "Instructions: Research this topic thoroughly across multiple angles/sources from your knowledge and any matching feed notes below. "
            "Do not invent unverified facts or fake numbers.\n"
        )
        if rss_matches:
            research_notes += "Matching Feed Notes:\n" + "\n".join(rss_matches[:6])

        return {
            "topic": custom_topic,
            "is_custom": True,
            "research_notes": research_notes,
        }

    # ২) কাস্টম টপিক না থাকলে ফিড থেকে নতুন বৈচিত্র্যময় বিষয় নেওয়া হবে
    candidates = fetch_rss_candidates()
    for cand in candidates:
        t_low = cand["title"].lower()
        if t_low not in used_set:
            save_used_topic(cand["title"])
            notes = (
                f"Feed Title: {cand['title']}\n"
                f"Feed Source: {cand['source']}\n"
                f"Feed Summary: {cand['summary']}\n"
                "Note: Treat these feed notes as factual anchor points. Do not fabricate statistics, dates, or claims not supported by verified knowledge or the notes."
            )
            return {
                "topic": cand["title"],
                "is_custom": False,
                "research_notes": notes,
            }

    raise RuntimeError("No fresh topic could be retrieved from custom queue or RSS feeds.")


# ==============================================================================
# ৪. Gemini + Groq Fallback ইঞ্জিন (মানবিক লেখা + সহজ ব্যাখ্যা + সত্যতা)
# ==============================================================================
def build_article_prompt(topic: str, research_notes: str, is_custom: bool) -> str:
    mode_line = (
        "এটি সম্পাদক বা পাঠকের অনুরোধে আসা একটি বিশেষ বিষয়। বিভিন্ন দিক ও সূত্র থেকে যাচাইকৃত তথ্য গুছিয়ে সহজ ও আকর্ষণীয়ভাবে উপস্থাপন করো।"
        if is_custom
        else "এটি একটি নতুন ও প্রাসঙ্গিক বিষয়। একাধিক দৃষ্টিকোণ থেকে তথ্য যাচাই করে সহজ, সাবলীল ও তথ্যবহুলভাবে উপস্থাপন করো।"
    )

    return f"""
তুমি '{SITE_NAME}' বাংলা নিউজ ও ইনসাইট পোর্টালের একজন অভিজ্ঞ, আন্তরিক ও দরদি লেখক।
তোমার কাজ হলো নিচের বিষয় ও রিসার্চ নোটের ওপর ভিত্তি করে একটি পূর্ণাঙ্গ, নির্ভুল ও মানুষের মতো সাবলীল বাংলা আর্টিকেল লেখা।

নির্দেশনা:
1. {mode_line}
2. লেখার ধরন (Human Touch): লেখাটি যেন যান্ত্রিক বা অনুবাদ করা মনে না হয়। মনে হবে একজন অভিজ্ঞ ও বন্ধুসুলভ মানুষ পাঠকের পাশে বসে সহজ, আন্তরিক ও প্রাঞ্জল বাংলায় বিষয়টি বুঝিয়ে বলছেন।
3. সহজ ব্যাখ্যা (Term & Company Explainer): লেখায় যদি কোনো নতুন কোম্পানি, প্রতিষ্ঠান, প্রযুক্তি বা অপরিচিত শব্দ/ধারণা আসে (যেমন Anthropic বা যেকোনো নতুন টার্ম), তবে লেখার ভেতরেই সহজ ভাষায় পরিষ্কার করে বুঝিয়ে বলবে যে সেটি কী এবং কেন গুরুত্বপূর্ণ—যাতে পাঠক লেখাটি পড়েই পুরো বিষয়টি সহজে বুঝতে পারে।
4. কঠোর সত্যতা (Strict Factuality): রিসার্চ নোট ও যাচাইকৃত তথ্যের বাইরে কোনো মনগড়া পরিসংখ্যান, কাল্পনিক দাবি বা অপ্রমাণিত অনুমান যোগ করবে না। কোনো তথ্য নিশ্চিত না হলে তা বানিয়ে লিখবে না।
5. আউটপুট ফরম্যাট: উত্তর অবশ্যই নিচের সঠিক JSON কাঠামোতে দেবে (JSON-এর বাইরে অতিরিক্ত কথা লিখবে না):
{{
  "title": "আকর্ষণীয় ও স্পষ্ট বাংলা শিরোনাম",
  "label": "একটি উপযুক্ত বাংলা বিভাগ/লেবেল (যেমন: প্রযুক্তি, বিজ্ঞান, বিশ্ব, বিশ্লেষণ)",
  "excerpt": "২-৩ বাক্যের সহজ ও আকর্ষণীয় সারসংক্ষেপ",
  "html_body": "<p>...</p><h2>...</h2><p>...</p>...",
  "image_prompt": "Detailed, watermark-free English visual prompt describing a clean editorial/conceptual scene matching the article subject (no text, no logos, no watermarks)"
}}

বিষয়: {topic}

রিসার্চ নোট:
{research_notes}
""".strip()


def call_gemini_single(api_key: str, model_name: str, prompt: str) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.65,
            "responseMimeType": "application/json",
        },
    }
    resp = requests.post(url, json=payload, timeout=60)
    if not resp.ok:
        raise RuntimeError(f"Gemini ({model_name}) HTTP {resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    candidates = data.get("candidates") or []
    if not candidates:
        raise RuntimeError(f"Gemini ({model_name}) returned empty candidates.")

    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if isinstance(p, dict)).strip()
    if not text:
        raise RuntimeError(f"Gemini ({model_name}) returned empty text.")
    return text


def call_groq_single(api_key: str, model_name: str, prompt: str) -> str:
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model_name,
        "temperature": 0.65,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": "You are a warm, accurate Bengali editorial writer. Always respond strictly in valid JSON.",
            },
            {"role": "user", "content": prompt},
        ],
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    if not resp.ok:
        raise RuntimeError(f"Groq ({model_name}) HTTP {resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"Groq ({model_name}) returned empty choices.")

    content = ((choices[0].get("message") or {}).get("content") or "").strip()
    if not content:
        raise RuntimeError(f"Groq ({model_name}) returned empty content.")
    return content


def parse_article_json(raw_text: str, fallback_topic: str) -> Dict[str, str]:
    cleaned = raw_text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)

    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    json_str = match.group(0) if match else cleaned

    parsed = json.loads(json_str)
    title = str(parsed.get("title") or fallback_topic).strip()
    label = str(parsed.get("label") or "বিশ্লেষণ").strip()
    excerpt = str(parsed.get("excerpt") or "").strip()
    html_body = str(parsed.get("html_body") or "").strip()
    image_prompt = str(parsed.get("image_prompt") or "").strip()

    if not html_body:
        raise ValueError("Parsed article missing html_body.")

    if not image_prompt:
        image_prompt = (
            f"Clean editorial illustration representing {fallback_topic}, modern digital lighting, "
            "high detail, photorealistic editorial style, no text, no watermark, no logo"
        )

    return {
        "title": title,
        "label": label,
        "excerpt": excerpt,
        "html_body": html_body,
        "image_prompt": image_prompt,
    }


def generate_article_with_fallback(topic: str, research_notes: str, is_custom: bool) -> Dict[str, str]:
    prompt = build_article_prompt(topic, research_notes, is_custom)
    errors: List[str] = []

    # ধাপ ক: Gemini মডেল ও একাধিক Key fallback
    for key_idx, g_key in enumerate(GEMINI_KEYS, start=1):
        for model_name in GEMINI_MODELS:
            try:
                raw = call_gemini_single(g_key, model_name, prompt)
                article = parse_article_json(raw, topic)
                print(f"[OK] Article generated via Gemini ({model_name}, Key #{key_idx})")
                return article
            except Exception as exc:
                err_s = f"Gemini Key#{key_idx} [{model_name}]: {exc}"
                print(f"[WARN] {err_s}")
                errors.append(err_s)

    # ধাপ খ: Groq মডেল ও একাধিক Key fallback
    for key_idx, q_key in enumerate(GROQ_KEYS, start=1):
        for model_name in GROQ_MODELS:
            try:
                raw = call_groq_single(q_key, model_name, prompt)
                article = parse_article_json(raw, topic)
                print(f"[OK] Article generated via Groq ({model_name}, Key #{key_idx})")
                return article
            except Exception as exc:
                err_s = f"Groq Key#{key_idx} [{model_name}]: {exc}"
                print(f"[WARN] {err_s}")
                errors.append(err_s)

    raise RuntimeError("All Gemini and Groq attempts failed:\n" + "\n".join(errors[-8:]))


# ==============================================================================
# ৫. বুদ্ধিমান ইমেজ প্রম্পট ও ওয়াটারমার্কমুক্ত ছবি তৈরি (410 ফিক্সসহ)
# ==============================================================================
def build_intelligent_image_prompt(article_title: str, draft_prompt: str, topic: str) -> str:
    clean_draft = re.sub(r"\s+", " ", draft_prompt).strip()
    clean_title = re.sub(r"\s+", " ", article_title).strip()
    return (
        f"Editorial news illustration for topic '{topic}' ({clean_title}). "
        f"{clean_draft}. "
        "Sharp composition, natural lighting, authentic visual storytelling, "
        "strictly no watermark, no signature, no text overlay, no logo."
    )


def generate_image_bytes(image_prompt: str) -> Optional[Tuple[bytes, str]]:
    errors: List[str] = []

    # Hugging Face Inference Router (পুরোনো 410 api-inference এন্ডপয়েন্ট বাদ দিয়ে)
    for key_idx, hf_key in enumerate(HF_KEYS, start=1):
        for hf_model in HF_IMAGE_MODELS:
            url = f"https://router.huggingface.co/hf-inference/models/{hf_model}"
            headers = {
                "Authorization": f"Bearer {hf_key}",
                "Accept": "image/png",
            }
            payload = {
                "inputs": image_prompt,
                "parameters": {
                    "negative_prompt": "watermark, text, logo, signature, blurry, low quality, distorted",
                },
            }
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=90)
                ctype = (resp.headers.get("Content-Type") or "").lower()
                if resp.ok and ("image/" in ctype or len(resp.content) > 5000):
                    ext = "jpg" if "jpeg" in ctype or "jpg" in ctype else "png"
                    mime = "image/jpeg" if ext == "jpg" else "image/png"
                    print(f"[OK] Image generated via HF Router ({hf_model}, Key #{key_idx})")
                    return resp.content, mime
                errors.append(f"HF Key#{key_idx} [{hf_model}] HTTP {resp.status_code}: {resp.text[:200]}")
            except Exception as exc:
                errors.append(f"HF Key#{key_idx} [{hf_model}]: {exc}")

    # Pollinations ব্যাকআপ (যদি HF ব্যর্থ হয়)
    try:
        encoded = urllib.parse.quote(image_prompt[:450])
        seed = int(time.time()) % 100000
        poll_url = f"https://image.pollinations.ai/prompt/{encoded}?width=1200&height=675&nologo=true&seed={seed}"
        resp = requests.get(poll_url, timeout=60)
        ctype = (resp.headers.get("Content-Type") or "").lower()
        if resp.ok and ("image/" in ctype or len(resp.content) > 5000):
            print("[OK] Image generated via backup Pollinations (nologo=true)")
            return resp.content, "image/jpeg"
        errors.append(f"Pollinations HTTP {resp.status_code}")
    except Exception as exc:
        errors.append(f"Pollinations error: {exc}")

    notify_error("Image Generation", "\n".join(errors[-6:]), image_prompt[:200])
    return None


# ==============================================================================
# ৬. Blogger OAuth2 ও পোস্ট প্রকাশ
# ==============================================================================
def get_blogger_access_token() -> str:
    if not (BLOGGER_CLIENT_ID and BLOGGER_CLIENT_SECRET and BLOGGER_REFRESH_TOKEN):
        raise RuntimeError("Blogger OAuth secrets missing (BLOGGER_CLIENT_ID/SECRET/REFRESH_TOKEN).")

    resp = requests.post(
        "https://oauth2.googleapis.com/t

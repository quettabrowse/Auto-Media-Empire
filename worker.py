import os
import json
import re
import time
import urllib.parse
import requests
import feedparser
from bs4 import BeautifulSoup
from datetime import datetime
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# সিক্রেটস ও এনভায়রনমেন্ট লোড করা
CLIENT_ID = os.environ.get('BLOGGER_CLIENT_ID', '').strip()
CLIENT_SECRET = os.environ.get('BLOGGER_CLIENT_SECRET', '').strip()
REFRESH_TOKEN = os.environ.get('BLOGGER_REFRESH_TOKEN', '').replace('\n', '').replace('\r', '').replace(' ', '').strip()
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '').strip()
GROQ_API_KEY = os.environ.get('GROQ_API_KEY', '').strip()
HF_API_KEY = os.environ.get('HF_API_KEY', '').strip()
GITHUB_REPO = os.environ.get('GITHUB_REPOSITORY', '').strip()

HISTORY_FILE = "posted_history.json"
IMAGES_DIR = "images"

# ১০টি বড় ইন্টারন্যাশনাল টেক, ডেভেলপার ও সায়েন্স সোর্স
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
    ("Phys.org", "https://phys.org/rss-feed/")
]

BLOCKED_KEYWORDS = ["cricket", "ipl", "t20", "odi", "world cup", "football", "fifa", "nba", "nfl", "premier league", "match score"]

FALLBACK_IMAGES = {
    "Technology": "https://images.unsplash.com/photo-1518770660439-4636190af475?w=1200&q=80",
    "Developer Tools": "https://images.unsplash.com/photo-1555066931-4365d14bab8c?w=1200&q=80",
    "Science": "https://images.unsplash.com/photo-1507413245164-6160d8298b31?w=1200&q=80",
    "World News": "https://images.unsplash.com/photo-1451187580459-43490279c0fa?w=1200&q=80"
}

def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history[-100:], f, ensure_ascii=False, indent=2)

def call_groq(prompt):
    if not GROQ_API_KEY:
        return None
    models = ["openai/gpt-oss-120b", "qwen/qwen3.8-27b", "openai/gpt-oss-20b"]
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    for model in models:
        payload = {"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.5}
        try:
            res = requests.post(url, headers=headers, json=payload, timeout=45)
            if res.status_code == 200:
                print(f"✅ Groq সফল: {model}")
                return res.json()["choices"][0]["message"]["content"]
            print(f"⚠️ Groq ({model}) Status {res.status_code}, পরের মডেল চেষ্টা করা হচ্ছে...")
        except Exception as e:
            print(f"⚠️ Groq ({model}) এরর: {e}")
    return None

def call_gemini(prompt):
    models = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]
    headers = {"Content-Type": "application/json"}
    payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.65}}
    for model in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
        try:
            res = requests.post(url, headers=headers, json=payload, timeout=60)
            if res.status_code == 200:
                print(f"✅ Gemini সফল: {model}")
                return res.json()["candidates"][0]["content"]["parts"][0]["text"]
            print(f"⚠️ Gemini ({model}) Status {res.status_code}, ব্যাকআপ চেষ্টা করা হচ্ছে...")
        except Exception as e:
            print(f"⚠️ Gemini ({model}) এরর: {e}")
    raise Exception("সবগুলো Gemini মডেল ব্যর্থ হয়েছে।")

def scrape_news(posted_urls):
    articles = []
    for source_name, feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:4]:
                link = entry.get("link", "").strip()
                title = entry.get("title", "").strip()
                summary = re.sub('<[^<]+?>', '', entry.get("summary", entry.get("description", "")))[:350]
                if not link or not title or link in posted_urls:
                    continue
                if any(bad in title.lower() for bad in BLOCKED_KEYWORDS):
                    continue
                articles.append({"source": source_name, "title": title, "link": link, "summary": summary})
        except Exception as e:
            print(f"⚠️ {source_name} স্ক্র্যাপ এরর: {e}")
    return articles

def fetch_full_article_text(url, fallback_summary):
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        res = requests.get(url, headers=headers, timeout=15)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            paragraphs = [p.get_text(" ", strip=True) for p in soup.find_all("p") if len(p.get_text(strip=True)) > 40]
            full_text = "\n".join(paragraphs)[:4000]
            if len(full_text) > 250:
                print("✅ মূল ওয়েবপেজ থেকে বিস্তারিত তথ্য পড়া হয়েছে!")
                return full_text
    except Exception as e:
        print(f"⚠️ মূল পেজ পড়া যায়নি ({e}), সারসংক্ষেপ ব্যবহার করা হচ্ছে...")
    return fallback_summary

def save_image_bytes(content_bytes):
    """ছবি গিটহাবের images/ ফোল্ডারে স্থায়ীভাবে সেভ করা"""
    os.makedirs(IMAGES_DIR, exist_ok=True)
    filename = f"img_{int(time.time())}.jpg"
    filepath = os.path.join(IMAGES_DIR, filename)
    with open(filepath, "wb") as f:
        f.write(content_bytes)
    if GITHUB_REPO:
        return f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/{IMAGES_DIR}/{filename}"
    return None

def generate_and_save_image(image_prompt, category):
    """প্রথমে Hugging Face (FLUX.1 - ০% ওয়াটারমার্ক), ব্যর্থ হলে Pollinations.ai"""
    full_prompt = image_prompt + ", editorial tech news photography, ultra realistic, 4k, clean modern lighting, no text, no watermark"

    # ১ম চেষ্টা: Hugging Face (FLUX.1-schnell)
    if HF_API_KEY:
        try:
            print("🎨 ১ম চেষ্টা: Hugging Face (FLUX.1-schnell) দিয়ে ওয়াটারমার্ক-ফ্রি ছবি তৈরি হচ্ছে...")
            hf_url = "https://router.huggingface.co/hf-inference/models/black-forest-labs/FLUX.1-schnell"
            headers = {"Authorization": f"Bearer {HF_API_KEY}", "Content-Type": "application/json"}
            res = requests.post(hf_url, headers=headers, json={"inputs": full_prompt}, timeout=45)
            if res.status_code == 200 and len(res.content) > 5000:
                perm_url = save_image_bytes(res.content)
                if perm_url:
                    print(f"✅ Hugging Face ছবি স্থায়ীভাবে সেভ হয়েছে: {perm_url}")
                    return perm_url
            else:
                print(f"⚠️ Hugging Face ব্যর্থ (Status {res.status_code}), সরাসরি Pollinations.ai-তে যাওয়া হচ্ছে...")
        except Exception as e:
            print(f"⚠️ Hugging Face এরর ({e}), সরাসরি Pollinations.ai-তে যাওয়া হচ্ছে...")

    # ২য় চেষ্টা (ব্যাকআপ): Pollinations.ai
    try:
        print("🎨 ২য় চেষ্টা: Pollinations.ai দিয়ে ছবি তৈরি হচ্ছে...")
        encoded = urllib.parse.quote(full_prompt)
        pol_url = f"https://image.pollinations.ai/prompt/{encoded}?width=1200&height=675&nologo=true&model=flux"
        res = requests.get(pol_url, timeout=35)
        if res.status_code == 200 and len(res.content) > 5000:
            perm_url = save_image_bytes(res.content)
            if perm_url:
                print(f"✅ Pollinations.ai ছবি স্থায়ীভাবে সেভ হয়েছে: {perm_url}")
                return perm_url
            return pol_url
    except Exception as e:
        print(f"⚠️ Pollinations.ai এরর ({e}), ক্যাটাগরি ব্যাকআপ ছবি নেওয়া হচ্ছে...")

    return FALLBACK_IMAGES.get(category, FALLBACK_IMAGES["Technology"])

def main():
    try:
        history = load_history()
        posted_urls = {item.get("source_url") for item in history if item.get("source_url")}
        recent_titles = [item.get("bn_title", "") for item in history[-10:] if item.get("bn_title")]
        last_category = history[-1].get("category", "") if history else ""

        creds = Credentials(
            token=None,
            refresh_token=REFRESH_TOKEN,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET
        )
        service = build('blogger', 'v3', credentials=creds)
        blogs = service.blogs().listByUser(userId='self').execute()
        blog_id = blogs['items'][0]['id']
        print(f"✅ ব্লগে কানেক্ট হয়েছে: {blogs['items'][0]['name']}")

        print("🔍 ১০টি সোর্স থেকে তাজা খবর খোঁজা হচ্ছে...")
        candidates = scrape_news(posted_urls)
        if not candidates:
            print("❌ নতুন কোনো ইউনিক খবর পাওয়া যায়নি।")
            return

        shortlist = candidates[:25]
        list_text = "\n".join([f"{i}. [{a['source']}] {a['title']} - {a['summary'][:100]}" for i, a in enumerate(shortlist)])
        recent_str = "\n".join([f"- {t}" for t in recent_titles]) if recent_titles else "None"

        # ধাপ ১: স্মার্ট টপিক মেমোরি ও ক্যাটাগরি রোটেশনসহ খবর বাছাই
        selector_prompt = f"""
        You are the Chief Editor of 'The Daily Insight' (Bengali Tech, Developer Tools & Science portal).
        From the 25 news stories below, pick the SINGLE ABSOLUTE BEST OF THE BEST story.
        
        SMART RULES:
        1. AVOID DUPLICATE TOPICS: Do NOT pick any story covering the same event/topic as these recently published articles:
        {recent_str}
        2. CATEGORY ROTATION: Our last published category was '{last_category}'. Strongly prefer a DIFFERENT category this time (choose from: Technology, Developer Tools, Science, World News) if a great story exists.
        3. Strictly ignore boring PR or sports.
        
        List:
        {list_text}
        
        Return ONLY valid JSON (no markdown, no extra text):
        {{
            "index": 0,
            "category": "Technology or Developer Tools or Science or World News",
            "image_prompt": "A vivid, detailed English prompt for a realistic editorial news thumbnail image representing this story (no text in image)"
        }}
        """

        print("🧠 Groq (চিফ এডিটর) স্মার্ট মেমোরি চেক করে সেরা খবর বাছাই করছে...")
        sel_res = call_groq(selector_prompt) or call_gemini(selector_prompt)
        choice = json.loads(re.search(r'\{.*\}', sel_res, re.DOTALL).group())

        selected = shortlist[int(choice.get("index", 0))]
        category = choice.get("category", "Technology")
        image_prompt = choice.get("image_prompt", selected["title"])
        print(f"🎯 বাছাইকৃত খবর: {selected['title']} ({category})")

        # ধাপ ২: ডিপ রিসার্চ
        full_details = fetch_full_article_text(selected["link"], selected["summary"])
        research_prompt = f"""
        You are a Senior Tech & Science Researcher. Read the full details of this selected news story and create an accurate, structured research brief for our Bengali journalist.
        Include ONLY verified facts from the text (never invent numbers or fake claims):
        1. Core facts and what happened.
        2. Why this matters in simple terms.
        3. Real-world impact on everyday tech users or developers.
        4. 2 common reader questions with clear, accurate answers.
        
        Title: {selected['title']}
        Source: {selected['source']}
        Full Details:
        {full_details}
        """

        print("🔬 Groq (ডিপ রিসার্চার) নির্ভুল রিসার্চ নোট তৈরি করছে...")
        deep_research = call_groq(research_prompt) or call_gemini(research_prompt)

        # ধাপ ৩: সহজ, মানবিক, নির্ভুল ও স্টাইলিশ বাংলা আর্টিকেল
        writer_prompt = f"""
        You are an experienced, friendly human Bengali Tech & Science Journalist writing for 'The Daily Insight'.
        
        STRICT HUMAN-WRITING, SIMPLICITY & ACCURACY RULES (CRITICAL):
        - Write 100% in your own natural, warm, human-like Bengali voice. NEVER copy or do robotic word-for-word translation.
        - KEEP IT EASY TO UNDERSTAND: Avoid overly complicated, heavy, or confusing jargon. If a technical term is needed, explain it simply so any curious reader can understand effortlessly.
        - ZERO FALSE INFORMATION: Stick strictly to the verified facts in the research brief below. Never invent statistics, quotes, or claims.
        - NEVER use Markdown asterisks like **bold** or *italic* — ALWAYS use proper HTML tags like <strong>...</strong>.
        - Write strictly in pure Bengali (with standard English tech terms only). NEVER include any Chinese, Japanese, or garbled characters.
        
        News Title: {selected['title']}
        Category: {category}
        Verified Research Brief:
        {deep_research}
        
        ULTIMATE EDITORIAL HTML DESIGN RULES:
        1. First line MUST be ONLY the catchy, clear Bengali Headline wrapped in <TITLE>...</TITLE> tags.
        2. Rest of the response MUST be clean HTML (NO <html>, <head>, <body>, or ```html ticks).
        3. Start with a sleek summary card:
           <div style="background:#f8fafc; border:1px solid #e2e8f0; border-left:4px solid #2563eb; padding:18px; margin:18px 0 24px; border-radius:8px; color:#1e293b;">
           <div style="font-weight:700; font-size:17px; color:#2563eb; margin-bottom:10px;">এক নজরে মূল বিষয়</div>
           <ul style="margin:0; padding-left:20px; line-height:1.75;">... (3-4 crisp bullet points)</ul>
           </div>
        4. Use clean H2 headings: <h2 style="font-size:21px; font-weight:700; color:#0f172a; margin:28px 0 12px; padding-bottom:6px; border-bottom:2px solid #e2e8f0;">...</h2>
        5. Include ONE stylish Pull-Quote / Key Highlight in the middle:
           <blockquote style="background:#f1f5f9; border-left:4px solid #0d9488; padding:16px 20px; margin:22px 0; border-radius:0 8px 8px 0; font-style:italic; font-size:17px; color:#0f172a;">...</blockquote>
        6. If comparing features/specs/impacts, include a clean HTML <table> with border-collapse and #f8fafc header.
        7. End with a sleek "দ্রুত প্রশ্নোত্তর" box containing 2 sharp Q&As:
           <div style="background:#f8fafc; border:1px solid #e2e8f0; padding:18px; margin-top:26px; border-radius:10px; color:#1e293b;">
           <div style="font-weight:700; font-size:17px; color:#0f172a; margin-bottom:12px;">💡 দ্রুত প্রশ্নোত্তর</div>
           ...
           </div>
        8. Keep paragraphs short (3-4 lines max) for comfortable mobile reading.
        """

        print("✍️ Gemini সহজ, নির্ভুল ও স্টাইলিশ বাংলা আর্টিকেল লিখছে...")
        raw_article = call_gemini(writer_prompt)

        title_match = re.search(r'<TITLE>(.*?)</TITLE>', raw_article, re.DOTALL)
        bn_title = title_match.group(1).strip() if title_match else selected['title']
        article_html = re.sub(r'<TITLE>.*?</TITLE>', '', raw_article, flags=re.DOTALL).strip()
        article_html = article_html.replace("```html", "").replace("```", "").strip()

        # বাগ ফিক্স: **বোল্ড** এবং ভুল ভিনদেশি অক্ষর পরিষ্কার করা
        article_html = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', article_html)
        article_html = re.sub(r'[\u4e00-\u9fff\u3040-\u30ff]+', '', article_html)
        bn_title = re.sub(r'[\u4e00-\u9fff\u3040-\u30ff]+', '', bn_title).strip()

        # ধাপ ৪: স্থায়ী ওয়াটারমার্ক-ফ্রি ছবি তৈরি
        img_url = generate_and_save_image(image_prompt, category)

        # টপ ক্যাটাগরি ব্যাজ + ছবি
        badge_html = f'<div style="margin-bottom:14px;"><span style="display:inline-block; background:#eff6ff; color:#1d4ed8; border:1px solid #bfdbfe; font-size:12px; font-weight:700; padding:4px 12px; border-radius:20px; letter-spacing:0.5px; text-transform:uppercase;">{category}</span></div>'
        image_html = f'<div style="text-align:center; margin:0 0 24px 0;"><img src="{img_url}" alt="{bn_title}" style="width:100%; max-width:800px; border-radius:12px; box-shadow:0 6px 18px rgba(0,0,0,0.08);" /></div>'

        # ধাপ ৫: "আরও পড়ুন" বক্স
        related_html = ""
        valid_past = [h for h in history if h.get("blog_url") and h.get("bn_title")][-2:]
        if valid_past:
            links_li = "".join([f'<li style="margin-bottom:8px;"><a href="{h["blog_url"]}" style="color:#2563eb; text-decoration:none; font-weight:600;">{h["bn_title"]}</a></li>' for h in reversed(valid_past)])
            related_html = f'<div style="background:#f8fafc; border:1px solid #e2e8f0; padding:16px 18px; margin-top:26px; border-radius:10px;"><div style="font-weight:700; font-size:16px; color:#0f172a; margin-bottom:10px;">📌 আরও পড়ুন</div><ul style="padding-left:20px; margin:0;">{links_li}</ul></div>'

        # ধাপ ৬: তথ্যসূত্র + অদৃশ্য Google SEO NewsArticle Schema
        credit_html = f'<p style="margin-top:24px; font-size:13px; color:#64748b; border-top:1px solid #e2e8f0; padding-top:12px;"><em>তথ্যসূত্র: <a href="{selected["link"]}" target="_blank" rel="noopener noreferrer" style="color:#2563eb;">{selected["source"]}</a></em></p>'

        schema_data = {
            "@context": "[https://schema.org](https://schema.org)",
            "@type": "NewsArticle",
            "headline": bn_title,
            "image": [img_url],
            "datePublished": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "author": {"@type": "Organization", "name": "The Daily Insight"}
        }
        schema_html = f'<script type="application/ld+json">{json.dumps(schema_data, ensure_ascii=False)}</script>'

        final_content = badge_html + image_html + article_html + related_html + credit_html + schema_html

        # ধাপ ৭: ব্লগে পাবলিশ
        print("🚀 ব্লগে পাবলিশ করা হচ্ছে...")
        post_body = {
            "title": bn_title,
            "content": final_content,
            "labels": [category, "The Daily Insight"]
        }
        response = service.posts().insert(blogId=blog_id, body=post_body, isDraft=False).execute()
        blog_url = response.get("url")
        print(f"🎉 সাফল্য! ফাইনাল পোস্ট লাইভ হয়েছে: {blog_url}")

        # ধাপ ৮: মেমোরি সেভ
        history.append({
            "source_url": selected["link"],
            "bn_title": bn_title,
            "blog_url": blog_url,
            "category": category,
            "date": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        })
        save_history(history)
        print("💾 মেমোরি আপডেট হয়েছে।")

    except Exception as e:
        print(f"❌ সিস্টেমে এরর এসেছে: {e}")
        raise e

if __name__ == '__main__':
    main()

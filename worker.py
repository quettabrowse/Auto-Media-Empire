import os
import json
import re
import urllib.parse
import requests
import feedparser
from datetime import datetime
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# সিক্রেটস লোড করা
CLIENT_ID = os.environ.get('BLOGGER_CLIENT_ID', '').strip()
CLIENT_SECRET = os.environ.get('BLOGGER_CLIENT_SECRET', '').strip()
REFRESH_TOKEN = os.environ.get('BLOGGER_REFRESH_TOKEN', '').replace('\n', '').replace('\r', '').replace(' ', '').strip()
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '').strip()

HISTORY_FILE = "posted_history.json"

# মাল্টি-সোর্স স্ক্র্যাপার (টেকনোলজি, ডেভেলপার টুলস, বড় খবর ও বিজ্ঞান)
RSS_FEEDS = [
    ("TechCrunch", "https://techcrunch.com/feed/"),
    ("The Verge", "https://www.theverge.com/rss/index.xml"),
    ("Hacker News", "https://hnrss.org/frontpage"),
    ("Dev.to", "https://dev.to/feed"),
    ("ScienceDaily", "https://www.sciencedaily.com/rss/top/science.xml"),
    ("Wired", "https://www.wired.com/feed/rss")
]

# যে বিষয়গুলো বাদ যাবে (সাধারণ ক্রিকেট/খেলা)
BLOCKED_KEYWORDS = ["cricket", "ipl", "t20", "odi", "world cup", "football", "fifa", "nba", "nfl", "premier league", "match score"]

# ক্যাটাগরি অনুযায়ী ব্যাকআপ ছবি (যদি এআই ইমেজ সার্ভার ডাউন থাকে)
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

def call_gemini(prompt):
    """একাধিক মডেলের অটো-ব্যাকআপসহ Gemini API কল"""
    models = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]
    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.7}
    }
    
    for model in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
        try:
            res = requests.post(url, headers=headers, json=payload, timeout=60)
            if res.status_code == 200:
                data = res.json()
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                print(f"✅ এআই মডেল সফলভাবে কাজ করেছে: {model}")
                return text
            else:
                print(f"⚠️ {model} ব্যর্থ হয়েছে (Status {res.status_code}), ব্যাকআপ মডেল চেষ্টা করা হচ্ছে...")
        except Exception as e:
            print(f"⚠️ {model} এরর: {e}, ব্যাকআপ মডেল চেষ্টা করা হচ্ছে...")
    
    raise Exception("সবগুলো Gemini মডেল ব্যর্থ হয়েছে।")

def scrape_news(posted_urls):
    """সব সোর্স থেকে তাজা ও ইউনিক খবর সংগ্রহ"""
    articles = []
    for source_name, feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:5]:
                link = entry.get("link", "").strip()
                title = entry.get("title", "").strip()
                summary = re.sub('<[^<]+?>', '', entry.get("summary", entry.get("description", "")))[:400]
                
                if not link or not title or link in posted_urls:
                    continue
                
                # ক্রিকেট বা সাধারণ খেলা বাদ দেওয়া
                lower_title = title.lower()
                if any(bad in lower_title for bad in BLOCKED_KEYWORDS):
                    continue
                
                articles.append({
                    "source": source_name,
                    "title": title,
                    "link": link,
                    "summary": summary
                })
        except Exception as e:
            print(f"⚠️ {source_name} স্ক্র্যাপ করতে সমস্যা: {e}")
    return articles

def generate_or_fallback_image(image_prompt, category):
    """Pollinations.ai দিয়ে ছবি তৈরি ও প্রি-লোড চেক (ব্যাকআপসহ)"""
    try:
        encoded = urllib.parse.quote(image_prompt + ", editorial news photography, ultra realistic, 4k, clean lighting, no text")
        img_url = f"https://image.pollinations.ai/prompt/{encoded}?width=1200&height=675&nologo=true&model=flux"
        print("🎨 এআই ইমেজ জেনারেট ও যাচাই করা হচ্ছে...")
        check = requests.get(img_url, timeout=30)
        if check.status_code == 200 and len(check.content) > 5000:
            print("✅ এআই ইমেজ সফলভাবে তৈরি হয়েছে!")
            return img_url
    except Exception as e:
        print(f"⚠️ এআই ইমেজ তৈরি হয়নি ({e}), ব্যাকআপ ছবি ব্যবহার করা হচ্ছে...")
    
    return FALLBACK_IMAGES.get(category, FALLBACK_IMAGES["Technology"])

def main():
    try:
        # ১. মেমোরি লোড করা
        history = load_history()
        posted_urls = {item.get("source_url") for item in history if item.get("source_url")}

        # ২. ব্লগারের সাথে কানেক্ট করা
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

        # ৩. খবর স্ক্র্যাপ করা
        print("🔍 একাধিক সোর্স থেকে তাজা খবর খোঁজা হচ্ছে...")
        candidates = scrape_news(posted_urls)
        if not candidates:
            print("❌ নতুন কোনো ইউনিক খবর পাওয়া যায়নি।")
            return

        # সেরা ১৫টি খবর থেকে এডিটর এআই-কে বাছাই করতে দেওয়া
        shortlist = candidates[:15]
        list_text = "\n".join([f"{i}. [{a['source']}] {a['title']} - {a['summary'][:120]}" for i, a in enumerate(shortlist)])

        editor_prompt = f"""
        You are the Chief Editor of a premium Bengali Tech, Developer Tools & Science news portal.
        From the list below, pick the SINGLE most interesting, impactful, and unique news story (prioritize latest technology, developer tools/updates, big tech news, or amazing logical science; avoid boring PR or general sports).
        
        List:
        {list_text}
        
        Return ONLY valid JSON in this exact format (no markdown, no extra text):
        {{
            "index": 0,
            "category": "Technology or Developer Tools or Science or World News",
            "image_prompt": "A vivid, detailed English prompt for a realistic news thumbnail image representing this story (no text in image)"
        }}
        """
        
        print("🧠 এডিটর এআই সেরা খবরটি বাছাই করছে...")
        editor_res = call_gemini(editor_prompt)
        json_match = re.search(r'\{.*\}', editor_res, re.DOTALL)
        choice = json.loads(json_match.group())
        
        selected = shortlist[int(choice.get("index", 0))]
        category = choice.get("category", "Technology")
        image_prompt = choice.get("image_prompt", selected["title"])
        print(f"🎯 বাছাইকৃত খবর: {selected['title']} ({category})")

        # ৪. ৩-রাইটার টিম (রিসার্চার + বাঙালি সাংবাদিক + চিফ এডিটর) দিয়ে প্রিমিয়াম বাংলা আর্টিকেল লেখা
        writer_prompt = f"""
        Act as a 3-person elite Bengali newsroom team (Tech Researcher + Senior Bengali Journalist + Chief Editor).
        Write a deeply analytical, engaging, premium '.com' news portal style article in natural, fluent Bengali based on this news:
        
        Title: {selected['title']}
        Source: {selected['source']}
        Details: {selected['summary']}
        Link: {selected['link']}
        Category: {category}
        
        Strict Requirements:
        1. First line MUST be ONLY the catchy, professional Bengali Headline wrapped in <TITLE>...</TITLE> tags.
        2. Rest of the response MUST be clean, modern HTML (do NOT include <html>, <head>, <body>, or markdown ```html ticks).
        3. Start with a stylish highlight box: <div style="background:#f0f7ff; border-left:4px solid #0066cc; padding:15px; margin:15px 0; border-radius:6px;"><strong>এক নজরে মূল বিষয়:</strong> ... (3-4 bullet points)</div>
        4. Write 4-5 well-structured sections with <h2> headings (Deep Analysis, Why it matters, Impact on Developers/Users, Future Outlook).
        5. If it's a tool, update, or comparison, include a clean HTML <table> or highlighted feature box.
        6. Keep paragraphs short, crisp, and human-like (no robotic translation).
        """
        
        print("✍️ এআই নিউজরুম প্রিমিয়াম বাংলা আর্টিকেল লিখছে...")
        raw_article = call_gemini(writer_prompt)
        
        title_match = re.search(r'<TITLE>(.*?)</TITLE>', raw_article, re.DOTALL)
        bn_title = title_match.group(1).strip() if title_match else selected['title']
        article_html = re.sub(r'<TITLE>.*?</TITLE>', '', raw_article, flags=re.DOTALL).strip()
        article_html = article_html.replace("```html", "").replace("```", "").strip()

        # ৫. স্মার্ট ইমেজ তৈরি করা
        img_url = generate_or_fallback_image(image_prompt, category)
        image_html = f'<div style="text-align:center; margin-bottom:20px;"><img src="{img_url}" alt="{bn_title}" style="width:100%; max-width:800px; border-radius:10px; box-shadow:0 4px 12px rgba(0,0,0,0.1);" /></div>'

        # ৬. হেল্পার: "আরও পড়ুন" (Internal Linking) বক্স
        related_html = ""
        valid_past = [h for h in history if h.get("blog_url") and h.get("bn_title")][-2:]
        if valid_past:
            links_li = "".join([f'<li style="margin-bottom:8px;"><a href="{h["blog_url"]}" style="color:#0066cc; text-decoration:none; font-weight:bold;">{h["bn_title"]}</a></li>' for h in reversed(valid_past)])
            related_html = f'<div style="background:#f9f9f9; border:1px solid #e0e0e0; padding:15px; margin-top:25px; border-radius:8px;"><h3 style="margin-top:0; color:#333;">📌 আরও পড়ুন:</h3><ul style="padding-left:20px; margin-bottom:0;">{links_li}</ul></div>'

        # ৭. হেল্পার: তথ্যসূত্র (Source Credit)
        credit_html = f'<p style="margin-top:25px; font-size:13px; color:#666; border-top:1px solid #eee; padding-top:10px;"><em>তথ্যসূত্র: <a href="{selected["link"]}" target="_blank" rel="noopener noreferrer">{selected["source"]}</a></em></p>'

        final_content = image_html + article_html + related_html + credit_html

        # ৮. ব্লগে পাবলিশ করা
        print("🚀 ব্লগে পাবলিশ করা হচ্ছে...")
        post_body = {
            "title": bn_title,
            "content": final_content,
            "labels": [category, "The Daily Insight"]
        }
        response = service.posts().insert(blogId=blog_id, body=post_body, isDraft=False).execute()
        blog_url = response.get("url")
        print(f"🎉 সাফল্য! পোস্ট লাইভ হয়েছে: {blog_url}")

        # ৯. মেমোরি আপডেট করা
        history.append({
            "source_url": selected["link"],
            "bn_title": bn_title,
            "blog_url": blog_url,
            "category": category,
            "date": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        })
        save_history(history)
        print("💾 মেমোরি (posted_history.json) সেভ করা হয়েছে।")

    except Exception as e:
        print(f"❌ সিস্টেমে এরর এসেছে: {e}")
        raise e

if __name__ == '__main__':
    main()
    

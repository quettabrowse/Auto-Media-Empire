import os
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

CLIENT_ID = os.environ.get('BLOGGER_CLIENT_ID', '').strip()
CLIENT_SECRET = os.environ.get('BLOGGER_CLIENT_SECRET', '').strip()
REFRESH_TOKEN = os.environ.get('BLOGGER_REFRESH_TOKEN', '').replace('\n', '').replace('\r', '').replace(' ', '').strip()

def main():
    try:
        creds = Credentials(
            token=None,
            refresh_token=REFRESH_TOKEN,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET
        )

        service = build('blogger', 'v3', credentials=creds)

        print("ব্লগের সাথে কানেক্ট করা হচ্ছে...")
        blogs = service.blogs().listByUser(userId='self').execute()
        
        if not blogs.get('items'):
            print("Error: কোনো ব্লগ পাওয়া যায়নি।")
            return
        
        blog_id = blogs['items'][0]['id']
        blog_name = blogs['items'][0]['name']
        print(f"Success! কানেক্ট হয়েছে: {blog_name} (ID: {blog_id})")

        print("টেস্ট পোস্ট পাবলিশ করা হচ্ছে...")
        post_body = {
            "title": "সিস্টেম টেস্ট: Master Key Active!",
            "content": "<h1>সাফল্য!</h1><p>গিটহাব অ্যাকশন এবং OAuth 2.0 ব্যবহার করে এই পোস্টটি অটোমেটিক পাবলিশ হয়েছে। আমাদের বটের এখন ফুল অ্যাডমিন অ্যাক্সেস আছে!</p>",
            "labels": ["System Test"]
        }

        request = service.posts().insert(blogId=blog_id, body=post_body, isDraft=False)
        response = request.execute()
        
        print(f"পোস্ট সফলভাবে পাবলিশ হয়েছে! লিংক: {response.get('url')}")

    except Exception as e:
        print(f"কোথাও একটা ভুল হয়েছে: {e}")

if __name__ == '__main__':
    main()
        

from google.oauth2 import service_account
from googleapiclient.discovery import build
import json
import os

# গিটহাব থেকে চাবি বের করা
creds_json = os.environ.get('BLOGGER_CREDENTIALS')
creds_dict = json.loads(creds_json)

# চাবি দিয়ে রোবটকে রেডি করা
credentials = service_account.Credentials.from_service_account_info(creds_dict, scopes=['https://www.googleapis.com/auth/blogger'])
service = build('blogger', 'v3', credentials=credentials)

try:
    # রোবট তোমার ব্লগের আইডি খুঁজছে এবং ইনভাইটেশন অ্যাকসেপ্ট করছে
    print("Robot is checking the door...")
    
    # ইনভাইটেশন চেক করে সরাসরি অ্যাকসেপ্ট করা
    blog_list = service.users().blogs().list(userId='self').execute()
    print("Success! Robot entered the blog.")
    
    if 'items' in blog_list:
        blog_id = blog_list['items'][0]['id']
        print(f"Blog ID found: {blog_id}")
    else:
        print("Robot accepted the invite, but couldn't fetch the Blog ID yet.")
        
except Exception as e:
    print(f"Error: {e}")
    

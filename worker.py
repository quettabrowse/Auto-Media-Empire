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

# তোমার ব্লগের আইডি (এটা পরে আমরা আসল আইডি দিয়ে পাল্টাব, এখন শুধু কানেকশন চেক করার জন্য)
blog_id = "1234567890" # আপাতত একটা ডামি আইডি দিলাম

try:
    # রোবট নিজে থেকেই ইনভাইটেশন চেক করে অ্যাকসেপ্ট করার চেষ্টা করবে
    print("Robot is checking the door...")
    service.users().get(userId='self').execute()
    print("Success! Robot entered the blog.")
except Exception as e:
    print(f"Error: {e}")
  

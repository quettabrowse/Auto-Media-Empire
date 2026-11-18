import requests

# নতুন ফ্রেশ কোড এবং চাবি দিয়ে আসল মাস্টার চাবি (Refresh Token) আনা হচ্ছে
url = "https://oauth2.googleapis.com/token"
data = {
    "code": "4/0AUn6Hd9KhOsluzC6rbNUcZuOwDzKUpnwaUlmQ_KumN1KoLxdC2P4-TLmr2XWICqvNSGJ6A",
    "client_id": "214264688491-hlnfvq89pfu4a9usnioob56g2io8iu0u.apps.googleusercontent.com",
    "client_secret": "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",
    "redirect_uri": "http://localhost",
    "grant_type": "authorization_code"
}

response = requests.post(url, data=data).json()
print("==========================================")
print("YOUR MASTER KEY DATA:")
print(response)
print("==========================================")

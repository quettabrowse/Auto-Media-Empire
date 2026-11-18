import requests

# তোমার নতুন কোড দিয়ে গুগল থেকে আসল মাস্টার চাবি (Refresh Token) আনা হচ্ছে
url = "https://oauth2.googleapis.com/token"
data = {
    "code": "4/0AUn6Hd9WJGAjyCYAaSfjoxWRKYsLzPhTgD2b7us6s88liovfaXVZPbImLDS92tc6fZ8o2A",
    "client_id": "214264688491-f5o82nca7r2emrm0gtbif29ol2lic7pj.apps.googleusercontent.com",
    "client_secret": "GOCSPX-KVZveM8iCcLbSO_Jnj8vct_Kplx",
    "redirect_uri": "http://localhost",
    "grant_type": "authorization_code"
}

response = requests.post(url, data=data).json()
print("==========================================")
print("YOUR MASTER KEY DATA:")
print(response)
print("==========================================")

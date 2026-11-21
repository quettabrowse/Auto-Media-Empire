import os
import requests

CLIENT_ID = os.environ.get('BLOGGER_CLIENT_ID')
CLIENT_SECRET = os.environ.get('BLOGGER_CLIENT_SECRET')
AUTH_CODE = "4/0AUn6Hd8DhuhKKJoCGCBPlCEdncnwPR0UU70qy7j24kJTrDZ--oc6iojqQH7XeBZPJ31bcA"

response = requests.post("https://oauth2.googleapis.com/token", data={
    "code": AUTH_CODE,
    "client_id": CLIENT_ID,
    "client_secret": CLIENT_SECRET,
    "redirect_uri": "http://localhost",
    "grant_type": "authorization_code"
})

data = response.json()
print("==============================")
if "refresh_token" in data:
    print("PERMANENT REFRESH TOKEN:")
    print(data["refresh_token"])
else:
    print("ERROR:", data)
print("==============================")

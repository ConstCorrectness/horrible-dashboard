import requests

res = requests.post(
    "http://127.0.0.1:8000/clubhouse/send_channel_message",
    json={"channel": "dummy", "message": "hello"}
)
print("Status:", res.status_code)
print("Response:", res.text)

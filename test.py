from openai import OpenAI
from pprint import pprint
from openai import OpenAI, DefaultHttpx2Client
import json


# client = OpenAI(

# )

def log_request(request):
    print("\n=== HTTP Request ===")
    print("Method:", request.method)
    print("URL:", request.url)

    if request.content:
        try:
            body = json.loads(request.content)
            print("Body:")
            print(json.dumps(body, indent=2, ensure_ascii=False))
        except Exception:
            print(request.content)


client = OpenAI(
    api_key="sk-3fDUuB4stGslmZifKVW5AmuRHfYCTVbQg1bgog2x4c3U1YVm",
    base_url="https://zctotoken.com/v1",
    http_client=DefaultHttpx2Client(
        event_hooks={
            "request": [log_request]
        }
    )
)

response = client.chat.completions.create(
    model="gpt-5.6-sol",
    messages=[
        {
            "role": "system",
            "content": "You are a helpful assistant."
        },
        {
            "role": "user",
            "content": "hello"
        }
    ],
    stream=False,
    reasoning_effort="none",
    extra_body={
        "thinking": {
            "type": "disabled"
        }
    }
)

print(response)
"""
Test VLM endpoint — sends raw JPEG bytes to /vlm/describe-bytes
Usage: python testvlm.py [image_path]
"""
import sys
import requests

SERVER = "https://user.tail87d9fe.ts.net"
DEFAULT_IMAGE = "/home/user/vora_project/VORA/VORA/Images/capture_1771504382522.jpg"

image_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_IMAGE

print(f"Image: {image_path}")
print(f"Server: {SERVER}")
print()

with open(image_path, "rb") as f:
    img_bytes = f.read()

print(f"Frame size: {len(img_bytes)} bytes")
print("Sending to /vlm/describe-bytes ...")
print()

res = requests.post(
    f"{SERVER}/vlm/describe-bytes",
    content=img_bytes,
    headers={"Content-Type": "image/jpeg"},
    params={
        "prompt": "Describe ALL objects you see — near and far, big and small. For each: name, color, position (left/center/right).",
        "lang": "en",
        "max_tokens": "500",
    },
    timeout=60,
    verify=False,
)

print(f"Status: {res.status_code}")
if res.status_code == 200:
    data = res.json()
    print(f"Model: {data.get('model')}")
    print(f"eval_duration_ms: {data.get('eval_duration_ms')}")
    print(f"text_len: {len(data.get('text', ''))}")
    print()
    print("=== VLM Description ===")
    print(data.get("text", "(empty)"))
else:
    print("Response:", res.text)



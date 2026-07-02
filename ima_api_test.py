#!/usr/bin/env python3
import json
import os
import urllib.request
import urllib.error

with open(os.path.expanduser('~/.config/ima/client_id'), 'r') as f:
    CLIENT_ID = f.read().strip()
with open(os.path.expanduser('~/.config/ima/api_key'), 'r') as f:
    API_KEY = f.read().strip()

BASE_URL = "https://ima.qq.com"

# Test ALL APIs systematically
tests = [
    ("openapi/note/v1/list_notebook", '{"limit":3}', "list notebooks"),
    ("openapi/wiki/v1/get_addable_knowledge_base_list", '{"cursor":"","limit":3}', "get addable KB"),
    ("openapi/wiki/v1/search_knowledge_base", '{"query":"","cursor":"","limit":3}', "search KB"),
    ("openapi/wiki/v1/search_knowledge", '{"query":"","cursor":"","limit":3,"knowledge_base_id":"test"}', "search knowledge (no KB)"),
]

# Try WITHOUT ima-openapi-ctx header
headers_no_ctx = {
    'ima-openapi-clientid': CLIENT_ID,
    'ima-openapi-apikey': API_KEY,
    'Content-Type': 'application/json',
}

# Try WITH ima-openapi-ctx header
headers_with_ctx = {
    'ima-openapi-clientid': CLIENT_ID,
    'ima-openapi-apikey': API_KEY,
    'ima-openapi-ctx': 'skill_version=1.1.7',
    'Content-Type': 'application/json',
}

print("=" * 60)
print("Testing WITHOUT ima-openapi-ctx header")
print("=" * 60)
for api_path, body, desc in tests:
    url = f"{BASE_URL}/{api_path}"
    req = urllib.request.Request(url, data=body.encode('utf-8'), headers=headers_no_ctx, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = resp.read().decode('utf-8')
            print(f"✅ {desc}: {result[:150]}")
    except urllib.error.HTTPError as e:
        err = e.read().decode()
        print(f"❌ {desc}: HTTP {e.code} - {err[:150]}")

print()
print("=" * 60)
print("Testing WITH ima-openapi-ctx header (skill_version=1.1.7)")
print("=" * 60)
for api_path, body, desc in tests:
    url = f"{BASE_URL}/{api_path}"
    req = urllib.request.Request(url, data=body.encode('utf-8'), headers=headers_with_ctx, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = resp.read().decode('utf-8')
            print(f"✅ {desc}: {result[:150]}")
    except urllib.error.HTTPError as e:
        err = e.read().decode()
        print(f"❌ {desc}: HTTP {e.code} - {err[:150]}")

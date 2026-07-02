#!/usr/bin/env python3
"""Test notes API vs knowledge-base API auth"""
import json
import os
import urllib.request
import urllib.error

with open(os.path.expanduser('~/.config/ima/client_id'), 'r') as f:
    CLIENT_ID = f.read().strip()
with open(os.path.expanduser('~/.config/ima/api_key'), 'r') as f:
    API_KEY = f.read().strip()

BASE_URL = "https://ima.qq.com"

# Test notes API (should work per skill docs)
tests = [
    ("openapi/note/v1/list_notebook", {}, "list notebooks"),
    ("openapi/note/v1/list_note", {"notebook_id": ""}, "list notes"),
    ("openapi/wiki/v1/search_knowledge_base", '{"query":"","cursor":"","limit":3}', "search knowledge base"),
    ("openapi/wiki/v1/get_addable_knowledge_base_list", '{"cursor":"","limit":3}', "get addable KB list"),
]

headers = {
    'ima-openapi-clientid': CLIENT_ID,
    'ima-openapi-apikey': API_KEY,
    'ima-openapi-ctx': 'skill_version=1.1.7',
    'Content-Type': 'application/json',
}

for api_path, body, desc in tests:
    url = f"{BASE_URL}/{api_path}"
    body_str = json.dumps(body)
    req = urllib.request.Request(url, data=body_str.encode('utf-8'), headers=headers, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = resp.read().decode('utf-8')
            print(f"✅ {desc}: SUCCESS")
            print(f"   {result[:200]}")
    except urllib.error.HTTPError as e:
        err_body = e.read().decode()
        print(f"❌ {desc}: HTTP {e.code}")
        print(f"   {err_body[:200]}")
    print()

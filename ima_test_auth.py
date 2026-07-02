#!/usr/bin/env python3
"""IMA API caller - test different auth combinations"""
import json
import os
import sys
import urllib.request
import urllib.error

# Load credentials
with open(os.path.expanduser('~/.config/ima/client_id'), 'r') as f:
    CLIENT_ID = f.read().strip()
with open(os.path.expanduser('~/.config/ima/api_key'), 'r') as f:
    API_KEY = f.read().strip()

BASE_URL = "https://ima.qq.com"

# Test different header combinations
test_cases = [
    {"ima-openapi-clientid": CLIENT_ID, "ima-openapi-apikey": API_KEY, "Content-Type": "application/json"},
    {"ima-openapi-clientid": CLIENT_ID, "ima-openapi-apikey": API_KEY, "ima-openapi-ctx": "skill_version=1.1.7", "Content-Type": "application/json"},
    {"ima-openapi-clientid": CLIENT_ID, "ima-openapi-apikey": API_KEY, "ima-openapi-ctx": "skill_version=1.1.7", "Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
    {"ima-openapi-clientid": CLIENT_ID, "ima-openapi-apikey": API_KEY, "Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
]

API_PATH = "openapi/wiki/v1/search_knowledge_base"
BODY = '{"query":"","cursor":"","limit":3}'

for i, headers in enumerate(test_cases):
    url = f"{BASE_URL}/{API_PATH}"
    req = urllib.request.Request(url, data=BODY.encode('utf-8'), headers=headers, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = resp.read().decode('utf-8')
            print(f"Test {i}: SUCCESS")
            print(result[:200])
            break
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"Test {i}: HTTP {e.code} - {body[:150]}")
    except Exception as e:
        print(f"Test {i}: Error - {e}")

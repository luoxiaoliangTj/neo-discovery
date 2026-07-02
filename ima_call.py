#!/usr/bin/env python3
"""IMA API caller - avoids shell escaping issues with + in API key"""
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

API_PATH = sys.argv[1] if len(sys.argv) > 1 else "openapi/wiki/v1/search_knowledge_base"
BODY = sys.argv[2] if len(sys.argv) > 2 else '{"query":"","cursor":"","limit":5}'

BASE_URL = "https://ima.qq.com"
SKILL_VERSION = "1.1.7"

url = f"{BASE_URL}/{API_PATH}"
headers = {
    'ima-openapi-clientid': CLIENT_ID,
    'ima-openapi-apikey': API_KEY,
    'ima-openapi-ctx': f'skill_version={SKILL_VERSION}',
    'Content-Type': 'application/json',
}

req = urllib.request.Request(url, data=BODY.encode('utf-8'), headers=headers, method='POST')

try:
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = resp.read().decode('utf-8')
        print(result)
except urllib.error.HTTPError as e:
    print(f"HTTP {e.code}: {e.read().decode()}", file=sys.stderr)
    sys.exit(1)
except Exception as e:
    print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)

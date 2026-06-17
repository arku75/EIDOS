#!/usr/bin/env python3
"""
Find MyFRITZ! hostname for FRITZ!Box 5530 Fiber

Usage:
    python3 find_myfritz_hostname.py

This script:
1. Checks if FRITZ!Box web UI is accessible at http://192.168.178.1
2. Attempts to find MyFRITZ! hostname via common paths
3. Provides instructions for manual lookup if automated methods fail
"""

import re
import sys
import time
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

FRITZBOX_IP = "192.168.178.1"
FRITZBOX_URL = f"http://{FRITZBOX_IP}"


def check_fritzbox_access() -> bool:
    """Check if FRITZ!Box web UI is accessible."""
    try:
        r = requests.get(FRITZBOX_URL, timeout=5)
        return r.status_code == 200
    except Exception:
        return False


def find_hostname_via_api() -> Optional[str]:
    """Try to find MyFRITZ! hostname via common API paths."""
    api_paths = [
        "/myfritz/hostname",
        "/internet/myfritz_hostname.lua",
        "/data.lua?xhr=1&page=overview",
        "/data.lua?xhr=1&page=internet",
    ]
    
    for path in api_paths:
        try:
            r = requests.get(f"{FRITZBOX_URL}{path}", timeout=5)
            if r.status_code == 200:
                text = r.text
                # Look for hostname patterns
                hostname_match = re.search(r'([a-zA-Z0-9]{8,}\.myfritz\.net)', text)
                if hostname_match:
                    return hostname_match.group(1)
                # Look for JSON responses
                if "myfritz" in text.lower():
                    json_data = r.json()
                    for key, value in json_data.items():
                        if isinstance(value, str) and "myfritz.net" in value:
                            return value
        except Exception:
            continue
    return None


def find_hostname_via_web_ui() -> Optional[str]:
    """Try to find MyFRITZ! hostname by scraping web UI."""
    try:
        # Login page
        r = requests.get(FRITZBOX_URL, timeout=5)
        soup = BeautifulSoup(r.text, 'html.parser')
        
        # Check for MyFRITZ! section
        myfritz_links = soup.find_all('a', href=re.compile(r'myfritz|internet', re.I))
        for link in myfritz_links:
            href = link.get('href', '')
            if 'myfritz' in href.lower():
                # Follow the link
                r2 = requests.get(f"{FRITZBOX_URL}{href}", timeout=5)
                soup2 = BeautifulSoup(r2.text, 'html.parser')
                hostname_match = re.search(r'([a-zA-Z0-9]{8,}\.myfritz\.net)', soup2.text)
                if hostname_match:
                    return hostname_match.group(1)
        
        # Check overview page
        r3 = requests.get(f"{FRITZBOX_URL}/data.lua?xhr=1&page=overview", timeout=5)
        hostname_match = re.search(r'([a-zA-Z0-9]{8,}\.myfritz\.net)', r3.text)
        if hostname_match:
            return hostname_match.group(1)
            
    except Exception:
        pass
    
    return None


def manual_instructions():
    """Print manual instructions for finding MyFRITZ! hostname."""
    print("""
=== Manual Instructions for MyFRITZ! Hostname ===

1. Open your web browser and go to: http://192.168.178.1
2. Log in to your FRITZ!Box (if prompted)
3. Navigate to:
   - German: "Internet" → "MyFRITZ!-Konto"
   - English: "Internet" → "MyFRITZ! Account"
   - Spanish: "Internet" → "Cuenta MyFRITZ!"
4. Look for a field labeled:
   - "MyFRITZ! Internet address"
   - "MyFRITZ! hostname"
   - "MyFRITZ! domain"
5. The hostname will look like: abc123def.myfritz.net
6. Note it down and provide it to EIDOS

If you can't find it, check:
- Is MyFRITZ! service enabled? (Should be by default)
- Are you logged in with admin privileges?
- Try different language settings in the FRITZ!Box UI
""")


def main():
    print("🔍 Searching for MyFRITZ! hostname...")
    
    if not check_fritzbox_access():
        print(f"❌ Cannot access FRITZ!Box at {FRITZBOX_URL}")
        print("   - Make sure you're connected to the FRITZ!Box network")
        print("   - Try accessing http://192.168.178.1 in your browser")
        return
    
    print(f"✅ FRITZ!Box accessible at {FRITZBOX_URL}")
    
    # Try automated methods
    hostname = find_hostname_via_api() or find_hostname_via_web_ui()
    
    if hostname:
        print(f"\n🎉 Found MyFRITZ! hostname: {hostname}")
        print(f"\n📋 Next steps:")
        print(f"   1. Test SSH connection: ssh luka@{hostname}")
        print(f"   2. Configure HEAVY_NODE_URL: export HEAVY_NODE_URL='http://{hostname}:11434'")
        print(f"   3. EIDOS will use Mac's deepseek-r1:14b model for heavy inference")
        
        # Write to file for EIDOS to read
        with open(Path.home() / ".eidos" / 
#!/usr/bin/env python3
"""내 계정에 있는 캘린더 목록과 주소를 찾아 준다. (설정할 때 한 번만 쓴다)

사용법: CALDAV_USER=아이디 CALDAV_PASSWORD=앱비밀번호 python3 find_calendars.py naver
        (아이폰 캘린더면 naver 대신 icloud)
"""
import base64
import os
import re
import sys
import urllib.parse
import urllib.request

SERVERS = {
    "naver": "https://caldav.calendar.naver.com/",
    "icloud": "https://caldav.icloud.com/",
}

auth = base64.b64encode(f"{os.environ['CALDAV_USER']}:{os.environ['CALDAV_PASSWORD']}".encode()).decode()


def propfind(url, prop, depth="0"):
    body = (f'<?xml version="1.0"?><d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
            f"<d:prop>{prop}</d:prop></d:propfind>").encode()
    req = urllib.request.Request(url, data=body, method="PROPFIND", headers={
        "Authorization": f"Basic {auth}", "Depth": depth, "Content-Type": "application/xml"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8")


def hrefs_in(xml, tag):
    block = re.search(rf"<(?:\w+:)?{tag}[^>]*>(.*?)</(?:\w+:)?{tag}>", xml, re.S | re.I)
    return re.findall(r"<(?:\w+:)?href>([^<]+)</(?:\w+:)?href>", block.group(1), re.I) if block else []


base = SERVERS[sys.argv[1] if len(sys.argv) > 1 else "naver"]
principal = urllib.parse.urljoin(base, hrefs_in(propfind(base, "<d:current-user-principal/>"), "current-user-principal")[0])
home = urllib.parse.urljoin(principal, hrefs_in(propfind(principal, "<c:calendar-home-set/>"), "calendar-home-set")[0])
listing = propfind(home, "<d:displayname/><d:resourcetype/>", depth="1")

print("찾은 캘린더:")
for resp in re.findall(r"<(?:\w+:)?response[^>]*>(.*?)</(?:\w+:)?response>", listing, re.S | re.I):
    if not re.search(r"<(?:\w+:)?calendar\s*/>", resp, re.I):
        continue
    href = re.search(r"<(?:\w+:)?href>([^<]+)</", resp, re.I).group(1)
    name = re.search(r"<(?:\w+:)?displayname[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</", resp, re.S | re.I)
    print(f"  - {name.group(1).strip() if name else '(이름 없음)'}  →  {urllib.parse.urljoin(home, href)}")
print(f"\n클라우드 네트워크 허용 목록에 넣을 주소: {urllib.parse.urlparse(home).hostname}")

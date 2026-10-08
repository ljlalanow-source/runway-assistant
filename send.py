#!/usr/bin/env python3
"""표준 입력으로 받은 브리핑을 텔레그램으로 보낸다.

사용법: python3 send.py [마지막_update_id] < message.txt
마지막_update_id를 주면, 보내기에 성공한 뒤 그 메시지까지 '읽음' 처리해서
다음 날 브리핑에 같은 할 일이 또 나오지 않게 한다.
"""
import json
import os
import sys
import urllib.parse
import urllib.request

api = f"https://api.telegram.org/bot{os.environ['TELEGRAM_TOKEN']}"
text = sys.stdin.read().strip()
if not text:
    sys.exit("보낼 내용이 없어요")

body = urllib.parse.urlencode({"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": text}).encode()
with urllib.request.urlopen(f"{api}/sendMessage", data=body, timeout=30) as r:
    if not json.load(r)["ok"]:
        sys.exit("보내기 실패")
print("보냈어요")

if len(sys.argv) > 1 and sys.argv[1] not in ("", "None", "null"):
    urllib.request.urlopen(f"{api}/getUpdates?offset={int(sys.argv[1]) + 1}", timeout=30).read()
    print("할 일 읽음 처리 완료")

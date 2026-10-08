#!/usr/bin/env python3
"""비서의 아침 브리핑 재료(날씨, 일정, 할 일)를 모아 JSON으로 출력한다.

설정: config.json (위치, 출퇴근 시간, 캘린더 목록)
비밀값(환경 변수): TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
                  캘린더를 쓰면 CALDAV_USER, CALDAV_PASSWORD 또는 config.json의 url_env에 적은 이름
사용법: python3 collect.py [YYYY-MM-DD]   (날짜를 빼면 오늘, 한국 시간)
"""
import base64
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
CONFIG = json.loads((Path(__file__).parent / "config.json").read_text(encoding="utf-8"))
WEEKDAYS_KO = "월화수목금토일"
DAY_CODES = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
ANNIVERSARY = re.compile(r"생일|생신|기념일|birthday|anniversary", re.I)


def http(url, method="GET", body=None, headers=None):
    # 일시적인 오류(요청 과다, 연결 끊김)는 잠깐 쉬었다가 두 번 더 시도한다
    headers = {"User-Agent": "runway-assistant/1.0", **(headers or {})}
    for wait in (5, 20, None):
        try:
            req = urllib.request.Request(url, data=body, method=method, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8")
        except (urllib.error.HTTPError, urllib.error.URLError) as e:
            permanent = isinstance(e, urllib.error.HTTPError) and e.code not in (429, 500, 502, 503, 504)
            if permanent or wait is None:
                raise
            time.sleep(wait)


# ---------- 날씨 ----------

def weather(day):
    w = CONFIG["weather"]
    home = w["home"]
    work = w.get("work") or home

    def fetch(place):
        url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={place['lat']}&longitude={place['lon']}"
            "&hourly=apparent_temperature,precipitation_probability,weather_code"
            "&past_days=1&forecast_days=2&timezone=Asia%2FSeoul"
        )
        h = json.loads(http(url))["hourly"]
        return {t: (a, p, c) for t, a, p, c in zip(
            h["time"], h["apparent_temperature"], h["precipitation_probability"], h["weather_code"])}

    def hours(hhmm):
        # "08:30" → 8시와 9시의 평균, "08:00" → 8시만
        h, m = map(int, hhmm.split(":"))
        return [f"{h:02d}", f"{h + 1:02d}"] if m else [f"{h:02d}"]

    sources = {"home": fetch(home)}
    sources["work"] = fetch(work) if work is not home else sources["home"]
    weekend = day.weekday() >= 5
    if weekend:
        slots = [("오전", "home", hours(w.get("weekend_am", "10:00"))),
                 ("오후", "home", hours(w.get("weekend_pm", "15:00")))]
    else:
        slots = [("출근길", "home", hours(w.get("commute_go", "08:30"))),
                 ("퇴근길", "work", hours(w.get("commute_back", "18:00")))]

    def feel(d, src, hs):
        vals = [src[f"{d}T{h}:00"][0] for h in hs if f"{d}T{h}:00" in src]
        return round(sum(vals) / len(vals)) if vals else None

    def rain(d, src, hs):
        vals = [src[f"{d}T{h}:00"][1] or 0 for h in hs if f"{d}T{h}:00" in src]
        return max(vals) if vals else 0

    today, yday = day.isoformat(), (day - timedelta(days=1)).isoformat()
    result = {"구분": "주말" if weekend else "평일", "시간대": []}
    for label, key, hs in slots:
        src = sources[key]
        result["시간대"].append({
            "이름": label,
            "체감온도": feel(today, src, hs),
            "어제_체감온도": feel(yday, src, hs),
            "비_확률": rain(today, src, hs),
        })
    daytime = [f"{h:02d}" for h in range(7, 21)]
    home_src = sources["home"]
    result["낮_최대_비_확률"] = rain(today, home_src, daytime)
    result["낮_날씨코드"] = [home_src[f"{today}T{h}:00"][2] for h in daytime if f"{today}T{h}:00" in home_src]
    result["날씨코드_설명"] = "WMO: 0 맑음, 1 대체로 맑음, 2 구름 조금, 3 흐림, 45/48 안개, 51-57 이슬비, 61-67 비, 71-77 눈, 80-82 소나기, 95-99 뇌우"
    return result


# ---------- 캘린더 ----------

def ics_events(text):
    text = re.sub(r"\r?\n[ \t]", "", text)  # 접힌 줄 펴기
    events = []
    for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", text, re.S):
        ev = {"exdates": set()}
        for line in block.strip().splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            name, _, params = key.partition(";")
            if name == "SUMMARY":
                ev["summary"] = value.replace("\\,", ",").replace("\\;", ";").replace("\\n", " ").strip()
            elif name in ("DTSTART", "DTEND", "RECURRENCE-ID"):
                ev[name] = parse_dt(value, params)
            elif name == "RRULE":
                ev["rrule"] = dict(p.split("=", 1) for p in value.split(";") if "=" in p)
            elif name == "EXDATE":
                for v in value.split(","):
                    ev["exdates"].add(parse_dt(v, params)[0])
        if "DTSTART" in ev:
            events.append(ev)
    return events


def parse_dt(value, params):
    """(날짜, 'HH:MM' 또는 None) 반환. 종일 일정은 시간이 None."""
    value = value.strip()
    if "T" not in value:
        return datetime.strptime(value[:8], "%Y%m%d").date(), None
    dt = datetime.strptime(value.rstrip("Z")[:15], "%Y%m%dT%H%M%S")
    if value.endswith("Z"):
        dt = dt.replace(tzinfo=timezone.utc).astimezone(KST)
    return dt.date(), dt.strftime("%H:%M")


def months_between(a, b):
    return (b.year - a.year) * 12 + b.month - a.month


def nth_weekday_ok(d, spec):
    m = re.fullmatch(r"([+-]?\d*)([A-Z]{2})", spec)
    if not m or DAY_CODES.index(m.group(2)) != d.weekday():
        return False
    if not m.group(1):
        return True
    n = int(m.group(1))
    if n > 0:
        return (d.day - 1) // 7 + 1 == n
    next_month = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
    return ((next_month - d).days - 1) // 7 + 1 == -n


def rule_matches(start, rule, d):
    interval = int(rule.get("INTERVAL", 1))
    freq = rule.get("FREQ")
    byday = rule.get("BYDAY", "").split(",") if rule.get("BYDAY") else []
    bymonthday = [int(x) for x in rule.get("BYMONTHDAY", "").split(",") if x]
    bymonth = [int(x) for x in rule.get("BYMONTH", "").split(",") if x]
    if freq == "DAILY":
        return (d - start).days % interval == 0
    if freq == "WEEKLY":
        days = [DAY_CODES.index(x[-2:]) for x in byday] or [start.weekday()]
        weeks = ((d - timedelta(days=d.weekday())) - (start - timedelta(days=start.weekday()))).days // 7
        return d.weekday() in days and weeks % interval == 0
    if freq == "MONTHLY":
        if months_between(start, d) % interval:
            return False
        if bymonthday:
            return d.day in bymonthday
        if byday:
            return any(nth_weekday_ok(d, x) for x in byday)
        return d.day == start.day
    if freq == "YEARLY":
        if (d.year - start.year) % interval:
            return False
        if d.month not in (bymonth or [start.month]):
            return False
        if byday:
            return any(nth_weekday_ok(d, x) for x in byday)
        return d.day in (bymonthday or [start.day])
    return False


def occurs_on(ev, d):
    start, _ = ev["DTSTART"]
    if d < start or d in ev["exdates"]:
        return False
    rule = ev.get("rrule")
    if not rule:
        end = ev.get("DTEND", (start, None))[0]
        if ev["DTSTART"][1] is None and end > start:  # 여러 날 종일 일정 (끝 날짜는 포함 안 됨)
            return start <= d < end
        return d == start
    if "UNTIL" in rule and d > parse_dt(rule["UNTIL"], "")[0]:
        return False
    if not rule_matches(start, rule, d):
        return False
    if "COUNT" in rule:
        n = sum(1 for i in range((d - start).days + 1)
                if rule_matches(start, rule, start + timedelta(days=i))
                and start + timedelta(days=i) not in ev["exdates"])
        return n <= int(rule["COUNT"])
    return True


def caldav_events(url):
    """CalDAV 캘린더(네이버, 아이클라우드)의 일정을 한 번에 받아 온다."""
    user = os.environ["CALDAV_USER"]
    auth = base64.b64encode(f"{user}:{os.environ['CALDAV_PASSWORD']}".encode()).decode()
    headers = {"Authorization": f"Basic {auth}", "Depth": "1", "Content-Type": "application/xml"}
    listing = http(url, "PROPFIND",
                   b'<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop><d:getetag/></d:prop></d:propfind>',
                   headers)
    hrefs = re.findall(r"<(?:\w+:)?href>([^<]+\.ics)</(?:\w+:)?href>", listing, re.I)
    if not hrefs:
        return []
    # 일정 파일을 하나씩 받으면 수백 번 요청하게 되니, 목록을 한 번에 요청한다(multiget)
    body = ('<?xml version="1.0"?><c:calendar-multiget xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
            "<d:prop><c:calendar-data/></d:prop>"
            + "".join(f"<d:href>{h}</d:href>" for h in hrefs)
            + "</c:calendar-multiget>")
    return ics_events(html.unescape(http(url, "REPORT", body.encode(), headers)))


def calendar(day):
    today, soon = [], []
    before = CONFIG.get("anniversary_days_before", 7)
    for cal in CONFIG.get("calendars", []):
        if cal["type"] == "caldav":
            events = caldav_events(cal["url"])
        else:  # "ics": 구글 캘린더 비공개 주소 같은 구독 주소
            events = ics_events(http(os.environ[cal["url_env"]]))
        # 반복 일정의 일부만 바뀐 경우(RECURRENCE-ID): 원래 반복에서는 그 날짜를 빼고 바뀐 일정만 쓴다
        moved = {e["RECURRENCE-ID"][0] for e in events if "RECURRENCE-ID" in e}
        for e in events:
            if "rrule" in e:
                e["exdates"] |= moved
        for e in events:
            item = {"캘린더": cal["label"], "표시": cal.get("mark", ""),
                    "제목": e.get("summary", "(제목 없음)"), "시간": e["DTSTART"][1]}
            if occurs_on(e, day):
                today.append(item)
            if ANNIVERSARY.search(item["제목"]) and occurs_on(e, day + timedelta(days=before)):
                soon.append({**item, "날짜": (day + timedelta(days=before)).isoformat(), "남은_날": before})
    today.sort(key=lambda x: x["시간"] or "")
    return {"오늘_일정": today, "다가오는_기념일": soon}


# ---------- 텔레그램 할 일 ----------

def todos():
    url = f"https://api.telegram.org/bot{os.environ['TELEGRAM_TOKEN']}/getUpdates"
    updates = json.loads(http(url))["result"]
    chat = int(os.environ["TELEGRAM_CHAT_ID"])
    items, last = [], None
    for u in updates:
        last = u["update_id"]
        m = u.get("message") or {}
        text = (m.get("text") or "").strip()
        if m.get("chat", {}).get("id") == chat and text and not text.startswith("/"):
            items.append(text)
    return {"할_일": items, "마지막_update_id": last}


if __name__ == "__main__":
    day = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else datetime.now(KST).date()
    out = {"날짜": day.isoformat(), "요일": WEEKDAYS_KO[day.weekday()] + "요일"}
    jobs = [("날씨", lambda: weather(day)), ("텔레그램", todos)]
    if CONFIG.get("calendars"):
        jobs.insert(1, ("캘린더", lambda: calendar(day)))
    for key, fn in jobs:
        try:
            out[key] = fn()
        except Exception as e:  # 한 군데가 실패해도 나머지로 브리핑은 보낸다
            out[key] = {"오류": f"{type(e).__name__}: {e}"}
    print(json.dumps(out, ensure_ascii=False, indent=2))

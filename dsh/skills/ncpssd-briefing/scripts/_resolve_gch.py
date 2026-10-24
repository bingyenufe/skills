import io
import json
import os
import re
import sys
import time
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

TARGETS = ["中国社会科学", "经济研究", "管理世界", "经济学（季刊）",
           "世界经济", "中国工业经济", "数量经济技术经济研究", "财贸经济"]


def norm(s):
    s = s.replace("（", "(").replace("）", ")")
    s = s.replace("：", ":")
    return re.sub(r"\s+", "", s)


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def parse(h):
    out = []
    for m in re.finditer(r"gch=([0-9A-Za-z]+)&nav=1[^>]*>(.*?)</a>", h, re.S):
        t = re.sub(r"<[^>]+>", "", m.group(2))
        t = re.sub(r"^\s*\d+、", "", t).strip()
        if t:
            out.append((t, m.group(1)))
    return out


want = {norm(t): t for t in TARGETS}
found = {}
for page in range(1, 128):
    try:
        h = get("https://m.ncpssd.cn/journal/list?page=%d" % page)
    except Exception as e:
        print("page %d failed: %s" % (page, e))
        continue
    items = parse(h)
    for name, gch in items:
        n = norm(name)
        if n in want and n not in found:
            found[n] = {"name": name, "gch": gch}
            print("FOUND page %-3d %-22s gch=%s" % (page, name, gch))
    if len(found) == len(want):
        break
    time.sleep(0.2)

print("\n结果:")
for t in TARGETS:
    n = norm(t)
    print("  %-14s %s" % (t, found.get(n, "NOT FOUND")))
json.dump(found, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "_gch_map.json"), "w",
                      encoding="utf-8"), ensure_ascii=False, indent=2)

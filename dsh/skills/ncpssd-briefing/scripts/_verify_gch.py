import io
import json
import re
import sys
import time
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

MAP = [
    ("中国社会科学", "81908X"),
    ("经济研究", "95645X"),
    ("管理世界", "95499X"),
    ("经济学（季刊）", "84307X"),
    ("世界经济", "92713X"),
    ("中国工业经济", "93800A"),
    ("数量经济技术经济研究", "94503X"),
    ("财贸经济", "96197X"),
]


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


print("%-14s %-7s %-7s %-6s %-6s %s" % ("期刊", "gch", "2025期数", "最新期", "篇数", "最新期首篇"))
for name, gch in MAP:
    nums = []
    try:
        h = get("https://m.ncpssd.cn/journal/journalHandler?op=getnum&gch=%s&years=2025" % gch)
        nums = [int(x) for x in re.findall(r'data-val="(\d+)"', h)]
    except Exception as e:
        print(name, gch, "getnum fail", e)
        continue
    try:
        toc = get("https://m.ncpssd.cn/journal/details?gch=%s" % gch)
    except Exception as e:
        print(name, gch, "toc fail", e)
        continue
    ids = list(dict.fromkeys(re.findall(r'data-id="([^"]+)"', toc)))
    nm = re.search(r"<dt>\s*([^<]+?)\s*</dt>", toc)
    num_now = re.search(r"id=\"span_num\"[^>]*>\s*([^<]+?)\s*<", toc)
    first = re.search(r"AddHandleCount\(this,\s*'[^']*',\s*'[^']*',\s*\d+,\s*-?\d+,\s*'[^']*',\s*'[^']*',\s*'[^']*',\s*'([^']{4,60})'", toc)
    print("%-14s %-7s %-7s %-6s %-6s %s" % (
        name, gch, len(nums), (num_now.group(1) if num_now else "?"),
        len(ids), (first.group(1)[:40] if first else "?")))
    time.sleep(0.5)

import glob
import io
import json
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
_HERE = os.path.dirname(os.path.abspath(__file__))
_parts = _HERE.split(os.sep)
_ROOT = os.sep.join(_parts[:_parts.index(".dsh")]) if ".dsh" in _parts else os.path.dirname(_HERE)
manifests = sorted(glob.glob(os.path.join(_ROOT, "snap", "*", "manifest.json")))
if not manifests:
    print("snap/*/manifest.json 不存在: 还没跑 collect.py --journal ?")
    sys.exit(0)
grand = 0
for mf in manifests:
    m = json.load(open(mf, encoding="utf-8"))
    entries = m["entries"]
    grand += len(entries)
    print("== %s (%s) run_date=%s window=%s target_months=%s total=%d" % (
        m["journal"], m["short"], m["run_date"], m.get("window"),
        m["target_months"], len(entries)))
    if m.get("window_note"):
        print("   note: %s" % m["window_note"])
    for it in m["issues"]:
        sub = [e for e in entries
               if e["journal"] == it["journal"] and e["issue"] == it["issue"]]
        ym = "%d-%02d" % (it["year"], it["month"])
        first = (sub[0]["title"][:28] if sub else "")
        print("  %-7s 第%-3s 篇数%-4s %-10s 倍数%-6s %s" % (
            ym, it["issue"], it["count"], it["status"],
            it.get("toc_ratio"), first))
print("\nsnap 下共 %d 篇条目 (%d 刊)" % (grand, len(manifests)))

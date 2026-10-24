import glob
import io
import os
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
_HERE = os.path.dirname(os.path.abspath(__file__))
_parts = _HERE.split(os.sep)
_ROOT = os.sep.join(_parts[:_parts.index(".dsh")]) if ".dsh" in _parts else os.path.dirname(_HERE)
for f in sorted(glob.glob(os.path.join(_ROOT, "snap", "*", "raw", "toc__*.html"))):
    html = open(f, encoding="utf-8").read()
    raw = re.findall(r'data-id="[^"]+"', html)
    uniq = set(re.findall(r'data-id="([^"]+)"', html))
    links = re.findall(r"openDetail\('/Literature/articleinfo", html)
    print("%-72s data-id=%3d uniq=%3d openDetail=%3d ratio=%.2f" % (
        os.path.basename(f)[:72], len(raw), len(uniq), len(links),
        (len(raw) / len(uniq)) if uniq else 0))

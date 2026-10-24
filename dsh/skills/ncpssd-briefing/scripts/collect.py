#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NCPSSD（国家哲学社会科学文献中心）文献元数据采集脚本 —— 仅 Python 标准库, 串行执行。

只采集标题/作者/关键词/摘要, 不采全文。以"刊"为单位组织输出: 每刊一个子目录,
重采某刊只清空该刊目录, 其他刊的采集结果原样保留（供分批续跑）。

已验证端点 (2026-10, 免登录):
  目录页(移动版):  GET  {base_m}/journal/details?gch={gch}&years={year}&num={issue}
                   页面内 data-id 即文章 lngid (如 JJYJ2026007001 / CMJJ2026009001)
  期次列表:        GET  {base_m}/journal/journalHandler?op=getnum&gch={gch}&years={year}
                   返回 JSON, 含该年各期 num
  单篇元数据:      POST {base_www}/articleinfoHandler/getjournalarticletable
                   body: {"lngid": "...", "type": "journalArticle", "pageType": 1}
                   返回含 titlec 标题 / showwriter 作者 / remarkc 摘要 / keywordc 关键词
                         mediac 刊名 / years 年 / num 期 / beginpage / endpage / publishdate / issn / doi
  单篇页面(供链接): GET  {base_www}/Literature/articleinfo?id={lngid}&type=journalArticle

用法:
  python collect.py                                  # config 全部期刊, 按序逐刊采集
  python collect.py --journal 经济研究               # 单刊(刊名/short/gch 均可), 可多次传
  python collect.py --journal jjyj --year 2026 --month 8   # 调试: 单刊单月(期号按 freq 推)
  python collect.py --journal jjyj --year 2026 --issue 7   # 调试: 单刊单期(显式期号)
  python collect.py --probe [--journal X]            # 诊断: 各刊窗口年份已上线期号与最新一期样例

产出 (每刊一个子目录, 目录以 config 的 short 命名):
  snap/<short>/manifest.json   该刊 issues 清单 + entries 元数据(含 file 指向单篇文件)
  snap/<short>/articles/NN.md  逐篇单文件 —— 写简报时一次只读一篇, 不要整读 manifest
  snap/<short>/papers.md       易读版, 仅供人工核对
  snap/<short>/raw/*.html      目录页原始 HTML
采集窗口: 运行月 M 减去该刊 config 里 months 指定的偏移(如 [2,3] 即 M-2、M-3)。
非月刊若窗口内两月映射到同一期号, 只采一次, manifest.window_note 注明。
snap/state.md 是批次进度表, 由简报执行者维护, 本脚本不读不写。
每次运行无记忆: 不与任何历史数据比较。整次运行 0 篇成功条目时以非零退出。
"""
import argparse
import json
import math
import os
import re
import shutil
import sys
import time
import urllib.request
from datetime import date

_HERE = os.path.dirname(os.path.abspath(__file__))


def _project_root():
    r"""数据目录(snap/reports)所在的项目根。

    脚本可直接放在 <项目>\scripts\ 下, 也可放在 skill 里
    (<项目>\.dsh\skills\<name>\scripts\)。后者向上找到 .dsh 的父目录。
    环境变量 NCPSSD_ROOT 可强制指定。
    """
    env = os.environ.get("NCPSSD_ROOT")
    if env:
        return os.path.abspath(env)
    parts = _HERE.split(os.sep)
    if "skills" in parts and ".dsh" in parts:
        i = parts.index(".dsh")
        return os.sep.join(parts[:i]) if i else _HERE
    return os.path.dirname(_HERE)


ROOT = _project_root()
SNAP = os.path.join(ROOT, "snap")
CFG_PATH = os.path.join(_HERE, "config.json")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


# ---------- 基础 IO ----------

def load_cfg():
    with open(CFG_PATH, encoding="utf-8") as f:
        return json.load(f)


def _request(url, cfg, data=None):
    last = None
    for attempt in range(cfg.get("retries", 2) + 1):
        try:
            req = urllib.request.Request(url, data=data, headers={
                "User-Agent": UA,
                "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
            })
            if data is not None:
                req.add_header("Content-Type", "application/json; charset=utf-8")
            with urllib.request.urlopen(req, timeout=cfg.get("timeout_sec", 30)) as r:
                return r.read()
        except Exception as e:
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise last


def http_get(url, cfg):
    raw = _request(url, cfg)
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")


def http_post_json(url, payload, cfg):
    raw = _request(url, cfg, data=json.dumps(payload).encode("utf-8"))
    return json.loads(raw.decode("utf-8"))


def polite(cfg):
    time.sleep(cfg.get("polite_delay_sec", 1.0))


def save_raw(dirpath, url, html, tag):
    os.makedirs(dirpath, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", url)[:120]
    path = os.path.join(dirpath, "%s__%s.html" % (tag, safe))
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


# ---------- 业务逻辑 ----------

def short_of(j):
    s = j.get("short") or j["gch"]
    return re.sub(r"[^A-Za-z0-9_.-]", "_", s)


def months_for(j):
    """该刊窗口: 运行月 M 减去 months 偏移, 返回 [(year, month, offset), ...] 近月在前."""
    today = date.today()
    out = []
    for off in sorted(j.get("months", [2, 3])):
        m = today.month - off
        y = today.year
        if m <= 0:
            m += 12
            y -= 1
        out.append((y, m, off))
    return out


def issue_for_month(freq, month):
    """月刊=当月; 双月刊=ceil(month/2); 季刊=ceil(month/3)."""
    if freq == "bimonthly":
        return math.ceil(month / 2)
    if freq == "quarterly":
        return math.ceil(month / 3)
    return month  # monthly / 默认


def build_plans(j):
    """该刊采集计划: [(year, month, issue), ...].

    窗口内两月映射到同一期号时只保留第一次(较近月), 并生成 note."""
    plans, notes, seen = [], [], set()
    for (y, m, off) in months_for(j):
        issue = issue_for_month(j.get("freq", "monthly"), m)
        key = (y, issue)
        if key in seen:
            notes.append("M-%d(%d-%02d)与较近月同为%d年第%d期, 未重复采集"
                         % (off, y, m, y, issue))
            continue
        seen.add(key)
        plans.append((y, m, issue))
    return plans, notes


def get_issue_list(gch, year, cfg):
    """该年已上线的各期 num 列表 (int), 失败返回 []."""
    url = "%s/journal/journalHandler?op=getnum&gch=%s&years=%d&langType=1" % (
        cfg["base_m"], gch, year)
    try:
        j = json.loads(http_get(url, cfg))
    except Exception:
        return []
    # 接口返回 {"result":true,"code":200,"data":"<li data-val=\"7\" dtgch=...><span>7期</span></li>..."}
    data = j.get("data") if isinstance(j, dict) else j
    if isinstance(data, str):
        nums = [int(x) for x in re.findall(r'data-val="(\d+)"', data)]
    else:
        nums = []
        for it in (data or []):
            v = it.get("num") if isinstance(it, dict) else it
            try:
                nums.append(int(v))
            except (TypeError, ValueError):
                pass
    return sorted(nums)


def fetch_toc(gch, year, issue, cfg):
    """目录页 HTML + 文中全部 data-id (去重保序) + 文章链接数(完整性旁证)."""
    url = "%s/journal/details?gch=%s&years=%d&num=%d" % (
        cfg["base_m"], gch, year, issue)
    html = http_get(url, cfg)
    ids = list(dict.fromkeys(re.findall(r'data-id="([^"]+)"', html)))
    n_links = len(re.findall(r"openDetail\('/Literature/articleinfo", html))
    return url, html, ids, n_links


def fetch_article(lngid, cfg):
    url = "%s/articleinfoHandler/getjournalarticletable" % cfg["base_www"]
    j = http_post_json(url, {"lngid": lngid, "type": "journalArticle",
                             "pageType": 1}, cfg)
    d = j.get("data", j)
    if isinstance(d, dict):
        return d
    return None


def article_page_url(lngid, cfg):
    return "%s/Literature/articleinfo?id=%s&type=journalArticle" % (
        cfg["base_www"], lngid)


def clean(s):
    """去 HTML 标签/多余空白."""
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", "", str(s))
    s = s.replace("&nbsp;", " ").replace("&amp;", "&")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def make_entry(lngid, d, journal, year, issue, pub):
    """实测字段名: titlec 标题 / showwriter 作者 / keywordc 关键词 /
    remarkc 摘要 / mediac 刊名 / years 年 / num 期 / publishdate 出版日期."""
    return {
        "lngid": lngid,
        "journal": journal,
        "year": year,
        "issue": issue,
        "publish": pub,
        "title": clean(d.get("titlec") or d.get("title")),
        "authors": clean(d.get("showwriter") or d.get("authorc") or d.get("author")),
        "keywords": clean(d.get("keywordc") or d.get("keywords") or d.get("keyword")),
        "abstract": clean(d.get("remarkc") or d.get("abstractc") or d.get("abstract")),
        "issn": d.get("issn") or "",
        "doi": d.get("doi") or "",
        "url": article_page_url(lngid, cfg_global),
    }


cfg_global = None  # main() 中注入, 供 make_entry 用


def process_issue(j, year, month, issue, cfg, jdir):
    """采一刊一期. 返回 (md_lines, entries, issue_status, toc_ratio)."""
    gch = j["gch"]
    name = j["name"]
    rawdir = os.path.join(jdir, "raw")
    url, html, ids, n_links = fetch_toc(gch, year, issue, cfg)
    save_raw(rawdir, url, html, "toc")
    lines = ["## %s %d年第%d期" % (name, year, issue), ""]
    entries = []
    if not ids:
        lines.append("> 目录为空: 该期尚未上线或期号不对.")
        return lines, entries, "empty_toc", None
    pub = ""
    issue_checked = False  # 期号校验只在第一篇拿到数据的条目上做一次
    for lngid in ids:
        polite(cfg)
        try:
            d = fetch_article(lngid, cfg)
        except Exception as e:
            lines.append("- [获取失败] id=%s (%s)" % (lngid, e))
            continue
        if not d:
            lines.append("- [无数据] id=%s" % lngid)
            continue
        if not issue_checked:
            # 期号校验: 防止网站对不存在的期号回退到最新一期, 导致错标
            issue_checked = True
            def _i(v):
                try:
                    return int(float(str(v)))
                except (TypeError, ValueError):
                    return None
            ay, an = _i(d.get("years")), _i(d.get("num"))
            if (ay is not None and ay != year) or (an is not None and an != issue):
                lines.append("> 网站返回 %s年第%s期, 非请求的 %d年第%d期, 已跳过."
                             % (d.get("years") or "?", d.get("num") or "?", year, issue))
                return lines, [], "mismatch", None
        art_pub = (d.get("publishdate") or d.get("publishDate") or "")
        pub = pub or art_pub          # 期级出版日期: 取首篇的, 供标题行使用
        e = make_entry(lngid, d, name, year, issue, art_pub or pub)
        if not e["title"]:
            e["title"] = "(标题缺失) id=%s" % lngid
        entries.append(e)
        lines += [
            "### %s" % e["title"],
            "- 作者: %s" % (e["authors"] or "—"),
            "- 关键词: %s" % (e["keywords"] or "—"),
            "- 摘要: %s" % (e["abstract"] or "—"),
            "- 期次: %s %d年第%d期%s" % (name, year, issue,
                                          ("  出版日期 %s" % pub) if pub else ""),
            "- ID: %s" % lngid,
            "- 链接: %s" % e["url"],
            "",
        ]
    status = "ok" if entries else "no_data"
    ratio = (n_links / len(ids)) if ids else None
    if ratio is not None and abs(ratio - round(ratio)) > 1e-6:
        status = "toc_odd"  # 链接数与文章数不成整数倍, 页面结构可能变了
    lines.insert(1, "共 %d 篇%s" % (len(entries),
                                     ("  出版日期 %s" % pub) if pub else ""))
    return lines, entries, status, ratio


def write_article_file(jdir, idx, e):
    """单篇文件 articles/NN.md —— 写简报时一次只读这一篇."""
    adir = os.path.join(jdir, "articles")
    os.makedirs(adir, exist_ok=True)
    fname = "%02d.md" % idx
    body = "\n".join([
        "# 第%d篇 | %s" % (idx, e["title"]),
        "",
        "- 出处: %s %d年第%d期 (出版日期 %s)" % (e["journal"], e["year"],
                                                  e["issue"], e["publish"] or "?"),
        "- 作者: %s" % (e["authors"] or "—"),
        "- 关键词: %s" % (e["keywords"] or "—"),
        "- 链接: %s" % e["url"],
        "",
        "## 摘要",
        "",
        e["abstract"] or "(无摘要)",
        "",
    ])
    with open(os.path.join(adir, fname), "w", encoding="utf-8") as f:
        f.write(body)
    return "articles/" + fname


def collect_journal(cfg, j, plans, notes=None):
    """采集一本期刊并落盘. 返回 (ok_count, total_entries, issue_rows)."""
    jdir = os.path.join(SNAP, short_of(j))
    if os.path.isdir(jdir):
        shutil.rmtree(jdir, ignore_errors=True)
    os.makedirs(os.path.join(jdir, "raw"), exist_ok=True)
    md = ["# NCPSSD 采集: %s (%s)" % (j["name"], short_of(j)),
          "", "run_date %s" % date.today().isoformat(), ""]
    issues, entries = [], []
    for (year, month, issue) in plans:
        sys.stderr.write(">> %s %d-%02d (第%d期) ...\n" % (j["name"], year, month, issue))
        sys.stderr.flush()
        try:
            lines, ents, status, ratio = process_issue(j, year, month, issue, cfg, jdir)
        except Exception as e:
            lines = ["## %s %d年第?期" % (j["name"], year), "",
                     "> 采集失败: %s" % e]
            ents, status, ratio = [], "error", None
        md += lines
        md.append("")
        issues.append({"journal": j["name"], "year": year, "month": month,
                       "issue": issue, "status": status, "count": len(ents),
                       "toc_ratio": round(ratio, 2) if ratio else None})
        entries += ents
    for idx, e in enumerate(entries, 1):
        e["file"] = write_article_file(jdir, idx, e)
    ym = []
    for (yr, mth, _) in plans:
        t = "%d-%02d" % (yr, mth)
        if t not in ym:
            ym.append(t)
    manifest = {
        "run_date": date.today().isoformat(),
        "journal": j["name"],
        "short": short_of(j),
        "window": sorted(j.get("months", [2, 3])),
        "target_months": ym,
        "window_note": "; ".join(notes) if notes else "",
        "issues": issues,
        "entries": entries,
    }
    with open(os.path.join(jdir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    with open(os.path.join(jdir, "papers.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    ok = sum(1 for e in entries
             if e["title"] and not e["title"].startswith("(标题缺失)"))
    detail = ", ".join("%d-%02d第%d期:%s×%d" % (it["year"], it["month"], it["issue"],
                                                 it["status"], it["count"])
                       for it in issues)
    print("done: %s ok=%d/%d (%s)" % (short_of(j), ok, len(entries), detail))
    return ok, len(entries), issues


def run(cfg, selected, forced_plans=None):
    """按序逐刊采集. forced_plans: 调试模式下单刊单期 [(year, month, issue)]."""
    global cfg_global
    cfg_global = cfg
    total_ok = 0
    for j in selected:
        if forced_plans:
            plans = forced_plans
            notes = []
        else:
            plans, notes = build_plans(j)
            for n in notes:
                sys.stderr.write("   (skip: %s)\n" % n)
        if not plans:
            print("done: %s 窗口为空" % short_of(j))
            continue
        ok, _, _ = collect_journal(cfg, j, plans, notes)
        total_ok += ok
    return total_ok


def probe(cfg, selected):
    """诊断: 各刊窗口年份的已上线期号 + 官网当前默认目录(最新一期)样例."""
    global cfg_global
    cfg_global = cfg
    probedir = os.path.join(SNAP, "_probe")
    if os.path.isdir(probedir):
        shutil.rmtree(probedir, ignore_errors=True)
    rawdir = os.path.join(probedir, "raw")
    for j in selected:
        plans, _ = build_plans(j)
        years = sorted({y for (y, _, _) in plans} | {date.today().year}, reverse=True)
        print("== %s (%s, gch=%s, months=%s)" % (
            j["name"], short_of(j), j["gch"], j.get("months", [2, 3])))
        for y in years:
            nums = get_issue_list(j["gch"], y, cfg)
            print("  %d 年: 已上线期号 %s" % (y, nums or "?"))
        # 不带 years/num 时网站返回最新一期, 用它验证 gch 与解析
        url = "%s/journal/details?gch=%s" % (cfg["base_m"], j["gch"])
        html = http_get(url, cfg)
        save_raw(rawdir, url, html, "probe_toc")
        ids = list(dict.fromkeys(re.findall(r'data-id="([^"]+)"', html)))
        n_links = len(re.findall(r"openDetail\('/Literature/articleinfo", html))
        print("  当前默认目录: %d 篇(链接 %d 条), 前3个 id: %s" % (len(ids), n_links, ids[:3]))
        if ids:
            polite(cfg)
            d = fetch_article(ids[0], cfg)
            if d:
                print("  样例首篇: %s年第%s期 | %s | %s" % (
                    d.get("years") or "?", d.get("num") or "?",
                    (d.get("titlec") or "")[:40],
                    (d.get("publishdate") or "")))
        polite(cfg)


def resolve_journal(cfg, token):
    for j in cfg["journals"]:
        if token in (j["name"], short_of(j), j["gch"]):
            return j
    sys.exit("config 中无期刊: %s" % token)


def main():
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--journal", action="append",
                    help="刊名/short/gch, 可多次; 省略=config 全部期刊")
    ap.add_argument("--probe", action="store_true",
                    help="诊断: 窗口年份已上线期号与最新一期样例 (不写各刊采集目录)")
    ap.add_argument("--year", type=int, help="调试: 指定年(配 --journal)")
    ap.add_argument("--month", type=int, help="调试: 指定月, 按 freq 推期号")
    ap.add_argument("--issue", type=int, help="调试: 显式期号(优先于 --month)")
    a = ap.parse_args()
    cfg = load_cfg()
    global cfg_global
    cfg_global = cfg

    selected = [resolve_journal(cfg, t) for t in a.journal] if a.journal else cfg["journals"]

    if a.probe:
        probe(cfg, selected)
        return
    forced = None
    if a.year or a.month or a.issue:
        if len(selected) != 1:
            sys.exit("调试模式需 --journal 恰好指定一刊, 并配 --year + --month/--issue")
        j = selected[0]
        year = a.year or date.today().year
        if a.issue:
            issue, month = a.issue, (a.month or a.issue)
        elif a.month:
            month = a.month
            issue = issue_for_month(j.get("freq", "monthly"), month)
        else:
            sys.exit("调试模式需 --month 或 --issue")
        forced = [(year, month, issue)]
    ok = run(cfg, selected, forced_plans=forced)
    if ok == 0:
        sys.exit("collect: 本次 0 篇成功条目, 各期状态见对应 snap/<short>/manifest.json")


if __name__ == "__main__":
    main()

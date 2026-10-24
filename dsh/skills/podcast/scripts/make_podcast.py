#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""make_podcast.py — 把简报对谈稿合成一集 mp3（edge-tts，免费无 key）。

稿本格式（纯文本行，见 SKILL.md）：
  # 注释行（含 `# 第N篇 标题` 定位标记），合成时忽略
  [A] 台词……          A 主播（默认 zh-CN-YunxiNeural 男声）
  [B] 台词……          B 主播（默认 zh-CN-XiaoxiaoNeural 女声）
  紧跟台词后的无标签行并入上一句（同一次发声）。

用法：
  python make_podcast.py --script <数据目录>\\reports\\20261019\\script_jjyj_20261019.md
        [--out <同目录>\\podcast_jjyj_20261019.mp3] [--voice-a V] [--voice-b V] [--jobs 3]

增量：每段文本+音色做哈希缓存于 <out同级>\\cache_<out名>\\，只合成缺失段；
改稿后重跑仅重配变化句。网络失败退出码非零，直接重跑即可续。
"""
import argparse
import asyncio
import hashlib
import re
import sys
from pathlib import Path

DEFAULT_VOICE_A = "zh-CN-YunxiNeural"
DEFAULT_VOICE_B = "zh-CN-XiaoxiaoNeural"
SEG_RE = re.compile(r"^\[([AB])\]\s*(.*)$")
CHARS_PER_MIN = 260  # 中文新闻播报语率估算，仅用于打印时长


def parse_script(path: Path):
    """返回 [(speaker, text), ...]，保持稿本顺序。"""
    segs = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = SEG_RE.match(line)
        if m:
            text = m.group(2).strip()
            if text:
                segs.append([m.group(1), text])
            # 空标签行（只有 [A]）忽略
        elif segs:
            segs[-1][1] += line  # 续行并入上一段
    return segs


async def synth_one(text: str, voice: str, out: Path, sem, retries=3):
    import edge_tts

    async with sem:
        for attempt in range(retries):
            try:
                await asyncio.wait_for(
                    edge_tts.Communicate(text=text, voice=voice).save(str(out)),
                    timeout=180,
                )
                if out.stat().st_size > 512:
                    return True
                raise RuntimeError("empty/truncated audio")
            except Exception as e:
                if attempt == retries - 1:
                    print(f"FAIL seg={out.name} voice={voice}: {e}", file=sys.stderr)
                    return False
                await asyncio.sleep(2 * (attempt + 1))
    return False


async def synth_all(todo, jobs):
    sem = asyncio.Semaphore(max(1, jobs))
    return await asyncio.gather(*(synth_one(t, v, f, sem) for t, v, f in todo))


def main():
    ap = argparse.ArgumentParser(description="briefing 对谈稿 -> mp3 播客")
    ap.add_argument("--script", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=None,
                    help="默认：稿本名 script_ -> podcast_，扩展名 .mp3")
    ap.add_argument("--voice-a", default=DEFAULT_VOICE_A)
    ap.add_argument("--voice-b", default=DEFAULT_VOICE_B)
    ap.add_argument("--jobs", type=int, default=3, help="并发合成数（默认3，别调大）")
    args = ap.parse_args()

    segs = parse_script(args.script)
    if not segs:
        sys.exit("稿本里没有 [A]/[B] 台词行，退出")

    out = args.out or args.script.with_name(
        args.script.name.rsplit(".", 1)[0].replace("script_", "podcast_", 1) + ".mp3"
    )
    cache = out.with_name(f"cache_{out.stem}")
    cache.mkdir(parents=True, exist_ok=True)

    voices = {"A": args.voice_a, "B": args.voice_b}
    parts, todo = [], []
    for i, (spk, text) in enumerate(segs):
        key = hashlib.sha1(f"{voices[spk]}|{text}".encode("utf-8")).hexdigest()[:16]
        f = cache / f"{i:04d}_{voices[spk]}_{key}.mp3"
        parts.append(f)
        if not f.exists() or f.stat().st_size == 0:
            todo.append((text, voices[spk], f))

    print(f"segments={len(segs)} to_synth={len(todo)} cached={len(segs) - len(todo)}")
    if todo:
        ok = asyncio.run(synth_all(todo, args.jobs))
        if not all(ok):
            sys.exit(f"synth_failed={ok.count(False)} 重跑本命令即可续（已合成段自动跳过）")

    # edge-tts 输出统一为 mp3 帧流（无 ID3 头），字节级拼接可直接播放
    with open(out, "wb") as w:
        for f in parts:
            w.write(f.read_bytes())

    chars = sum(len(t) for _, t in segs)
    print(f"OK out={out} bytes={out.stat().st_size} chars={chars} est_min={chars / CHARS_PER_MIN:.1f}")


if __name__ == "__main__":
    main()

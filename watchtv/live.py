import os
import re
import sys
import time

import requests

# ========== 填写源的地址 ==========
URL_LIST = [
    #"https://lihansong.top/iptv/src/yf.txt",
    "https://raw.githubusercontent.com/bang359/dsj/refs/heads/main/dsjcs.txt",
    
    "https://raw.githubusercontent.com/djshxjsh233/migu/refs/heads/main/live.txt",
]

# ========== 分组映射：左边是源里的分组名（不区分大小写），右边是输出时改后的分组名 ==========
GROUP_MAP = {
    # ---- iptv/src/yf ----
    #"咪咕央视2": "HS咪咕",
    #"咪咕央视": "HS咪咕",
    # ---- bang359/dsj ----
    "📺咪咕视频": "咪咕视频",
    # ---- djshxjsh233/migu ----
    "央视频道": "HS咪咕",
    "卫视频道": "HS咪咕",
    "cgtn": "HS咪咕",
    "教育频道": "HS咪咕",
    "本地频道": "HS咪咕",
    "浙江": "Z视介",
}

# 说明：不做存活检测（Actions 在美国，访问国内源不稳定），存活筛选交给宝塔端。
# 本脚本负责：拉取上游源(重试/超时/缓存兜底) -> 分组映射 -> 合并去重 -> 输出 live.m3u8

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "cache")
OUTPUT_PATH = os.path.join(BASE_DIR, "live.m3u8")

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; iptv-sync/1.0)"}
RETRIES = 3
TIMEOUT = (10, 30)  # (连接超时, 读取超时) 秒

GROUP_MAP_LOWER = {k.strip().lower(): v for k, v in GROUP_MAP.items()}


def fetch(url: str, idx: int):
    """拉取一个源。成功则写缓存；失败则读缓存。返回 (文本或None, 是否来自缓存)。"""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_file = os.path.join(CACHE_DIR, f"source_{idx}.txt")

    last_err = None
    for attempt in range(1, RETRIES + 1):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            resp.raise_for_status()
            resp.encoding = resp.encoding if resp.encoding and resp.encoding.lower() != "iso-8859-1" else "utf-8"
            text = resp.text
            if not text.strip():
                raise ValueError("返回内容为空")
            with open(cache_file, "w", encoding="utf-8") as f:
                f.write(text)
            return text, False
        except Exception as e:  # noqa: BLE001
            last_err = e
            print(f"⚠️ 第 {attempt}/{RETRIES} 次拉取失败 {url}：{e}")
            if attempt < RETRIES:
                time.sleep(3 * attempt)

    print(f"❌ 拉取 {url} 最终失败：{last_err}")
    if os.path.exists(cache_file):
        print("   ↳ 使用上次缓存")
        with open(cache_file, "r", encoding="utf-8") as f:
            return f.read(), True
    return None, False


def parse_any(text: str):
    res = []
    extinf_line = None
    current_group = None
    for raw_line in text.splitlines():
        ln = raw_line.strip()
        if not ln:
            continue
        if ln.startswith("#EXTINF:"):
            extinf_line = ln
            continue
        if extinf_line is not None and not ln.startswith("#"):
            res.append((extinf_line, ln))
            extinf_line = None
            continue
        if "," in ln and not ln.startswith("#"):
            name_part, url_part = (s.strip() for s in ln.split(",", 1))
            if url_part == "#genre#":
                current_group = name_part
                continue
            if current_group:
                fake_ext = f'#EXTINF:-1 group-title="{current_group}",{name_part}'
            else:
                fake_ext = f"#EXTINF:-1,{name_part}"
            res.append((fake_ext, url_part))
    return res


def get_channel_name(extinf):
    return extinf.split(",")[-1].strip() if "," in extinf else ""


def get_group_title(extinf):
    m = re.search(r'group-title="([^"]+)"', extinf)
    return m.group(1).strip() if m else ""


def main():
    group_bucket = {v: [] for v in GROUP_MAP.values()}
    seen = set()
    ok_sources = 0

    for idx, url in enumerate(URL_LIST):
        text, from_cache = fetch(url, idx)
        if text is None:
            continue
        ok_sources += 1
        try:
            channels = parse_any(text)
        except Exception as e:  # noqa: BLE001
            print(f"⚠️ 解析 {url} 失败：{e}")
            continue
        for extinf, play_url in channels:
            ch_group = get_group_title(extinf).lower()
            if ch_group not in GROUP_MAP_LOWER:
                continue
            output_group = GROUP_MAP_LOWER[ch_group]
            ch_name = get_channel_name(extinf)
            key = (ch_name, play_url)
            if key not in seen:
                seen.add(key)
                group_bucket[output_group].append((ch_name, play_url))

    total_cnt = sum(len(v) for v in group_bucket.values())
    print(f"✅ 采集完成：成功 {ok_sources}/{len(URL_LIST)} 个源，共 {total_cnt} 个频道")
    for gname, ch_list in group_bucket.items():
        print(f"  - {gname}: {len(ch_list)} 个频道")

    # 保护：没有任何频道就不覆盖旧文件，并让任务显示失败以便发现问题
    if total_cnt == 0:
        print("❌ 没有提取到任何频道，保留原有 live.m3u8，不覆盖")
        sys.exit(1)

    lines = ["#EXTM3U"]
    for gname, ch_list in group_bucket.items():
        for cname, curl in ch_list:
            lines.append(f'#EXTINF:-1 group-title="{gname}",{cname}')
            lines.append(curl)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"✅ 已输出 m3u8：{OUTPUT_PATH}")


if __name__ == "__main__":
    main()

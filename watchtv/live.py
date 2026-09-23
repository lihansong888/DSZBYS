import requests
import re
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
# ========== 填写源的地址 ==========
URL_LIST = [
    "http://rihou.cc:567/gggg.nzk",
    "https://raw.githubusercontent.com/vbskycn/iptv/refs/heads/master/tv/iptv4.txt",
    "https://raw.githubusercontent.com/bj123sd/hycg/refs/heads/main/tv.txt",
    
]
# ========== 分组映射：左边是源里的分组名，右边是输出时改后的分组名 ==========
GROUP_MAP = {
    "央卫咪咕": "hansong咪咕-央卫",
    "音乐": "hansong-音乐直播",
    "央卫": "hansong-央卫直播",
}
# ===================== 粗筛配置（Github Action环境，放宽参数）=====================
MAX_WORKERS = 2    # 并发数，不要大于3，防止Action超时/被源站封禁
CHECK_TIMEOUT = 5  # 链接检测超时时间（秒），放宽到5秒
RETRY_TIMES = 2    # 失败重试次数，放宽到2次
# ========================================================================

def check_url_alive(url):
    """粗筛：仅判断链接是否能建立连接，不完整拉流，减轻压力"""
    for _ in range(RETRY_TIMES):
        try:
            # HEAD请求，不下载流内容，速度快、流量小
            resp = requests.head(url, timeout=CHECK_TIMEOUT, allow_redirects=True)
            if resp.status_code < 400:
                return True
        except Exception:
            pass
        # HEAD失败，降级用GET只拉取少量数据
        try:
            resp = requests.get(url, timeout=CHECK_TIMEOUT, allow_redirects=True, stream=True)
            resp.raw.read(512) # 只读取前512字节，不完整下载
            if resp.status_code < 400:
                return True
        except Exception:
            continue
    return False

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
        if ',' in ln and not ln.startswith("#"):
            sp = ln.split(',',1)
            name_part = sp[0].strip()
            url_part = sp[1].strip()
            if url_part == "#genre#":
                current_group = name_part
                continue
            if current_group:
                fake_ext = f'#EXTINF:-1 group-title="{current_group}",{name_part}'
            else:
                fake_ext = f'#EXTINF:-1,{name_part}'
            res.append((fake_ext, url_part))
    return res

def get_channel_name(extinf):
    if "," in extinf:
        return extinf.split(",")[-1].strip()
    return ""

def get_group_title(extinf):
    m = re.search(r'group-title="([^"]+)"', extinf)
    if m:
        return m.group(1).strip()
    return ""

def main():
    # 用改后的分组名初始化空列表
    group_bucket = {v: [] for v in GROUP_MAP.values()}
    seen = set()
    for url in URL_LIST:
        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            channels = parse_any(resp.text)
            for extinf, play_url in channels:
                ch_name = get_channel_name(extinf)
                ch_group = get_group_title(extinf)
                # 只保留 GROUP_MAP 里有的分组，其他全屏蔽
                if ch_group not in GROUP_MAP:
                    continue
                # 关键：查映射表，把源分组名改成输出分组名
                output_group = GROUP_MAP[ch_group]
                item_key = (ch_name, play_url)
                if item_key not in seen:
                    seen.add(item_key)
                    group_bucket[output_group].append((ch_name, play_url))
        except Exception as e:
            print(f"⚠️ 拉取 {url} 失败：{e}")
    total_raw_cnt = sum(len(v) for v in group_bucket.values())
    print(f"✅采集完成，原始频道总数：{total_raw_cnt} 个")
    for gname, ch_list in group_bucket.items():
        print(f"  - {gname}: {len(ch_list)} 个频道")

    # ===================== 新增：Github云端粗筛 =====================
    all_channels_flat = []
    for gname, ch_list in group_bucket.items():
        for cname, curl in ch_list:
            all_channels_flat.append((gname, cname, curl))

    valid_channels = []
    print(f"\n🔍 开始粗筛检测，并发数：{MAX_WORKERS}")
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_map = {executor.submit(check_url_alive, curl): (gname, cname, curl) for gname, cname, curl in all_channels_flat}
        for future in as_completed(future_map):
            gname, cname, curl = future_map[future]
            try:
                is_alive = future.result()
                if is_alive:
                    valid_channels.append((gname, cname, curl))
                    print(f"✅ {cname}")
                else:
                    print(f"❌ {cname} 粗筛判定失效，剔除")
            except Exception as e:
                print(f"⚠️ {cname} 检测异常，保留给宝塔复核")
                valid_channels.append((gname, cname, curl))

    print(f"\n✅粗筛完成，原始{total_raw_cnt}条，粗筛保留{len(valid_channels)}条")
    # 重新回填到分组结构
    final_group = {v: [] for v in GROUP_MAP.values()}
    for gname, cname, curl in valid_channels:
        final_group[gname].append((cname, curl))
    # ======================================================================

    # 输出m3u8文件
    out_dir = os.path.dirname(os.path.abspath(__file__))
    output_m3u = ["#EXTM3U"]
    for gname, ch_list in final_group.items():
        for cname, curl in ch_list:
            # 输出时用改后的分组名
            fake_ext = f'#EXTINF:-1 group-title="{gname}",{cname}'
            output_m3u.append(fake_ext)
            output_m3u.append(curl)
    m3u8_path = os.path.join(out_dir, "live.m3u8")
    with open(m3u8_path, "w", encoding="utf-8") as f:
        f.write("\n".join(output_m3u))
    print(f"✅已输出粗筛后的 m3u8：{m3u8_path}")

if __name__ == "__main__":
    main()

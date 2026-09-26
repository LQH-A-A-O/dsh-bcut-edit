"""下载完整版 ffmpeg（Windows x64 GPL，含 libx264 + libass + 图片解码器）。

为什么要完整版：录屏软件、剪辑软件自带的 ffmpeg 往往是精简版 ——
没有 libx264、没有 subtitles/drawtext 滤镜，有的**连 png/jpeg 解码器都砍了**，
结果是"能跑但剪不了图、烧不了字幕"。

下载源：BtbN 的 GitHub release。单连接约 128 KB/s，**8 连接约 370-470 KB/s**，
所以这里用分块并行下载。支持断点续传：中断了重新跑就行。

默认装到 <skill>/bin/ffmpeg.exe（skill 自己找得到）；也可用环境变量 BCUT_FFMPEG 指定。

    python get_ffmpeg.py
"""
import os
import threading
import time
import zipfile

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.environ.get('BCUT_FFMPEG_DIR') or os.path.join(HERE, 'bin')
EXTRACT_DIR = OUT_DIR
ZIP_PATH = os.path.join(OUT_DIR, 'ffmpeg-win64-gpl.zip')
API = 'https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/tags/latest'
CONNS = 8


def pick_asset():
    r = requests.get(API, timeout=30)
    r.raise_for_status()
    cands = [a for a in r.json().get('assets', [])
             if 'win64' in a['name'] and a['name'].endswith('.zip')]
    if not cands:
        raise RuntimeError('BtbN release 里没找到 win64 zip')
    # 要 GPL 版（libx264 是 GPL）；shared 版是一堆 DLL，不好用
    gpl = [a for a in cands if 'gpl' in a['name'] and 'shared' not in a['name']]
    pick = min(gpl or cands, key=lambda x: x['size'])
    print('选中: %s  (%.1f MB)' % (pick['name'], pick['size'] / 1048576))
    return pick


def already_extracted():
    return os.path.exists(os.path.join(EXTRACT_DIR, 'ffmpeg.exe'))


def download_parallel(url, total, conns=CONNS):
    parts_dir = ZIP_PATH + '.parts'
    os.makedirs(parts_dir, exist_ok=True)
    chunk = total // conns
    print('分块下载（%d 连接）-> %s' % (conns, parts_dir), flush=True)

    def work(i):
        a = i * chunk
        b = total - 1 if i == conns - 1 else (i + 1) * chunk - 1
        p = os.path.join(parts_dir, '%02d.part' % i)
        have = os.path.getsize(p) if os.path.exists(p) else 0
        if have >= b - a + 1:
            return
        with requests.get(url, stream=True, timeout=60,
                          headers={'Range': 'bytes=%d-%d' % (a + have, b)}) as r:
            r.raise_for_status()
            with open(p, 'ab') as f:
                for c in r.iter_content(262144):
                    f.write(c)

    ts = [threading.Thread(target=work, args=(i,)) for i in range(conns)]
    t0 = time.time()
    for t in ts:
        t.start()
    while any(t.is_alive() for t in ts):
        time.sleep(15)
        got = sum(os.path.getsize(os.path.join(parts_dir, f))
                  for f in os.listdir(parts_dir) if f.endswith('.part'))
        el = max(time.time() - t0, 1)
        rate = got / 1024 / el
        print('  %.1f/%.1f MB  %.0f KB/s  剩约 %.1f 分'
              % (got / 1048576, total / 1048576, rate,
                 (total - got) / 1024 / max(rate, 1) / 60), flush=True)
    for t in ts:
        t.join()
    print('合并…', flush=True)
    with open(ZIP_PATH, 'wb') as out:
        for i in range(conns):
            with open(os.path.join(parts_dir, '%02d.part' % i), 'rb') as f:
                while True:
                    blk = f.read(8 * 1024 * 1024)
                    if not blk:
                        break
                    out.write(blk)
    print('合并完成: %.1f MB' % (os.path.getsize(ZIP_PATH) / 1048576))


def extract():
    os.makedirs(EXTRACT_DIR, exist_ok=True)
    with zipfile.ZipFile(ZIP_PATH) as z:
        wanted = [n for n in z.namelist() if '/bin/' in n and n.endswith('.exe')]
        for n in wanted:
            tgt = os.path.join(EXTRACT_DIR, os.path.basename(n))
            with open(tgt, 'wb') as f:
                f.write(z.read(n))
            print('  解出 %s (%.1f MB)' % (os.path.basename(n),
                                          os.path.getsize(tgt) / 1048576))


if __name__ == '__main__':
    if already_extracted():
        print('已经装好了:', EXTRACT_DIR)
    else:
        a = pick_asset()
        size = a['size']
        if os.path.exists(ZIP_PATH) and os.path.getsize(ZIP_PATH) != size:
            os.remove(ZIP_PATH)
        download_parallel(a['browser_download_url'], size)
        extract()
    import subprocess
    exe = os.path.join(EXTRACT_DIR, 'ffmpeg.exe')
    print('\n' + subprocess.run([exe, '-version'], capture_output=True, text=True,
                                encoding='utf-8', errors='replace').stdout.splitlines()[0])
    print('OK ->', EXTRACT_DIR)
    print('（skill 会自动发现它；也可以设 BCUT_FFMPEG 指向别处）')

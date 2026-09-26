"""ffmpeg 剪辑底座 —— 剪切 / 拼接 / 转场 / 字幕 / 混音 / 导出。

需要**完整版** ffmpeg（libx264 + libass + 图片解码器）。
用 `python get_ffmpeg.py` 下到 <skill>/bin/，或者：
  - 环境变量 `BCUT_FFMPEG` 指向 ffmpeg.exe 或它所在目录
  - 或者把 ffmpeg 放进 PATH

⚠️ 录屏软件附带的精简版 ffmpeg 常常**缺图片解码器和字幕滤镜**，
   能跑但剪不了图、烧不了字幕。`report()` 会把实际能力探测出来。
"""
import json
import os
import re
import shutil
import subprocess

import env_paths

_CAP = {}


def ffmpeg_path():
    return env_paths.ffmpeg()


def ffprobe_path():
    return env_paths.ffprobe()


def is_full_build() -> bool:
    """用的是不是完整版（有 libx264 / libass / 图片解码器）"""
    c = capabilities()
    return ('libx264' in c['encoders'] and 'subtitles' in c['filters']
            and 'png' in c['decoders'])


def capabilities() -> dict:
    """探测可用的编码器/滤镜（结果缓存）"""
    if _CAP:
        return _CAP
    ff = ffmpeg_path()
    if not ff:
        _CAP.update(dict(ffmpeg=None, encoders=set(), filters=set()))
        return _CAP
    def kinds(flag):
        # -encoders/-decoders 行: " V....D libx264   H.264 ..."（首列是能力标志位）
        # -filters  行: " .. ass   V->V   ..." —— 标志位列**宽度不固定**（2~3 位），
        #             所以别写死 {3}，否则 ass/subtitles 这些会漏掉。
        pat = re.compile(r'^\s*([VASFXBD.]{3,8})\s+(\S+)') if flag != '-filters' \
            else re.compile(r'^\s*([TSCXAPN.|]{2,4})\s+(\S+)\s+\S+->\S+')
        try:
            out = subprocess.run([ff, '-hide_banner', flag], capture_output=True, text=True,
                                 encoding='utf-8', errors='replace', timeout=60).stdout
        except Exception:
            return set()
        got = set()
        for line in out.splitlines():
            m = pat.match(line)
            if m:
                got.add(m.group(2))
        return got
    _CAP.update(dict(ffmpeg=ff,
                     encoders=kinds('-encoders'),
                     decoders=kinds('-decoders'),
                     filters=kinds('-filters')))
    return _CAP


# 图片扩展名 -> ffmpeg 解码器名（精简版 ffmpeg 经常缺图片解码器）
IMAGE_DECODER = {'.png': 'png', '.jpg': 'mjpeg', '.jpeg': 'mjpeg',
                 '.webp': 'webp', '.bmp': 'bmp', '.gif': 'gif'}


def can_decode_image(path) -> bool:
    dec = IMAGE_DECODER.get(os.path.splitext(path)[1].lower())
    if not dec:
        return True
    return dec in capabilities()['decoders']


def require_image_decoder(path):
    dec = IMAGE_DECODER.get(os.path.splitext(path)[1].lower())
    if dec and dec not in capabilities()['decoders']:
        raise RuntimeError(
            '这个 ffmpeg 没有 %s 解码器，读不了图片：%s\n'
            '（有些录屏专用的精简版连 png/jpeg 解码器都砍了）\n'
            '装完整版：python "%s"' % (dec, path, os.path.join(env_paths.SKILL_ROOT, 'get_ffmpeg.py')))


def report() -> str:
    c = capabilities()
    if not c['ffmpeg']:
        return '找不到 ffmpeg。跑 tools\\get_ffmpeg.py 下载完整版。'
    decs = c['decoders']
    L = ['ffmpeg: %s' % c['ffmpeg'],
         '  完整版(有 libx264/libass): %s' % ('是' if is_full_build() else '否'),
         '  libx264 / libx265 : %s / %s' % ('libx264' in c['encoders'], 'libx265' in c['encoders']),
         '  GPU: nvenc=%s amf=%s mf=%s' % ('h264_nvenc' in c['encoders'],
                                           'h264_amf' in c['encoders'],
                                           'h264_mf' in c['encoders']),
         '  图片解码 png/jpeg/webp: %s/%s/%s' % ('png' in decs, 'mjpeg' in decs, 'webp' in decs),
         '  字幕滤镜 subtitles/drawtext/ass: %s/%s/%s' % (
             'subtitles' in c['filters'], 'drawtext' in c['filters'], 'ass' in c['filters'])]
    return '\n'.join(L)


def pick_encoder(prefer_gpu=False) -> str:
    """挑一个能用的 H.264 编码器名。

    默认优先 libx264：质量稳定、不挑输入。
    ⚠️ 踩过的坑：h264_nvenc 虽然在本机可用，但**对 lavfi 源/奇数尺寸会直接失败**，
       而且失败时 ffmpeg 会留下一个 0 字节的输出文件 —— 所以调用方必须检查返回码。
    """
    c = capabilities()['encoders']
    order = (['h264_nvenc', 'libx264'] if prefer_gpu else ['libx264', 'h264_nvenc']) + \
            ['h264_amf', 'h264_mf']
    for e in order:
        if e in c:
            return e
    raise RuntimeError('没有可用的 H.264 编码器。装完整版 ffmpeg（get_ffmpeg.py）')


def _enc_args(gpu=False, crf=18, preset='medium'):
    """挑编码器：优先 libx264（质量稳定），可选 nvenc"""
    c = capabilities()
    if gpu and 'h264_nvenc' in c['encoders']:
        return ['-c:v', 'h264_nvenc', '-preset', 'p5', '-rc', 'vbr', '-cq', str(crf), '-b:v', '0']
    if 'libx264' in c['encoders']:
        return ['-c:v', 'libx264', '-crf', str(crf), '-preset', preset, '-pix_fmt', 'yuv420p']
    if 'h264_nvenc' in c['encoders']:
        return ['-c:v', 'h264_nvenc', '-preset', 'p5', '-cq', str(crf), '-b:v', '0', '-pix_fmt', 'yuv420p']
    if 'h264_mf' in c['encoders']:
        return ['-c:v', 'h264_mf', '-b:v', '8M']
    raise RuntimeError('没有可用的 H.264 编码器。装完整版 ffmpeg（tools\\get_ffmpeg.py）')


def run(args, timeout=3600, quiet=False):
    ff = ffmpeg_path()
    if not ff:
        raise RuntimeError('找不到 ffmpeg')
    cmd = [ff, '-hide_banner', '-y'] + args
    r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8',
                       errors='replace', timeout=timeout)
    if r.returncode != 0 and not quiet:
        tail = '\n'.join((r.stderr or '').splitlines()[-18:])
        raise RuntimeError('ffmpeg 失败 (exit %d)\n%s\n%s' % (r.returncode, ' '.join(cmd[:12]), tail))
    return r


# ---------------------------------------------------------------- 基础操作
def grab_frame(src, out_jpg, at_ms=0, width=None) -> bool:
    """取一帧存成 jpg（封面用）"""
    args = ['-ss', '%.3f' % (at_ms / 1000.0), '-i', src, '-frames:v', '1']
    if width:
        args += ['-vf', 'scale=%d:-2' % width]
    args += ['-q:v', '3', out_jpg]
    try:
        run(args, timeout=120)
        return os.path.exists(out_jpg)
    except Exception:
        return False


def make_still(image, out, dur_ms, width, height, fps=30, motion='none', zoom=1.12) -> str:
    """图片 -> 视频片段。motion: none | zoom_in | zoom_out | pan_left | pan_right

    有了这一步，"图片+音乐宣传片"就不用去猜必剪的图片素材 schema。
    """
    require_image_decoder(image)
    d = max(dur_ms, 1) / 1000.0
    frames = int(d * fps)
    base = ('scale=%d:%d:force_original_aspect_ratio=decrease,'
            'pad=%d:%d:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1' % (width, height, width, height))
    if motion == 'zoom_in':
        vf = base + (',zoompan=z=\'min(zoom+0.0008,%.3f)\':d=%d:s=%dx%d:fps=%d'
                     % (zoom, frames, width, height, fps))
    elif motion == 'zoom_out':
        vf = base + (',zoompan=z=\'if(lte(zoom,1.0),%.3f,max(1.001,zoom-0.0008))\':d=%d:s=%dx%d:fps=%d'
                     % (zoom, frames, width, height, fps))
    elif motion in ('pan_left', 'pan_right'):
        dx = 'iw-iw/zoom' if motion == 'pan_left' else '0'
        vf = base + (',zoompan=z=%.3f:x=\'%s\':y=\'ih/2-(ih/zoom/2)\':d=%d:s=%dx%d:fps=%d'
                     % (zoom, dx, frames, width, height, fps))
    else:
        vf = base + ',fps=%d' % fps
    # ⚠️ 必须补一条静音音轨：所有片段都带音轨，后面的 acrossfade / amix 才不会 EINVAL
    run(['-loop', '1', '-i', image,
         '-f', 'lavfi', '-t', '%.3f' % d, '-i', 'anullsrc=r=48000:cl=stereo',
         '-t', '%.3f' % d, '-vf', vf, '-r', str(fps),
         '-map', '0:v', '-map', '1:a', '-shortest'] + _enc_args() +
        ['-c:a', 'aac', '-b:a', '192k', '-ac', '2', out])
    return out


def cut(src, out, start_ms=0, dur_ms=None, gpu=False, crf=18) -> str:
    """精确剪切（重编码，保证关键帧对齐）"""
    args = ['-ss', '%.3f' % (start_ms / 1000.0), '-i', src]
    if dur_ms:
        args += ['-t', '%.3f' % (dur_ms / 1000.0)]
    args += _enc_args(gpu=gpu, crf=crf) + ['-c:a', 'aac', '-b:a', '192k', out]
    run(args)
    return out


def concat(paths, out, reencode=True, gpu=False, crf=18) -> str:
    """按顺序拼接。reencode=False 时用 concat demuxer（快，但要求编码参数一致）"""
    if not reencode:
        lst = out + '.txt'
        with open(lst, 'w', encoding='utf-8') as f:
            for p in paths:
                f.write("file '%s'\n" % p.replace('\\', '/').replace("'", "'\\''"))
        run(['-f', 'concat', '-safe', '0', '-i', lst, '-c', 'copy', out])
        os.remove(lst)
        return out
    args = []
    for p in paths:
        args += ['-i', p]
    n = len(paths)
    fc = ''.join('[%d:v][%d:a]' % (i, i) for i in range(n)) + \
         'concat=n=%d:v=1:a=1[v][a]' % n
    args += ['-filter_complex', fc, '-map', '[v]', '-map', '[a]'] + _enc_args(gpu=gpu, crf=crf) + \
            ['-c:a', 'aac', '-b:a', '192k', out]
    run(args)
    return out


# 必剪的中文转场名 -> ffmpeg xfade 类型（视觉上最接近的）
TRANSITION_XFADE = {
    '折叠翻页': 'wipeleft', '交叉褪化': 'fade', '中心扩散': 'circleopen',
    '推拉晃动': 'slideleft', '模糊': 'fadeblack', '光线切割': 'wipeup',
    '推镜': 'zoomin', '淡入淡出': 'fade', '黑场': 'fadeblack', '白场': 'fadewhite',
    '左滑': 'slideleft', '右滑': 'slideright', '上滑': 'slideup', '下滑': 'slidedown',
    '圆形': 'circleopen', '矩形': 'rectcrop', '像素化': 'pixelize', '径向': 'radial',
}


def xfade_type(name):
    if not name:
        return 'fade'
    if name in TRANSITION_XFADE:
        return TRANSITION_XFADE[name]
    c = capabilities()
    if name in c['filters']:
        return name
    return 'fade'


def xfade_concat(segments, out, width, height, fps=30, gpu=False, crf=18,
                 default_trans='fade', default_dur=800) -> str:
    """带转场的拼接。

    segments: [ {'path':..., 'transition': {'name':'','dur':ms}} , ... ]
              第 i 项的 transition 表示"它和前一段之间"的转场（第 0 项忽略）
    做法：每段先归一化成同分辨率/帧率/像素格式，再用 xfade 逐段串起来，
          offset 按累计时长减去转场时长计算。
    """
    if len(segments) == 1:
        s = segments[0]
        return make_segment(s['path'], out, width, height, fps, gpu, crf)

    norm = []
    for s in segments:
        norm.append(make_segment(s['path'], s['path'] + '.norm.mp4', width, height, fps, gpu, crf))

    durs = [probe_duration(p) / 1000.0 for p in norm]
    args = []
    for p in norm:
        args += ['-i', p]
    # 音频也要一起转场，否则后半段会静音
    fc = []
    vprev, aprev = '[0:v]', '[0:a]'
    acc = durs[0]
    for i in range(1, len(norm)):
        t = segments[i].get('transition') or {}
        name = t.get('name') or default_trans
        d = (t.get('dur') or default_dur) / 1000.0
        d = min(d, durs[i - 1] * 0.9, durs[i] * 0.9)
        xf = xfade_type(name)
        off = max(acc - d, 0)
        vout, aout = '[v%d]' % i, '[a%d]' % i
        fc.append('%s%sxfade=transition=%s:duration=%.4f:offset=%.4f%s'
                  % (vprev, '[%d:v]' % i, xf, d, off, vout))
        fc.append('%s%sacrossfade=d=%.4f%s' % (aprev, '[%d:a]' % i, d, aout))
        vprev, aprev = vout, aout
        acc = acc + durs[i] - d
    args += ['-filter_complex', ';'.join(fc), '-map', vprev, '-map', aprev]
    args += _enc_args(gpu=gpu, crf=crf) + ['-c:a', 'aac', '-b:a', '192k', out]
    run(args)
    for p in norm:
        try:
            os.remove(p)
        except OSError:
            pass
    return out


def make_segment(src, out, width, height, fps, gpu=False, crf=18, trim_in=None, trim_out=None):
    """把一段素材归一化到统一的画布/帧率/像素格式（拼接前必做）。

    没有音轨的素材会自动补一条静音轨 —— 否则后面 acrossfade 会 EINVAL。
    """
    vf = ('scale=%d:%d:force_original_aspect_ratio=decrease,'
          'pad=%d:%d:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps=%d,format=yuv420p'
          % (width, height, width, height, fps))
    dur = None
    if trim_out:
        dur = (trim_out - (trim_in or 0)) / 1000.0
    else:
        d = probe_duration(src)
        if d:
            dur = (d - (trim_in or 0)) / 1000.0
    silent = not has_audio(src)
    args = []
    if trim_in:
        args += ['-ss', '%.3f' % (trim_in / 1000.0)]
    args += ['-i', src]
    if silent:
        args += ['-f', 'lavfi', '-t', '%.3f' % max(dur or 1, 0.1),
                 '-i', 'anullsrc=r=48000:cl=stereo']
    if dur:
        args += ['-t', '%.3f' % dur]
    args += ['-vf', vf, '-r', str(fps), '-map', '0:v']
    args += (['-af', 'aresample=48000', '-map', '0:a'] if not silent
             else ['-map', '1:a'])
    args += _enc_args(gpu=gpu, crf=crf) + ['-c:a', 'aac', '-b:a', '192k', '-ac', '2', '-shortest', out]
    run(args)
    return out


def probe_duration(path) -> int:
    """毫秒"""
    fp = ffprobe_path()
    if not fp:
        return 0
    r = subprocess.run([fp, '-v', 'error', '-show_entries', 'format=duration',
                        '-of', 'default=nw=1:nk=1', path],
                       capture_output=True, text=True, encoding='utf-8',
                       errors='replace', timeout=60)
    try:
        return int(float((r.stdout or '0').strip()) * 1000)
    except ValueError:
        return 0


def has_audio(path) -> bool:
    """有没有音频流。⚠️ 拼接/转场时这条很关键：
    没有音频流的片段会让 acrossfade 直接报 EINVAL（Invalid argument）。"""
    fp = ffprobe_path()
    if not fp:
        return True
    r = subprocess.run([fp, '-v', 'error', '-select_streams', 'a',
                        '-show_entries', 'stream=index', '-of', 'csv=p=0', path],
                       capture_output=True, text=True, encoding='utf-8',
                       errors='replace', timeout=60)
    return bool((r.stdout or '').strip())


# ---------------------------------------------------------------- 字幕
def write_ass(captions, path, width, height, font='Source Han Sans CN Medium',
              font_size=54, margin_v=180, primary='&H00FFFFFF', outline_colour='&H00000000',
              outline=3, shadow=1):
    """把字幕写成 ASS。x/y 用必剪的 transX/transY（-1..1，相对中心）换算。"""
    head = """[Script Info]
ScriptType: v4.00+
PlayResX: %d
PlayResY: %d
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: DSH,%s,%d,%s,&H000000FF,%s,&H64000000,0,0,0,0,100,100,0,0,1,%d,%d,5,40,40,%d,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""" % (width, height, font, font_size, primary, outline_colour, outline, shadow, margin_v)
    lines = [head]
    for c in captions:
        st, en = c['start'] / 1000.0, c['end'] / 1000.0
        tx, ty = c.get('x', 0.0), c.get('y', -0.76)
        px = int((1 + tx) / 2.0 * width)
        py = int((1 - ty) / 2.0 * height)
        text = c['text'].replace('\n', '\\N').replace('{', '(').replace('}', ')')
        lines.append('Dialogue: 0,%s,%s,DSH,,0,0,0,,{\\pos(%d,%d)}%s\n'
                     % (_ass_time(st), _ass_time(en), px, py, text))
    with open(path, 'w', encoding='utf-8-sig') as f:
        f.write(''.join(lines))
    return path


def _ass_time(t):
    t = max(t, 0)
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    s = t % 60
    return '%d:%02d:%05.2f' % (h, m, s)


def burn_subtitles(video, ass_path, out, gpu=False, crf=18, fonts_dir=None) -> str:
    """把 ASS 烧进画面。需要 libass（完整版 ffmpeg）；没有就抛错，由上层改用叠图。"""
    c = capabilities()
    if 'subtitles' not in c['filters'] and 'ass' not in c['filters']:
        raise RuntimeError('这个 ffmpeg 没有 subtitles/ass 滤镜，无法烧字幕（装完整版）')
    fdir = (fonts_dir or env_paths.font_dir() or '').replace('\\', '/')
    esc = ass_path.replace('\\', '/').replace(':', '\\:')
    vf = "subtitles='%s':fontsdir='%s'" % (esc, fdir.replace(':', '\\:'))
    run(['-i', video, '-vf', vf] + _enc_args(gpu=gpu, crf=crf) +
        ['-c:a', 'copy', out])
    return out


def pick_font():
    """找一个能显示中文的字体。优先用必剪自带的那几个（装了必剪就一定有）。"""
    d = env_paths.font_dir()
    for cand in ('Source Han Sans CN Medium.ttf', 'FZFSJW.ttf', 'ZKWYJW.ttf'):
        if d and os.path.exists(os.path.join(d, cand)):
            return os.path.join(d, cand)
    for sysdir in (os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'Fonts'),):
        for cand in ('msyh.ttc', 'msyhbd.ttc', 'simhei.ttf', 'simsun.ttc'):
            if os.path.exists(os.path.join(sysdir, cand)):
                return os.path.join(sysdir, cand)
    return None


def overlay_text_fallback(video, captions, out, width, height, gpu=False, crf=18,
                          font=None, font_size=54) -> str:
    """没有 libass 时的兜底：用 PIL 把每条字幕渲染成 PNG，再用 overlay 按时间段叠上去。

    效果比真字幕滤镜差（不能自动换行、不能富文本），但完全不需要 libass。
    """
    from PIL import Image, ImageDraw, ImageFont
    font = font or pick_font()
    tmp = os.path.join(env_paths.tmp_dir(), 'text_overlay')
    os.makedirs(tmp, exist_ok=True)
    pngs, args, fc, prev = [], [], [], '[0:v]'
    for i, c in enumerate(captions, 1):
        img = Image.new('RGBA', (width, int(font_size * 2.2)), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        f = ImageFont.truetype(font, font_size) if font else ImageFont.load_default()
        tw = d.textlength(c['text'], font=f)
        x = (width - tw) / 2
        for dx, dy in ((-3, 0), (3, 0), (0, -3), (0, 3), (-2, -2), (2, 2), (-2, 2), (2, -2)):
            d.text((x + dx, 10 + dy), c['text'], font=f, fill=(0, 0, 0, 255))
        d.text((x, 10), c['text'], font=f, fill=(255, 255, 255, 255))
        p = os.path.join(tmp, 'cap%03d.png' % i)
        img.save(p)
        pngs.append(p)
        args += ['-i', p]
        y = int((1 - c.get('y', -0.76)) / 2.0 * height) - int(font_size * 1.1)
        nxt = '[v%d]' % i
        fc.append('%s[%d:v]overlay=0:%d:enable=\'between(t,%.3f,%.3f)\'%s'
                  % (prev, i, max(y, 0), c['start'] / 1000.0, c['end'] / 1000.0, nxt))
        prev = nxt
    run(['-i', video] + args + ['-filter_complex', ';'.join(fc), '-map', prev,
                                '-map', '0:a?'] + _enc_args(gpu=gpu, crf=crf) +
        ['-c:a', 'copy', out])
    return out


# ---------------------------------------------------------------- 混音
def mix_audio(video, tracks, out, gpu=False, crf=18) -> str:
    """把若干条音频混进视频。

    tracks: [ {'path':..., 'start':ms, 'trim_in':ms, 'trim_out':ms,
               'volume_db':0, 'fade_in':ms, 'fade_out':ms, 'loop':False} ]
    视频原声用 {'keep_video': True, 'volume_db': -3} 表示。
    """
    args = ['-i', video]
    heads = ['[0:a]']
    fc = []
    # 视频原声统一成 48k 立体声
    fc.append('[0:a]aresample=48000,pan=stereo|c0=c0|c1=c1[v0]')
    heads = ['[v0]']
    idx = 1
    for t in tracks:
        if t.get('keep_video'):
            fc.append('[v0]volume=%.2fdB[a0]' % t.get('volume_db', 0))
            heads = ['[a0]']
            continue
        a = ['-i', t['path']]
        args += a
        delay = int(t.get('start', 0))
        tin = int(t.get('trim_in', 0))
        pre = ['atrim=start=%.3f' % (tin / 1000.0)]
        if t.get('trim_out'):
            pre.append('atrim=end=%.3f' % (t['trim_out'] / 1000.0))
        pre.append('asetpts=PTS-STARTPTS')
        pre.append('aresample=48000')
        pre.append('aformat=channel_layouts=stereo')
        if t.get('volume_db'):
            pre.append('volume=%.2fdB' % t['volume_db'])
        if t.get('fade_in'):
            pre.append('afade=t=in:st=0:d=%.3f' % (t['fade_in'] / 1000.0))
        if t.get('fade_out'):
            dur = ((t.get('trim_out') or probe_duration(t['path'])) - tin) / 1000.0
            fo = t['fade_out'] / 1000.0
            pre.append('afade=t=out:st=%.3f:d=%.3f' % (max(dur - fo, 0), fo))
        if delay:
            pre.append('adelay=%d|%d' % (delay, delay))
        fc.append('[%d:a]%s[a%d]' % (idx, ','.join(pre), idx))
        heads.append('[a%d]' % idx)
        idx += 1
    fc.append('%samix=inputs=%d:duration=first:dropout_transition=0[aout]'
              % (''.join(heads), len(heads)))
    args += ['-filter_complex', ';'.join(fc), '-map', '0:v', '-map', '[aout]']
    args += ['-c:v', 'copy'] + ['-c:a', 'aac', '-b:a', '192k', out]
    run(args)
    return out


# ---------------------------------------------------------------- 顶层
def render_plan(plan, out, gpu=False, crf=18):
    """把一个 edit_plan 直接渲染成 mp4（不经过必剪）。

    这是"能全自动出片"的那条路；代价是转场只能用 ffmpeg 自己的 xfade 类型，
    和必剪里那些专有转场（折叠翻页之类）视觉上不一样。
    """
    W, H, fps = plan['width'], plan['height'], plan.get('fps', 30)
    tmpdir = os.path.join(env_paths.tmp_dir(), 'render')
    os.makedirs(tmpdir, exist_ok=True)
    segs = []
    for i, c in enumerate(plan['clips']):
        src = c['path']
        if c.get('kind') == 'image' or os.path.splitext(src)[1].lower() in \
                ('.jpg', '.jpeg', '.png', '.webp', '.bmp'):
            p = make_still(src, os.path.join(tmpdir, 's%03d.mp4' % i),
                           c.get('duration_ms', 3000), W, H, fps,
                           motion=c.get('motion', 'none'))
        else:
            p = make_segment(src, os.path.join(tmpdir, 's%03d.mp4' % i), W, H, fps,
                             gpu=gpu, crf=crf,
                             trim_in=c.get('trim_in'), trim_out=c.get('trim_out'))
        segs.append({'path': p, 'transition': c.get('transition')})
    v = xfade_concat(segs, os.path.join(tmpdir, 'joined.mp4'), W, H, fps, gpu, crf) \
        if len(segs) > 1 else segs[0]['path']

    if plan.get('captions'):
        ass = write_ass(plan['captions'], os.path.join(tmpdir, 'sub.ass'), W, H,
                        font_size=plan.get('subtitle_size', 54),
                        margin_v=plan.get('subtitle_margin_v', 180))
        try:
            v = burn_subtitles(v, ass, os.path.join(tmpdir, 'subbed.mp4'), gpu, crf)
        except RuntimeError:
            v = overlay_text_fallback(v, plan['captions'], os.path.join(tmpdir, 'subbed.mp4'),
                                      W, H, gpu, crf, font_size=plan.get('subtitle_size', 54))

    audio = list(plan.get('audio', []))
    if plan.get('keep_original_audio'):
        audio.insert(0, {'keep_video': True, 'volume_db': plan.get('original_volume_db', 0)})
    if audio:
        v = mix_audio(v, audio, out, gpu, crf)
    else:
        shutil.copy2(v, out)
    return out

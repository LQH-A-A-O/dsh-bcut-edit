"""剪辑方案（plan）—— 一份数据，两个后端。

同一个 JSON 方案可以：
  to_bcut(plan)   -> 生成必剪草稿，你在必剪里看效果、微调、导出（转场是必剪原版效果）
  to_mp4(plan)    -> 用 ffmpeg 直接出成品 mp4（全自动，但转场是 ffmpeg 的 xfade）

方案格式（时间一律毫秒）
------------------------
{
  "name": "千年学院宣传片",
  "width": 1080, "height": 1920, "fps": 30,
  "keep_original_audio": true, "original_volume_db": -3,
  "subtitle_size": 54,
  "clips": [
    {"path": "D:/素材/a.mp4", "trim_in": 0, "trim_out": 3000},
    {"path": "D:/素材/封面.png", "kind": "image", "duration_ms": 4000, "motion": "zoom_in"},
    {"path": "D:/素材/b.mp4", "transition": {"name": "折叠翻页", "xffade": "wipeleft", "dur": 800}}
  ],
  "captions": [
    {"text": "第一句", "start": 0, "end": 2000, "x": 0.0, "y": -0.76}
  ],
  "audio": [
    {"path": "D:/音乐/bgm.mp3", "start": 0, "volume_db": -6, "fade_in": 1500, "fade_out": 3000}
  ]
}
"""
import json
import os

import bcut_draft
import ffmpeg_edit

IMAGE_EXT = bcut_draft.IMAGE_EXT


def default_plan(width=1080, height=1920, fps=30):
    return {'name': '未命名', 'width': width, 'height': height, 'fps': fps,
            'keep_original_audio': True, 'original_volume_db': 0,
            'subtitle_size': 54, 'clips': [], 'captions': [], 'audio': []}


def load(path):
    with open(path, encoding='utf-8') as f:
        plan = json.load(f)
    for k, v in default_plan().items():
        plan.setdefault(k, v)
    return plan


def save(plan, path):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(plan, f, ensure_ascii=False, indent=1)
    return path


def validate(plan):
    """返回 (问题列表, 警告列表)。问题不解决就别往下跑。"""
    errs, warns = [], []
    if not plan.get('clips'):
        errs.append('clips 是空的，没有东西可剪')
    for i, c in enumerate(plan.get('clips', [])):
        p = c.get('path')
        if not p:
            errs.append('clips[%d] 没有 path' % i)
        elif not os.path.exists(p):
            errs.append('clips[%d] 文件不存在: %s' % (i, p))
        if i == 0 and c.get('transition'):
            warns.append('clips[0] 的 transition 会被忽略（第一段前面没有东西可转）')
    for i, c in enumerate(plan.get('captions', [])):
        if not c.get('text'):
            errs.append('captions[%d] 没有 text' % i)
        if c.get('end', 0) <= c.get('start', 0):
            errs.append('captions[%d] 的 end 必须大于 start' % i)
    for i, a in enumerate(plan.get('audio', [])):
        if not a.get('path'):
            errs.append('audio[%d] 没有 path' % i)
        elif not os.path.exists(a['path']):
            errs.append('audio[%d] 文件不存在: %s' % (i, a['path']))
    cat = bcut_draft.catalog('transitions')
    names = {v.get('name') for v in cat.values() if v.get('name')}
    for i, c in enumerate(plan.get('clips', [])):
        t = c.get('transition')
        if t and t.get('name') and t['name'] not in names:
            warns.append('clips[%d] 转场「%s」不在必剪本机素材库里，'
                         '生成必剪草稿时会退化成"淡入淡出"；'
                         'ffmpeg 直出不受影响' % (i, t['name']))
    return errs, warns


# ---------------------------------------------------------------- 后端 A：必剪草稿
def _plan_layout(plan, durs):
    """算出每段在**方案时间轴**上的起点。

    方案时间轴 = 观众看到的时间轴：转场处两段重叠，所以总时长被压缩。
    （ffmpeg 的 xfade 就是这个行为）
    """
    starts, t = [], 0
    for i, c in enumerate(plan['clips']):
        if i:
            tr = c.get('transition') or {}
            t -= int(tr.get('dur') or 0)          # 转场重叠，吃掉前一段的尾巴
        s = c.get('start')
        if s is not None:
            t = int(s)
        starts.append(t)
        t += durs[i]
    return starts


def _time_mapper(plan, durs, bcut_starts):
    """把「方案时间」映射到「必剪时间」。

    ⚠️ 两个后端的时间轴不一样（真机验证过）：
       ffmpeg xfade  —— 转场重叠，时间线被压缩
       必剪          —— 片段首尾相接、inPoint/outPoint 原样保留，**不压缩**
    所以字幕/音频的时间必须做映射，否则必剪里会整体错位。
    """
    plan_starts = _plan_layout(plan, durs)

    def m(t):
        t = int(t)
        # 找"最近开始的那一段"。开头加 1ms 容差：转场让相邻两段共享边界，
        # 落在边界上的时间应该算给**后一段**（否则会算进上一段，字幕会盖错画面）。
        # 踩过：2.8-0.6 浮点算出 2199.9999…，int() 截成 2199，字幕就跑到标题卡上了。
        i = 0
        for k in range(len(plan_starts)):
            if plan_starts[k] <= t + 1:
                i = k
            else:
                break
        off = max(0, min(t - plan_starts[i], durs[i]))
        return bcut_starts[i] + off
    return m


def to_bcut(plan, name=None, guid=None):
    tl = bcut_draft.Timeline(plan['width'], plan['height'], plan.get('fps', 30))
    cat = bcut_draft.catalog('transitions')
    by_name = {v['name']: k for k, v in cat.items() if v.get('name')}
    durs = []
    for i, c in enumerate(plan['clips']):
        ex = os.path.splitext(c['path'])[1].lower()
        start = c.get('start')
        if c.get('kind') == 'image' or ex in IMAGE_EXT:
            span = int(c.get('duration_ms', 3000))
            tl.add_clip(c['path'], start=start, kind='image', duration_ms=span)
        else:
            span = int(c.get('trim_out') or 0) - int(c.get('trim_in', 0))
            if span <= 0:
                span = bcut_draft.probe(c['path'])['duration_ms'] - int(c.get('trim_in', 0))
            tl.add_clip(c['path'], trim_in=c.get('trim_in', 0),
                        trim_out=c.get('trim_out'), start=start,
                        speed=c.get('speed', 1.0),
                        gain_db=c.get('gain_db') if c.get('gain_db') is not None
                        else c.get('volume_db'))
        durs.append(int(span / max(c.get('speed', 1.0), 1e-6)))
        t = c.get('transition')
        if t and i > 0:
            mid = t.get('id') or by_name.get(t.get('name'))
            if mid:
                try:
                    tl.add_transition(i, mid, t.get('dur', 800))
                except Exception as e:
                    print('  片段 %d 的转场加不上：%s' % (i, e))
            else:
                print('  片段 %d 的转场「%s」本机没有，跳过' % (i, t.get('name')))

    # 必剪里片段是首尾相接的，起点就是累计时长
    bcut_starts, acc = [], 0
    for d in durs:
        bcut_starts.append(acc)
        acc += d
    m = _time_mapper(plan, durs, bcut_starts)

    for cap in plan.get('captions', []):
        tl.add_caption(cap['text'], m(cap['start']), m(cap['end']),
                       x=cap.get('x', 0.0), y=cap.get('y', -0.76),
                       opacity=cap.get('opacity', 1.0))
    for a in plan.get('audio', []):
        tl.add_audio(a['path'], start=m(a.get('start', 0)), trim_in=a.get('trim_in', 0),
                     trim_out=a.get('trim_out'), volume_db=a.get('volume_db', 0),
                     fade_in=a.get('fade_in', 0), fade_out=a.get('fade_out', 0))
    return tl.save(name=name or plan.get('name'), guid=guid)


# ---------------------------------------------------------------- 后端 B：ffmpeg 直出
def to_mp4(plan, out, gpu=False, crf=18):
    return ffmpeg_edit.render_plan(plan, out, gpu=gpu, crf=crf)


def demo_plan(media_dir=None, out_path=None):
    """生成一个可直接跑的示例方案：3 张图 + 转场 + 字幕 + 一条音频。"""
    from PIL import Image, ImageDraw
    import env_paths
    d = media_dir or os.path.join(env_paths.tmp_dir(), 'demo_media')
    os.makedirs(d, exist_ok=True)
    imgs = []
    for i, col in enumerate(((34, 40, 60), (60, 34, 48), (30, 56, 50))):
        p = os.path.join(d, 'card%d.png' % i)
        im = Image.new('RGB', (1080, 1920), col)
        dr = ImageDraw.Draw(im)
        dr.rectangle([80, 700, 1000, 1220], outline=(230, 230, 235), width=6)
        dr.text((140, 900), 'DEMO %d' % (i + 1), fill=(240, 240, 245))
        im.save(p)
        imgs.append(p)
    plan = default_plan(1080, 1920, 30)
    plan['name'] = 'DSH 示例片'
    for i, p in enumerate(imgs):
        plan['clips'].append({'path': p, 'kind': 'image', 'duration_ms': 2500,
                              'motion': 'zoom_in' if i % 2 == 0 else 'zoom_out',
                              'transition': {'name': '交叉褪化', 'xffade': 'fade', 'dur': 600}})
    plan['captions'] = [
        {'text': '这是 ffmpeg 直出 / 必剪草稿 两条路共用的方案', 'start': 200, 'end': 2300, 'y': -0.7},
        {'text': '第二句字幕', 'start': 2800, 'end': 4800, 'y': -0.7},
        {'text': '第三句字幕', 'start': 5300, 'end': 7300, 'y': -0.7},
    ]
    plan['_demo_dir'] = d
    if out_path:
        save(plan, out_path)
    return plan

"""必剪（Bcut）草稿读写 —— 用代码生成/编辑多轨道视频工程。

原理（2026-09-27 实测）
----------------------
必剪把工程存在：

    C:\\Users\\<用户>\\Documents\\Bcut Drafts\\<GUID>\\<时-分-秒-毫秒>--{uuid}.bjson
    C:\\Users\\<用户>\\Documents\\Bcut Drafts\\draftInfo.json      <- 草稿索引

`.bjson` 是**明文 JSON**（无加密无压缩），所以工程可以完全用代码生成。
必剪每 15 秒自动存一份快照到同一个文件夹，索引在 `draftInfo.json`。

单位约定（从真实草稿反推，已核对多份）
--------------------------------------
  clip["inPoint"] / ["outPoint"]   时间线上的位置，**毫秒**
  clip["trimIn"]  / ["trimOut"]    在源素材里截取哪一段，**毫秒**
  所以 outPoint - inPoint == trimOut - trimIn == 这段在时间线上占多长

多轨与转场
----------
  timeline.videoTracks[]           每条轨道一个 dict（clips + transitions）
  timeline.videoTracks[i].transitions[].srcIndex
                                   转场挂在**它后面那个片段**的索引上，
                                   即 srcIndex=k 表示"片段 k-1 和 k 之间"
  timeline.captionTracks[].captions[]
  timeline.audioTracks[].audioClips[]

安全约定
--------
`save()` **永远新建一个 GUID 文件夹**，绝不改动你已有的草稿。
自己创建的草稿会写一个 `.dsh-created` 标记，只有带标记的才允许被覆盖。
"""
import copy
import datetime
import json
import os
import random
import re
import shutil
import subprocess
import uuid as _uuid

HERE = os.path.dirname(os.path.abspath(__file__))
TPL_DIR = os.path.join(HERE, 'templates')
CAT_DIR = os.path.join(HERE, 'catalog')

import env_paths  # noqa: E402

DRAFT_ROOT = env_paths.draft_root()
LOCAL_RES = env_paths.local_res()
BCUT_HOME = env_paths.bcut_home()

MARKER = '.dsh-created'

# 素材类型枚举（实测自真实草稿）
KIND_VIDEO = dict(assetItemType=4, type=1, mediaType=0, videoType=1, itemType=4)
KIND_IMAGE = dict(assetItemType=6, type=3, mediaType=1, videoType=0, itemType=6)
KIND_AUDIO = dict(assetItemType=5, type=2, mediaType=0, videoType=0, itemType=3)

VIDEO_EXT = ('.mp4', '.mov', '.mkv', '.avi', '.flv', '.wmv', '.webm', '.m4v', '.ts')
IMAGE_EXT = ('.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif')
AUDIO_EXT = ('.mp3', '.wav', '.m4a', '.aac', '.flac', '.ogg', '.wma')


# ---------------------------------------------------------------- 小工具
def _id19() -> str:
    """必剪的 idString / uid：19 位十进制数字，取当前纳秒级时间戳"""
    return str(int(datetime.datetime.now().timestamp() * 1e6)) + str(random.randint(100, 999))[:3]


def _now_ms() -> int:
    return int(datetime.datetime.now().timestamp() * 1000)


def _cn_time(ts=None) -> str:
    """素材表里的 importTime 格式：周一 11月 24 22:11:30 2025"""
    d = datetime.datetime.fromtimestamp(ts) if ts else datetime.datetime.now()
    wd = '周' + '一二三四五六日'[d.weekday()]
    return '%s %d月 %d %02d:%02d:%02d %d' % (wd, d.month, d.day, d.hour, d.minute, d.second, d.year)


def fwd(p: str) -> str:
    """必剪里所有路径都是正斜杠"""
    return os.path.abspath(p).replace('\\', '/')


def resolve_material_file(src: str):
    """必剪内置素材（片头/片尾/黑场）在本机的实际路径。

    打包时存的是占位符路径，而且**别人机器上的必须自己下过才有**，
    所以这里按文件名到 <local_res>/material_lib/ 里找一遍。
    """
    if src and os.path.exists(src):
        return src
    if not src:
        return None
    base = os.path.basename(src.replace('\\', '/'))
    d = os.path.join(LOCAL_RES, 'material_lib')
    if os.path.isdir(d):
        for f in os.listdir(d):
            if f == base:
                return os.path.join(d, f).replace('\\', '/')
    return None


def resolve_transition_package(mid: str):
    """转场包路径。本地装过才有：<local_res>/transition/<materialId>_<日期>_aurora/aurora.videotransition"""
    d = os.path.join(LOCAL_RES, 'transition')
    if not os.path.isdir(d):
        return None
    for name in sorted(os.listdir(d)):
        if name.split('_')[0] == str(mid):
            p = os.path.join(d, name, 'aurora.videotransition')
            if os.path.exists(p):
                return p.replace('\\', '/')
    return None


def tpl(name: str) -> dict:
    """读原型对象。里面的本机绝对路径已经被打包时换成了占位符，这里还原。"""
    with open(os.path.join(TPL_DIR, name + '.json'), encoding='utf-8') as f:
        return env_paths.resolve_deep(json.load(f))


def catalog(name: str) -> dict:
    p = os.path.join(CAT_DIR, name + '.json')
    if not os.path.exists(p):
        return {}
    with open(p, encoding='utf-8') as f:
        return env_paths.resolve_deep(json.load(f))


# ---------------------------------------------------------------- 素材探测
def find_ffprobe():
    return env_paths.ffprobe()


def probe(path: str) -> dict:
    """探测素材：时长(ms) / 宽高 / 帧率。优先 ffprobe，图片退化到 PIL。"""
    path = os.path.abspath(path)
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    info = dict(path=path, duration_ms=0, width=0, height=0, fps_num=30, fps_den=1)
    ex = os.path.splitext(path)[1].lower()
    if ex in IMAGE_EXT:
        try:
            from PIL import Image
            with Image.open(path) as im:
                info['width'], info['height'] = im.size
        except Exception:
            pass
        info['kind'] = 'image'
        return info
    fp = find_ffprobe()
    if fp:
        try:
            out = subprocess.run(
                [fp, '-v', 'error', '-print_format', 'json',
                 '-show_format', '-show_streams', path],
                capture_output=True, text=True, encoding='utf-8', errors='replace',
                timeout=60).stdout
            o = json.loads(out or '{}')
            dur = float(o.get('format', {}).get('duration') or 0)
            info['duration_ms'] = int(round(dur * 1000))
            vs = [s for s in o.get('streams', []) if s.get('codec_type') == 'video']
            if vs:
                v = vs[0]
                info['width'] = int(v.get('width') or 0)
                info['height'] = int(v.get('height') or 0)
                m = re.match(r'(\d+)/(\d+)', v.get('r_frame_rate') or '')
                if m and int(m.group(2)):
                    info['fps_num'], info['fps_den'] = int(m.group(1)), int(m.group(2))
            if not info['duration_ms']:
                for s in o.get('streams', []):
                    if s.get('duration'):
                        info['duration_ms'] = int(float(s['duration']) * 1000)
                        break
        except Exception:
            pass
    info['kind'] = 'audio' if ex in AUDIO_EXT else 'video'
    return info


# ---------------------------------------------------------------- 草稿索引
def _draft_info_path():
    return os.path.join(DRAFT_ROOT, 'draftInfo.json')


def list_drafts() -> list:
    """列出所有草稿：id / 名字 / 修改时间 / 时长(ms) / 快照数"""
    p = _draft_info_path()
    if not os.path.exists(p):
        return []
    info = json.load(open(p, encoding='utf-8'))
    out = []
    for d in info.get('draftInfos', []):
        g = d.get('id')
        folder = os.path.join(DRAFT_ROOT, g) if g else ''
        snaps = []
        if folder and os.path.isdir(folder):
            snaps = sorted(f for f in os.listdir(folder) if f.endswith('.bjson'))
        out.append(dict(
            id=g, name=d.get('name'), modify_ms=d.get('modifyTime'),
            duration_ms=round((d.get('duration') or 0) / 1000),
            snapshots=len(snaps), folder=folder,
            has_cover=bool(folder) and os.path.exists(os.path.join(folder, 'cover.jpg')),
            mine=bool(folder) and os.path.exists(os.path.join(folder, MARKER)),
        ))
    out.sort(key=lambda x: x['modify_ms'] or 0, reverse=True)
    return out


def newest_bjson(guid: str) -> str:
    folder = os.path.join(DRAFT_ROOT, guid)
    fs = sorted(f for f in os.listdir(folder) if f.endswith('.bjson'))
    if not fs:
        raise FileNotFoundError('草稿 %s 里没有 .bjson' % guid)
    return os.path.join(folder, fs[-1])


def load(guid: str) -> dict:
    """读一份草稿（默认取最新快照）"""
    return json.load(open(newest_bjson(guid), encoding='utf-8'))


def _register(guid, name, duration_ms):
    p = _draft_info_path()
    info = json.load(open(p, encoding='utf-8')) if os.path.exists(p) else {'draftInfos': []}
    info.setdefault('draftInfos', [])
    for d in info['draftInfos']:
        if d.get('id') == guid:
            d.update(name=name, modifyTime=_now_ms(), duration=duration_ms * 1000)
            break
    else:
        info['draftInfos'].append({
            'cloud_draft_id': '', 'cloud_draft_version': '',
            'duration': duration_ms * 1000, 'id': guid,
            'modifyTime': _now_ms(), 'name': name, 'storyLineId': ''})
    tmp = p + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(info, f, ensure_ascii=False, indent=4)
    os.replace(tmp, p)


# ---------------------------------------------------------------- 时间线
class Timeline:
    """一个必剪工程。时间单位一律毫秒。

        tl = Timeline(width=1080, height=1920, fps=30)
        tl.add_clip(r'D:\\a.mp4', trim_out=3000)
        tl.add_clip(r'D:\\b.mp4', trim_in=1000, trim_out=4000)
        tl.add_transition(1, '淡入淡出', 800)      # 片段 0 和 1 之间
        tl.add_caption('第一句话', 0, 2000)
        tl.add_audio(r'D:\\bgm.mp3', volume_db=-6)
        tl.save('我的片子')
    """

    def __init__(self, width=1920, height=1080, fps=30, sample_rate=48000, channels=2):
        self.data = tpl('empty_draft')
        # ⚠️ tracking / tts / ttv 是 timelineWidget 的子节点，不在 timeline 里面
        self._w = self.data['timelineWidget']
        tl = self._w['timeline']
        tl['config'] = {'videoRes': {'width': width, 'height': height},
                        'videoFps': {'num': fps, 'den': 1},
                        'audioRes': {'sampleRate': sample_rate, 'channelCount': channels}}
        tl['idString'] = _id19()
        base = tl['videoTracks'][0]
        base['idString'] = _id19()
        base['index'] = 0
        self.data['mainWindow']['browserPanelFiles'] = []
        self._sources = {}
        self._cursor = {}      # track -> 下一段的起始毫秒（顺序追加用）
        self._tl = tl

    # ---- 轨道
    def video_track_count(self):
        return len(self._tl['videoTracks'])

    def add_video_track(self) -> int:
        t = copy.deepcopy(self._tl['videoTracks'][0])
        t['clips'] = []
        t['transitions'] = []
        t['index'] = len(self._tl['videoTracks'])
        t['idString'] = _id19()
        self._tl['videoTracks'].append(t)
        return t['index']

    def _ensure_audio_track(self, track) -> int:
        while len(self._tl['audioTracks']) <= track:
            self._tl['audioTracks'].append({
                'audioClips': [], 'audioTrackType': 100, 'compacted': False,
                'idString': _id19(), 'index': len(self._tl['audioTracks']), 'trackType': 2})
        return track

    def _ensure_caption_track(self, track) -> int:
        while len(self._tl['captionTracks']) <= track:
            self._tl['captionTracks'].append({
                'captions': [], 'compacted': False, 'idString': _id19(),
                'index': len(self._tl['captionTracks']), 'trackType': 4})
        return track

    # ---- 素材表
    def _register_source(self, path, kind):
        """登记到 browserPanelFiles（必剪左侧素材列表）。同一个文件只登记一次。"""
        sp = fwd(path)
        if sp in self._sources:
            return sp
        info = probe(path)
        item = copy.deepcopy(tpl('browser_file_image' if kind == 'image' else 'browser_file'))
        item.update({
            'srcPath': sp,
            'duration': str(info['duration_ms']) if kind != 'image' else '0',
            'width': info['width'], 'height': info['height'],
            'frameRateNum': info['fps_num'], 'frameRateDen': info['fps_den'],
            'itemType': KIND_IMAGE['itemType'] if kind == 'image' else KIND_VIDEO['itemType'],
            'importTime': _cn_time(), 'recentlyUsedTime': _cn_time(),
        })
        self.data['mainWindow']['browserPanelFiles'].append(item)
        self._sources[sp] = info
        return sp

    # ---- 片段
    def add_clip(self, path, trim_in=0, trim_out=None, start=None, track=0,
                 speed=1.0, volume_db=0, gain_db=None, kind=None, duration_ms=None):
        """加一段素材。start=None 表示接在轨道末尾（顺序拼）。

        trim_in/trim_out : 从源素材里截哪一段（毫秒）
        duration_ms      : 图片用，这一段在时间线上显示多久（默认 3000）
        gain_db          : 这一段的音量（**dB**）。写在 audioFxs 的 Volume.gainValue 里。
                           依据：真实草稿里该字段出现过 -120（=静音），所以是 dB。
                           不传就和真实草稿一样保持 0（原音量）。
        """
        ex = os.path.splitext(path)[1].lower()
        kind = kind or ('image' if ex in IMAGE_EXT else 'video')
        info = probe(path)
        if kind == 'image':
            span = int(duration_ms or 3000)
            trim_in, trim_out = 0, span
        else:
            total = info['duration_ms']
            trim_in = max(0, int(trim_in))
            trim_out = int(trim_out if trim_out is not None else total)
            trim_out = min(trim_out, total) if total else trim_out
            span = int(round((trim_out - trim_in) / max(speed, 1e-6)))
        if span <= 0:
            raise ValueError('片段时长为 0：%s (trim %s..%s)' % (path, trim_in, trim_out))
        if start is None:
            start = self._cursor.get(track, 0)
        start = int(start)

        while len(self._tl['videoTracks']) <= track:
            self.add_video_track()
        tr = self._tl['videoTracks'][track]

        sp = self._register_source(path, kind)
        clip = copy.deepcopy(tpl('image_clip' if kind == 'image' else 'video_clip'))
        e = KIND_IMAGE if kind == 'image' else KIND_VIDEO
        clip.update({
            'idString': _id19(), 'uid': _id19(),
            'inPoint': start, 'outPoint': start + span,
            'trimIn': trim_in, 'trimOut': trim_out,
            'speed': speed, 'sourcePath': sp, 'originalFilePath': sp,
            'volume': {'leftVolume': volume_db, 'rightVolume': volume_db},
            'srcMissing': False,
        })
        clip['assetInfo'].update({
            'assetItemType': e['assetItemType'], 'type': e['type'],
            'videoType': e['videoType'], 'srcPath': sp,
            'displayName': os.path.splitext(os.path.basename(path))[0],
            'itemName': os.path.basename(path),
            'content': os.path.splitext(os.path.basename(path))[0],
            'duration': 0 if kind == 'image' else info['duration_ms'],
            'width': info['width'], 'height': info['height'],
            'frameRateNum': info['fps_num'], 'frameRateDen': info['fps_den'],
        })
        # 音量走 audioFxs 的 Volume.gainValue（dB）；volume 那个字段真实草稿里恒为 0，别动
        if gain_db is not None:
            got = False
            for fx in clip.get('audioFxs', []):
                if fx.get('fxName') == 'Volume':
                    fx['gainValue'] = float(gain_db)
                    got = True
            if not got:
                clip.setdefault('audioFxs', []).append(
                    {'audioFxType': 0, 'bizType': 1, 'fxName': 'Volume',
                     'gainValue': float(gain_db), 'idString': _id19()})
        tr['clips'].append(clip)
        self._cursor[track] = start + span
        return len(tr['clips']) - 1

    def add_builtin(self, material_id, start=None, duration_ms=3000, track=0):
        """加必剪内置素材（片头/片尾/黑场）。material_id 见 catalog/builtin_materials.json"""
        bl = catalog('builtin_materials').get(str(material_id))
        if not bl:
            raise KeyError('没有内置素材 %s，看看 catalog/builtin_materials.json' % material_id)
        src = resolve_material_file(bl.get('srcPath'))
        if not src:
            raise FileNotFoundError(
                '内置素材「%s」不在本机。必剪要先自己用过一次，它才会下到\n'
                '  %s\n（这是在别人机器上最常见的失败原因）'
                % (bl.get('displayName') or material_id, os.path.join(LOCAL_RES, 'material_lib')))
        clip = copy.deepcopy(tpl('builtin_clip'))
        if start is None:
            start = self._cursor.get(track, 0)
        while len(self._tl['videoTracks']) <= track:
            self.add_video_track()
        tr = self._tl['videoTracks'][track]
        clip.update({'idString': _id19(), 'uid': _id19(), 'materialId': str(material_id),
                     'inPoint': int(start), 'outPoint': int(start) + int(duration_ms),
                     'trimIn': 0, 'trimOut': int(duration_ms)})
        clip['assetInfo'].update(bl)
        tr['clips'].append(clip)
        self._cursor[track] = int(start) + int(duration_ms)
        return len(tr['clips']) - 1

    def add_transition(self, before_clip_index, name_or_id, dur_ms=1000, track=0):
        """在"片段 before_clip_index-1 和 before_clip_index 之间"加转场。

        name_or_id 可以是中文名（如 '折叠翻页'）或 materialId（如 '5565464'）。

        ⚠️ 必剪**认的是 assetInfo**，不是顶层字段。真机验证踩过：
           只改顶层 materialId/transitionName，指定「交叉褪化」(2744) 会被
           悄悄解析成原型里的 5565464「折叠翻页」—— 不报错，就是换掉了。
           所以这里整块换 assetInfo，并且优先用该 materialId 的专属原型。
        """
        cat = catalog('transitions')
        hit = None
        for mid, v in cat.items():
            if mid == str(name_or_id) or (v.get('name') and v['name'] == str(name_or_id)):
                hit = v
                break
        if not hit:
            names = sorted({v['name'] for v in cat.values() if v.get('name')})
            raise KeyError('没有转场 %r。可用的有：%s' % (name_or_id, '、'.join(names)))
        pkg = resolve_transition_package(hit['materialId']) or \
            (hit.get('packagePath') if os.path.exists(hit.get('packagePath') or '') else None)
        if not pkg:
            raise RuntimeError(
                '转场「%s」(%s) 本机没装。必剪里要先用过一次这个转场，它才会下到\n'
                '  %s'
                % (hit.get('name') or name_or_id, hit['materialId'],
                   os.path.join(LOCAL_RES, 'transition')))
        label = hit.get('name') or str(name_or_id)
        try:
            t = copy.deepcopy(tpl('transition_%s' % hit['materialId']))
        except Exception:
            t = copy.deepcopy(tpl('transition'))
        t.update({
            'idString': _id19(), 'uid': _id19(),
            'materialId': str(hit['materialId']),
            'packagePath': pkg,
            'transitionName': label,
            'transitionDur': int(dur_ms),
            'srcIndex': int(before_clip_index),
        })
        if hit.get('cover') and os.path.exists(hit['cover']):
            t['cover'] = hit['cover'].replace('\\', '/')
        ai = copy.deepcopy(hit.get('assetInfo') or t.get('assetInfo') or {})
        ai.update({'realMaterialId': str(hit['materialId']),
                   'displayName': label, 'content': label, 'itemName': label,
                   'srcPath': pkg})
        if hit.get('cover') and os.path.exists(hit['cover']):
            ai['coverPath'] = hit['cover'].replace('\\', '/')
        else:
            ai.pop('coverPath', None)
        t['assetInfo'] = ai
        while len(self._tl['videoTracks']) <= track:
            self.add_video_track()
        self._tl['videoTracks'][track].setdefault('transitions', []).append(t)
        return t

    def add_caption(self, text, start, end, track=0, font_size=None, color=None,
                    outline_width=None, x=0.0, y=-0.76, opacity=1.0, **style):
        """加一条字幕。x/y 是相对画面中心的位移（-1..1，必剪的 transX/transY）。"""
        self._ensure_caption_track(track)
        c = copy.deepcopy(tpl('caption'))
        c.update({'idString': _id19(), 'uid': _id19(),
                  'captionText': text, 'inPoint': int(start), 'outPoint': int(end),
                  'transX': x, 'transY': y, 'opacity': opacity})
        if font_size is not None:
            c['scaleX'] = c['scaleY'] = font_size
        if outline_width is not None:
            c['outlineWidth'] = outline_width
        if color is not None:
            r, g, b = color
            c.setdefault('textColor', {})
            c['textColor'].update({'red': r, 'green': g, 'blue': b, 'alpha': 1})
        c.update(style)
        self._tl['captionTracks'][track]['captions'].append(c)
        return c

    def add_audio(self, path, start=0, trim_in=0, trim_out=None, track=0,
                  volume_db=0, fade_in=0, fade_out=0):
        """加一条音频（BGM / 音效）。同一条轨道上可以叠多段。"""
        info = probe(path)
        total = info['duration_ms']
        trim_in = max(0, int(trim_in))
        trim_out = int(trim_out if trim_out is not None else total)
        if total:
            trim_out = min(trim_out, total)
        span = trim_out - trim_in
        if span <= 0:
            raise ValueError('音频时长为 0：%s' % path)
        self._ensure_audio_track(track)
        sp = self._register_source(path, 'audio')
        a = copy.deepcopy(tpl('audio_clip'))
        a.update({'idString': _id19(), 'uid': _id19(),
                  'inPoint': int(start), 'outPoint': int(start) + span,
                  'trimIn': trim_in, 'trimOut': trim_out,
                  'sourcePath': sp, 'originalFilePath': sp,
                  'fadeIn': int(fade_in), 'fadeOut': int(fade_out)})
        a['assetInfo'].update({
            'assetItemType': KIND_AUDIO['assetItemType'], 'type': KIND_AUDIO['type'],
            'srcPath': sp, 'displayName': os.path.splitext(os.path.basename(path))[0],
            'itemName': os.path.basename(path), 'duration': total,
            'realMaterialId': '', 'customInfos': {}})
        for fx in a.get('fxs', []):
            if fx.get('fxName') == 'Volume':
                fx['gainValue'] = volume_db
        self._tl['audioTracks'][track]['audioClips'].append(a)
        return a

    # ---- 收尾
    def duration_ms(self) -> int:
        end = 0
        for tr in self._tl['videoTracks']:
            for c in tr['clips']:
                end = max(end, c['outPoint'])
        for tr in self._tl['audioTracks']:
            for c in tr['audioClips']:
                end = max(end, c['outPoint'])
        for tr in self._tl['captionTracks']:
            for c in tr['captions']:
                end = max(end, c['outPoint'])
        return int(end)

    def _sync_tracking(self):
        t = self._w['tracking']['tracks']
        vids, auds, caps, trans = [], [], [], []
        for tr in self._tl['videoTracks']:
            for c in tr['clips']:
                mid = c.get('materialId') or ''
                if mid:
                    vids.append(str(mid))
            for x in tr.get('transitions', []):
                trans.append(str(x.get('materialId') or ''))
        for tr in self._tl['audioTracks']:
            for c in tr['audioClips']:
                auds.append(str(c.get('materialId') or ''))
        for tr in self._tl['captionTracks']:
            for c in tr['captions']:
                caps.append(str(c.get('uid') or ''))
        t.update({'video_count': sum(len(x['clips']) for x in self._tl['videoTracks']),
                  'video_tracks': len(self._tl['videoTracks']),
                  'audio_tracks': len(self._tl['audioTracks']),
                  'pic_count': sum(1 for tr in self._tl['videoTracks'] for c in tr['clips']
                                   if c['assetInfo'].get('type') == 3),
                  'videos': ';'.join(v for v in vids if v),
                  'bgms': ';'.join(v for v in auds if v),
                  'subtitles': ';'.join(caps),
                  'transitions': ';'.join(v for v in trans if v)})
        self._w['tracking']['tracks'] = t

    def make_cover(self, out_jpg, clip_index=0):
        """用第 N 个片段的画面做封面。有 ffmpeg 就用真帧，没有就生成一张纯色图。"""
        from ffmpeg_edit import ffmpeg_path, grab_frame
        clips = self._tl['videoTracks'][0]['clips']
        if clips:
            c = clips[min(clip_index, len(clips) - 1)]
            src = c['sourcePath']
            if ffmpeg_path() and os.path.exists(src):
                if grab_frame(src, out_jpg, at_ms=c['trimIn'] + 100):
                    return out_jpg
        from PIL import Image
        Image.new('RGB', (640, 360), (18, 18, 22)).save(out_jpg, quality=88)
        return out_jpg

    def save(self, name=None, guid=None, cover=True):
        """写成一个新草稿。返回 GUID。

        - 不提供 guid：新建文件夹（推荐）
        - 提供 guid：只允许覆盖**自己创建的**（带 .dsh-created 标记）草稿
        """
        self._sync_tracking()
        dur = self.duration_ms()
        if guid:
            folder = os.path.join(DRAFT_ROOT, guid)
            if not os.path.exists(os.path.join(folder, MARKER)):
                raise PermissionError('草稿 %s 不是本工具创建的，拒绝覆盖（防误伤）' % guid)
            for f in os.listdir(folder):
                if f.endswith('.bjson'):
                    os.remove(os.path.join(folder, f))
        else:
            guid = str(_uuid.uuid4()).upper()
            folder = os.path.join(DRAFT_ROOT, guid)
            os.makedirs(folder, exist_ok=True)
            open(os.path.join(folder, MARKER), 'w').write('created by dsh bcut-edit\n')
        now = datetime.datetime.now()
        fn = '%02d-%02d-%02d-%03d--{%s}.bjson' % (
            now.hour, now.minute, now.second, now.microsecond // 1000, _uuid.uuid4())
        path = os.path.join(folder, fn)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, ensure_ascii=False, indent=1)
        if cover and not os.path.exists(os.path.join(folder, 'cover.jpg')):
            try:
                self.make_cover(os.path.join(folder, 'cover.jpg'))
            except Exception as e:
                print('  封面生成失败（不影响草稿）:', e)
        _register(guid, name or now.strftime('%Y%m%d%H%M'), dur)
        return guid

    def to_json(self):
        self._sync_tracking()
        return json.dumps(self.data, ensure_ascii=False, indent=1)


def summarize(guid: str) -> str:
    """把一份草稿的骨架打成可读文本（不加载 GUI 也能看清工程内容）"""
    o = load(guid)
    tl = o['timelineWidget']['timeline']
    cfg = tl['config']
    L = []
    L.append('草稿 %s' % guid)
    L.append('画布 %dx%d  %.3g fps  音频 %dHz/%dch' % (
        cfg['videoRes']['width'], cfg['videoRes']['height'],
        cfg['videoFps']['num'] / cfg['videoFps']['den'],
        cfg['audioRes']['sampleRate'], cfg['audioRes']['channelCount']))
    for ti, tr in enumerate(tl['videoTracks']):
        L.append('视频轨 %d：%d 段，%d 个转场' % (ti, len(tr['clips']), len(tr.get('transitions', []))))
        for i, c in enumerate(tr['clips']):
            ai = c.get('assetInfo', {})
            L.append('   #%-2d %8d→%-8d (%.2fs) trim %d..%d  %s%s' % (
                i, c['inPoint'], c['outPoint'], (c['outPoint'] - c['inPoint']) / 1000,
                c.get('trimIn', 0), c.get('trimOut', 0),
                ai.get('displayName') or os.path.basename(c.get('sourcePath') or ''),
                '  [图]' if ai.get('type') == 3 else ''))
            for x in tr.get('transitions', []):
                if x.get('srcIndex') == i:
                    L.append('        ↑ 转场「%s」%dms' % (x.get('transitionName'), x.get('transitionDur')))
    for ti, tr in enumerate(tl['audioTracks']):
        L.append('音频轨 %d：%d 段' % (ti, len(tr['audioClips'])))
        for c in tr['audioClips']:
            L.append('   %8d→%-8d trim %d..%d  %s' % (
                c['inPoint'], c['outPoint'], c.get('trimIn', 0), c.get('trimOut', 0),
                c['assetInfo'].get('displayName')))
    for ti, tr in enumerate(tl['captionTracks']):
        L.append('字幕轨 %d：%d 条' % (ti, len(tr['captions'])))
        for c in tr['captions'][:200]:
            L.append('   %8d→%-8d  %s' % (c['inPoint'], c['outPoint'], c['captionText']))
    end = 0
    for tr in tl['videoTracks'] + tl['audioTracks'] + tl['captionTracks']:
        for k in ('clips', 'audioClips', 'captions'):
            for c in tr.get(k, []):
                end = max(end, c['outPoint'])
    L.append('总时长 %.3f 秒' % (end / 1000))
    return '\n'.join(L)

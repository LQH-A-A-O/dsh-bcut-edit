"""bcut-edit 自检 —— 不碰你真实的必剪草稿列表。

做法：把 DRAFT_ROOT 指到一个临时目录（环境变量 BCUT_DRAFT_ROOT），
在里面建草稿、读回来、逐字段断言，最后报告。

跑法：
    python selftest.py
"""
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import env_paths  # noqa: E402

SANDBOX = os.path.join(env_paths.tmp_dir(), 'selftest')
os.environ['BCUT_DRAFT_ROOT'] = os.path.join(SANDBOX, 'Bcut Drafts')

import bcut_draft as bd          # noqa: E402
import edit_plan as ep           # noqa: E402
import ffmpeg_edit as fe         # noqa: E402

PASS = []
FAIL = []


def check(name, cond, extra=''):
    (PASS if cond else FAIL).append(name)
    print('  %s %-52s %s' % ('✅' if cond else '❌', name, extra))


def make_assets():
    """用 ffmpeg 造测试素材（不碰用户的任何文件）"""
    d = os.path.join(SANDBOX, 'media')
    os.makedirs(d, exist_ok=True)
    ff = fe.ffmpeg_path()
    if not ff:
        print('  ⚠ 没有 ffmpeg，只能测草稿部分，跳过素材生成')
        return {}
    out = {}
    enc = fe.pick_encoder()
    print('    （造素材用编码器: %s）' % enc)
    for name, size, dur, freq in (('a', '640x480', 3, 440), ('b', '480x640', 2, 660)):
        p = os.path.join(d, name + '.mp4')
        if os.path.exists(p) and os.path.getsize(p) > 1000:
            out[name] = p
            continue
        # ⚠️ 必须检查返回码：ffmpeg 失败时**会留下一个 0 字节的输出文件**，
        #    只看 os.path.exists 会拿到假素材，后面全线崩（踩过）
        r = subprocess.run([ff, '-hide_banner', '-y', '-f', 'lavfi',
                            '-i', 'testsrc=size=%s:rate=30' % size,
                            '-f', 'lavfi', '-i', 'sine=frequency=%d' % freq,
                            '-t', str(dur), '-c:v', enc, '-pix_fmt', 'yuv420p',
                            '-c:a', 'aac', '-shortest', p],
                           capture_output=True, text=True, encoding='utf-8', errors='replace')
        if r.returncode != 0 or not os.path.exists(p) or os.path.getsize(p) < 1000:
            print('  ⚠ 造素材 %s 失败 (exit %d): %s'
                  % (name, r.returncode, '\n'.join((r.stderr or '').splitlines()[-4:])))
            if os.path.exists(p):
                os.remove(p)
            continue
        out[name] = p
    from PIL import Image
    p = os.path.join(d, 'pic.png')
    Image.new('RGB', (800, 1200), (40, 44, 66)).save(p)
    out['pic'] = p
    p = os.path.join(d, 'bgm.wav')
    if not os.path.exists(p):
        subprocess.run([ff, '-hide_banner', '-y', '-f', 'lavfi',
                        '-i', 'sine=frequency=220:duration=8', p],
                       capture_output=True)
    if os.path.exists(p):
        out['bgm'] = p
    return out


def main():
    shutil.rmtree(SANDBOX, ignore_errors=True)
    os.makedirs(bd.DRAFT_ROOT, exist_ok=True)
    print('自检沙箱: %s\n' % SANDBOX)

    print('[1] 模板与目录')
    for name in ('video_clip', 'image_clip', 'transition', 'caption',
                 'audio_clip', 'browser_file', 'config', 'empty_draft'):
        try:
            bd.tpl(name)
            check('模板 %s' % name, True)
        except Exception as e:
            check('模板 %s' % name, False, str(e))
    cat = bd.catalog('transitions')
    check('转场目录非空', len(cat) > 0, '%d 个' % len(cat))
    named = [v['name'] for mid, v in cat.items()
             if v.get('name') and bd.resolve_transition_package(mid)]
    check('有可用（本机已装且有名字）的转场', len(named) > 0, '、'.join(named[:6]))

    print('\n[2] ffmpeg 能力')
    rep = fe.report()
    print('    ' + rep.replace('\n', '\n    '))
    check('找到 ffmpeg', fe.ffmpeg_path() is not None)
    check('有 H.264 编码器',
          any(e in fe.capabilities()['encoders']
              for e in ('libx264', 'h264_nvenc', 'h264_amf', 'h264_mf')))

    print('\n[3] 素材生成与探测')
    assets = make_assets()
    if assets.get('a'):
        i = bd.probe(assets['a'])
        check('probe 视频时长/分辨率', i['duration_ms'] > 2000 and i['width'] == 640,
              '%.2fs %dx%d' % (i['duration_ms'] / 1000, i['width'], i['height']))
    if assets.get('pic'):
        i = bd.probe(assets['pic'])
        check('probe 图片', i['width'] == 800 and i['kind'] == 'image', '%dx%d' % (i['width'], i['height']))

    print('\n[4] 生成草稿（多轨 + 转场 + 字幕 + 音频）')
    if not assets.get('a'):
        print('  跳过多轨草稿测试（没素材）')
    else:
        tl = bd.Timeline(1080, 1920, 30)
        tl.add_clip(assets['a'], trim_in=200, trim_out=2200)          # 2.0s
        tl.add_clip(assets['b'], trim_out=1000)                        # 1.0s
        if assets.get('pic'):
            tl.add_clip(assets['pic'], kind='image', duration_ms=1500)  # 1.5s
        tr = named[0] if named else None
        if tr:
            tl.add_transition(1, tr, 600)
        tl.add_caption('第一句字幕', 0, 1200)
        tl.add_caption('第二句字幕', 1500, 3000, y=-0.6)
        if assets.get('bgm'):
            tl.add_audio(assets['bgm'], start=0, volume_db=-8, fade_out=1000)
        tl.add_video_track()          # 叠一条空轨，验证多轨结构
        guid = tl.save('自检草稿')
        check('save 返回 GUID', bool(guid) and len(guid) == 36, guid)

        folder = os.path.join(bd.DRAFT_ROOT, guid)
        snaps = [f for f in os.listdir(folder) if f.endswith('.bjson')]
        check('快照文件已写出', len(snaps) == 1, snaps[0] if snaps else '')
        check('写了 .dsh-created 标记', os.path.exists(os.path.join(folder, bd.MARKER)))
        check('生成了封面 cover.jpg', os.path.exists(os.path.join(folder, 'cover.jpg')))

        o = bd.load(guid)
        tl2 = o['timelineWidget']['timeline']
        check('JSON 能重新解析', isinstance(tl2, dict))
        check('视频轨数 = 2', len(tl2['videoTracks']) == 2, str(len(tl2['videoTracks'])))
        clips = tl2['videoTracks'][0]['clips']
        check('视频片段数 = 3', len(clips) == 3, str(len(clips)))
        check('片段 0 时长 = 2000ms', clips[0]['outPoint'] - clips[0]['inPoint'] == 2000,
              '%d' % (clips[0]['outPoint'] - clips[0]['inPoint']))
        check('片段 0 trim = 200..2200',
              clips[0]['trimIn'] == 200 and clips[0]['trimOut'] == 2200)
        check('片段顺序拼接（0 结束 == 1 开始）',
              clips[0]['outPoint'] == clips[1]['inPoint'],
              '%d vs %d' % (clips[0]['outPoint'], clips[1]['inPoint']))
        check('图片片段被标成 type=3', clips[2]['assetInfo'].get('type') == 3)
        check('每条片段都有 Transform 2D',
              all(any(f.get('fxName') == 'Transform 2D' for f in c['fxs']) for c in clips))
        check('片段效果里没有残留蒙版区域',
              all('regionInfo' not in f for c in clips for f in c['fxs']))
        check('idString 唯一',
              len({c['idString'] for c in clips}) == len(clips))
        check('素材已登记进 browserPanelFiles',
              len(o['mainWindow']['browserPanelFiles']) >= 3,
              '%d 条' % len(o['mainWindow']['browserPanelFiles']))
        if tr:
            trans = tl2['videoTracks'][0]['transitions']
            check('转场已挂到 srcIndex=1',
                  len(trans) == 1 and trans[0]['srcIndex'] == 1,
                  trans[0]['transitionName'] if trans else '无')
            if trans:
                # ⚠️ 必剪认 assetInfo 而不是顶层字段 —— 必须一致，否则转场会被悄悄换掉
                ai = trans[0].get('assetInfo', {})
                want_id = [k for k, v in bd.catalog('transitions').items()
                           if v.get('name') == tr][0]
                check('转场 assetInfo 与顶层一致（必剪认 assetInfo）',
                      str(ai.get('realMaterialId')) == str(trans[0]['materialId']) == want_id
                      and ai.get('srcPath') == trans[0]['packagePath']
                      and ai.get('displayName') == tr,
                      'assetInfo.realMaterialId=%s 顶层=%s 期望=%s'
                      % (ai.get('realMaterialId'), trans[0]['materialId'], want_id))
        caps = tl2['captionTracks'][0]['captions']
        check('字幕 2 条', len(caps) == 2, str(len(caps)))
        check('字幕文本正确', [c['captionText'] for c in caps] == ['第一句字幕', '第二句字幕'])
        auds = tl2['audioTracks'][0]['audioClips']
        check('音频 1 条', len(auds) == 1)
        check('tracking 统计已同步',
              o['timelineWidget']['tracking']['tracks']['video_count'] == 3
              and o['timelineWidget']['tracking']['tracks']['pic_count'] == 1,
              'video_count=%d pic_count=%d' % (
                  o['timelineWidget']['tracking']['tracks']['video_count'],
                  o['timelineWidget']['tracking']['tracks']['pic_count']))

    print('\n[5] 草稿索引')
    ds = bd.list_drafts()
    check('list_drafts 能看到新草稿', len(ds) == 1 and ds[0]['name'] == '自检草稿',
          ds[0]['name'] if ds else '空')
    check('新草稿被标记为 mine', ds and ds[0]['mine'] is True)
    info = json.load(open(os.path.join(bd.DRAFT_ROOT, 'draftInfo.json'), encoding='utf-8'))
    check('draftInfo.json 格式正确',
          'draftInfos' in info and len(info['draftInfos']) == 1
          and info['draftInfos'][0]['duration'] > 0,
          'duration=%s' % info['draftInfos'][0]['duration'])
    s = bd.summarize(ds[0]['id'])
    check('summarize 能打印骨架', '视频轨 0' in s and '字幕轨 0' in s)
    print('    ---- summarize 输出 ----')
    for line in s.splitlines():
        print('    ' + line)

    print('\n[6] 拒绝覆盖非本工具创建的草稿')
    foreign = os.path.join(bd.DRAFT_ROOT, 'FOREIGN-GUID')
    os.makedirs(foreign, exist_ok=True)
    open(os.path.join(foreign, 'x.bjson'), 'w').write('{}')
    tl3 = bd.Timeline()
    try:
        tl3.save('x', guid='FOREIGN-GUID')
        check('覆盖别人的草稿被拒绝', False, '竟然成功了！')
    except PermissionError:
        check('覆盖别人的草稿被拒绝', True)

    print('\n[7] 方案校验')
    plan = ep.default_plan(1080, 1920, 30)
    plan['clips'] = [{'path': 'D:/不存在.mp4'}]
    errs, warns = ep.validate(plan)
    check('能识别不存在的素材', any('不存在' in e for e in errs), errs[0] if errs else '')
    plan2 = ep.default_plan()
    plan2['clips'] = [{'path': 'D:/x.mp4', 'transition': {'name': '根本没有这个转场'}}]
    errs2, warns2 = ep.validate(plan2)
    check('能给出转场缺失警告', any('本机' in w for w in warns2), warns2[0] if warns2 else '')

    print('\n[8] ffmpeg 直出（同一条方案的另一条路）')
    out = os.path.join(SANDBOX, 'out.mp4')
    if assets.get('pic') and assets.get('bgm'):
        p = ep.default_plan(640, 480, 30)
        p['clips'] = [
            {'path': assets['pic'], 'kind': 'image', 'duration_ms': 1500, 'motion': 'zoom_in'},
            {'path': assets['a'], 'trim_in': 0, 'trim_out': 1200,
             'transition': {'name': '交叉褪化', 'xffade': 'fade', 'dur': 500}},
        ]
        p['captions'] = [{'text': '直出测试字幕', 'start': 200, 'end': 1800, 'y': -0.7}]
        p['audio'] = [{'path': assets['bgm'], 'start': 0, 'volume_db': -8, 'fade_out': 800}]
        try:
            ep.to_mp4(p, out)
            d = fe.probe_duration(out)
            check('ffmpeg 直出 mp4', os.path.exists(out) and d > 1500,
                  '%.2fs %.2fMB' % (d / 1000, os.path.getsize(out) / 1048576))
        except Exception as e:
            check('ffmpeg 直出 mp4', False, str(e)[:200])
    else:
        print('  跳过（缺素材）')

    print('\n' + '=' * 62)
    print('通过 %d / %d' % (len(PASS), len(PASS) + len(FAIL)))
    if FAIL:
        print('失败：')
        for f in FAIL:
            print('   - ' + f)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())

"""bcut-edit 命令行入口。

    python bcut.py doctor                  环境体检（ffmpeg 能力 / 必剪目录 / 草稿）
    python bcut.py drafts                  列出必剪里的草稿
    python bcut.py show <GUID>             打印某个草稿的骨架（不打开 GUI 也能看清）
    python bcut.py transitions             列出本机装了哪些转场
    python bcut.py builtins                列出必剪内置素材（片头/片尾/黑场）
    python bcut.py probe <文件...>          探测素材时长/分辨率/帧率
    python bcut.py demo -o plan.json       生成一个示例剪辑方案
    python bcut.py check plan.json         校验方案
    python bcut.py build plan.json         生成必剪草稿（你在必剪里微调后导出）
    python bcut.py render plan.json -o out.mp4 [--gpu]   用 ffmpeg 直接出成品
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bcut_draft as bd          # noqa: E402
import edit_plan as ep           # noqa: E402
import env_paths                 # noqa: E402
import ffmpeg_edit as fe         # noqa: E402


def cmd_doctor(a):
    print('=== 路径发现 ===')
    print('  ' + env_paths.report().replace('\n', '\n  '))
    print()
    print('=== 必剪 ===')
    ds = bd.list_drafts()
    print('  草稿数量 : %d  （其中本工具创建 %d）' % (len(ds), sum(1 for d in ds if d['mine'])))
    print()
    print('=== ffmpeg ===')
    print(fe.report())
    print()
    print('=== 素材目录 ===')
    cat = bd.catalog('transitions')
    ins = sum(1 for mid in cat if bd.resolve_transition_package(mid))
    print('  转场     : 目录里 %d 个，本机已装 %d 个' % (len(cat), ins))
    if ins == 0:
        print('             ⚠ 本机一个都没装。必剪里手动用过一次某转场，它才会下到本地。')
    bl = bd.catalog('builtin_materials')
    have = [v.get('displayName', '?') for v in bl.values()
            if bd.resolve_material_file(v.get('srcPath'))]
    print('  内置素材 : 目录里 %d 个，本机有 %d 个  %s'
          % (len(bl), len(have), '、'.join(have) if have else '(无)'))
    return 0


def cmd_drafts(a):
    ds = bd.list_drafts()
    if not ds:
        print('没找到草稿（%s）' % bd.DRAFT_ROOT)
        return 1
    print('%-38s %-16s %8s %6s %s' % ('GUID', '名字', '时长', '快照', '标记'))
    for d in ds:
        print('%-38s %-16s %7.1fs %6d %s' % (
            d['id'], d['name'] or '', d['duration_ms'] / 1000.0, d['snapshots'],
            ('本工具创建' if d['mine'] else '') + ('' if d['has_cover'] else ' 无封面')))
    return 0


def cmd_show(a):
    print(bd.summarize(a.guid))
    return 0


def cmd_transitions(a):
    cat = bd.catalog('transitions')
    print('%-10s %-14s %-8s %s' % ('materialId', '名字', '已装', '包路径'))
    for mid, v in sorted(cat.items()):
        pkg = bd.resolve_transition_package(mid)
        print('%-10s %-14s %-8s %s' % (mid, v.get('name') or '?',
                                       '是' if pkg else '否', pkg or '(必剪里没用过这个转场)'))
    return 0


def cmd_builtins(a):
    bl = bd.catalog('builtin_materials')
    for mid, v in sorted(bl.items()):
        p = bd.resolve_material_file(v.get('srcPath'))
        print('%-10s %-20s %s' % (mid, v.get('displayName'), p or '(本机没有)'))
    return 0


def cmd_probe(a):
    for p in a.files:
        try:
            i = bd.probe(p)
            print('%-50s %8.2fs  %dx%d  %.3ffps  %s' % (
                os.path.basename(p), i['duration_ms'] / 1000.0, i['width'], i['height'],
                i['fps_num'] / max(i['fps_den'], 1), i['kind']))
        except Exception as e:
            print('%-50s ❌ %s' % (os.path.basename(p), e))
    return 0


def cmd_demo(a):
    plan = ep.demo_plan(out_path=a.out)
    print('示例方案已写到 %s' % a.out)
    print('  素材目录: %s' % plan['_demo_dir'])
    print('  片段 %d 段，字幕 %d 条' % (len(plan['clips']), len(plan['captions'])))
    print('\n下一步：')
    print('  python bcut.py check  "%s"' % a.out)
    print('  python bcut.py build  "%s"' % a.out)
    print('  python bcut.py render "%s" -o out.mp4' % a.out)
    return 0


def cmd_check(a):
    plan = ep.load(a.plan)
    errs, warns = ep.validate(plan)
    for e in errs:
        print('❌ %s' % e)
    for w in warns:
        print('⚠  %s' % w)
    print('方案 %s：%d 段视频 / %d 条字幕 / %d 条音频，画布 %dx%d@%gfps'
          % (a.plan, len(plan['clips']), len(plan['captions']), len(plan['audio']),
             plan['width'], plan['height'], plan['fps']))
    return 1 if errs else 0


def cmd_build(a):
    plan = ep.load(a.plan)
    errs, warns = ep.validate(plan)
    for w in warns:
        print('⚠  %s' % w)
    if errs:
        for e in errs:
            print('❌ %s' % e)
        return 1
    guid = ep.to_bcut(plan, name=a.name, guid=a.guid)
    print('✅ 已生成必剪草稿 %s' % guid)
    print('   名字: %s' % (a.name or plan.get('name')))
    print('   打开必剪就能在草稿列表里看到它，微调后自己导出。')
    print('   （没有动你任何已有草稿）')
    return 0


def cmd_render(a):
    plan = ep.load(a.plan)
    errs, warns = ep.validate(plan)
    for w in warns:
        print('⚠  %s' % w)
    if errs:
        for e in errs:
            print('❌ %s' % e)
        return 1
    out = ep.to_mp4(plan, a.out, gpu=a.gpu, crf=a.crf)
    dur = fe.probe_duration(out) / 1000.0
    print('✅ 已输出 %s  (%.2f 秒, %.1f MB)' % (out, dur, os.path.getsize(out) / 1048576))
    return 0


def main():
    ap = argparse.ArgumentParser(description='必剪草稿生成 + ffmpeg 直出')
    sub = ap.add_subparsers(dest='cmd', required=True)

    sub.add_parser('doctor').set_defaults(func=cmd_doctor)
    sub.add_parser('drafts').set_defaults(func=cmd_drafts)

    p = sub.add_parser('show'); p.add_argument('guid'); p.set_defaults(func=cmd_show)
    sub.add_parser('transitions').set_defaults(func=cmd_transitions)
    sub.add_parser('builtins').set_defaults(func=cmd_builtins)

    p = sub.add_parser('probe'); p.add_argument('files', nargs='+'); p.set_defaults(func=cmd_probe)

    p = sub.add_parser('demo')
    p.add_argument('-o', '--out', default=os.path.join(os.getcwd(), 'plan_demo.json'))
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser('check'); p.add_argument('plan'); p.set_defaults(func=cmd_check)

    p = sub.add_parser('build')
    p.add_argument('plan'); p.add_argument('--name'); p.add_argument('--guid')
    p.set_defaults(func=cmd_build)

    p = sub.add_parser('render')
    p.add_argument('plan'); p.add_argument('-o', '--out', required=True)
    p.add_argument('--gpu', action='store_true'); p.add_argument('--crf', type=int, default=18)
    p.set_defaults(func=cmd_render)

    a = ap.parse_args()
    return a.func(a)


if __name__ == '__main__':
    sys.exit(main())

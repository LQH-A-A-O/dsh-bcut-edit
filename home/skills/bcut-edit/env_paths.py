"""路径发现 —— 让这个 skill 换台机器也能跑。

打包前这里原本写死了作者的绝对路径（`E:\\aqca\\...`、`D:\\BcutBilibili`、
`E:\\ACLOS\\...`），转发出去在别人机器上必然全废。现在统一在这里发现：

    必剪安装目录  环境变量 → 注册表卸载项 → 常见位置 → 各盘根目录搜 BCUT.exe
    必剪数据目录  %LOCALAPPDATA%\\BCUT\\local_res（本来就与用户无关，跟着用户走）
    草稿目录      ~/Documents/Bcut Drafts（可用环境变量覆盖）
    ffmpeg        环境变量 → <skill>/bin → PATH → 常见位置

所有路径都可以用环境变量覆盖，方便调试：
    BCUT_HOME / BCUT_LOCAL_RES / BCUT_DRAFT_ROOT / BCUT_FFMPEG / BCUT_EDIT_TMP
"""
import os
import shutil
import sys

SKILL_ROOT = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser('~')

_CACHE = {}


def _first_existing(paths):
    for p in paths:
        if p and os.path.exists(p):
            return p
    return None


def skill_dir(*parts):
    return os.path.join(SKILL_ROOT, *parts)


# ---------------------------------------------------------------- 必剪
def _from_registry():
    """从注册表卸载项里找必剪的安装目录（DisplayIcon 通常是 <dir>\\BCUT.exe）"""
    if sys.platform != 'win32':
        return []
    try:
        import winreg
    except ImportError:
        return []
    found = []
    roots = [(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall'),
             (winreg.HKEY_LOCAL_MACHINE,
              r'SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall'),
             (winreg.HKEY_CURRENT_USER, r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall')]
    for hive, sub in roots:
        try:
            key = winreg.OpenKey(hive, sub)
        except OSError:
            continue
        for i in range(winreg.QueryInfoKey(key)[0]):
            try:
                name = winreg.EnumKey(key, i)
                k = winreg.OpenKey(key, name)
                def val(n):
                    try:
                        return winreg.QueryValueEx(k, n)[0]
                    except OSError:
                        return ''
                if 'bcut' not in (name + val('DisplayName') + val('InstallLocation')).lower():
                    continue
                for cand in (val('InstallLocation'), os.path.dirname(val('DisplayIcon'))):
                    if cand and os.path.exists(os.path.join(cand, 'BCUT.exe')):
                        found.append(cand)
            except OSError:
                continue
    return found


def _scan_drives():
    """兜底：各盘根目录和常见安装位置找 BCUT.exe"""
    names = ['BcutBilibili', 'BCUT', 'Bcut', 'bcut']
    bases = [r'C:\Program Files', r'C:\Program Files (x86)',
             os.path.join(HOME, 'AppData', 'Local'),
             os.path.join(HOME, 'AppData', 'Local', 'Programs'),
             os.path.join(HOME, 'AppData', 'Roaming')]
    outs = []
    for b in bases:
        for n in names:
            outs.append(os.path.join(b, n))
    for drive in 'CDEFGH':
        for n in names:
            outs.append('%s:\\%s' % (drive, n))
    return outs


def bcut_home():
    if 'bcut_home' in _CACHE:
        return _CACHE['bcut_home']
    cands = [os.environ.get('BCUT_HOME')] + _from_registry() + _scan_drives()
    for c in cands:
        if c and os.path.exists(os.path.join(c, 'BCUT.exe')):
            _CACHE['bcut_home'] = c
            return c
    # 再退一步：只要有 Font 目录也认（有些安装把 exe 放子目录）
    for c in cands:
        if c and os.path.isdir(os.path.join(c, 'Font')):
            _CACHE['bcut_home'] = c
            return c
    _CACHE['bcut_home'] = None
    return None


def font_dir():
    h = bcut_home()
    d = os.path.join(h, 'Font') if h else None
    return d if d and os.path.isdir(d) else None


def local_res():
    p = os.environ.get('BCUT_LOCAL_RES') or os.path.join(
        HOME, 'AppData', 'Local', 'BCUT', 'local_res')
    return p


def cover_cache():
    return os.path.join(HOME, 'AppData', 'Roaming', 'BCUT', 'Cache', 'Cover')


def draft_root():
    return os.environ.get('BCUT_DRAFT_ROOT') or os.path.join(
        HOME, 'Documents', 'Bcut Drafts')


# ---------------------------------------------------------------- ffmpeg
_DEV_HINTS = [                      # 可选：把 ffmpeg 放在这些目录里也能被发现
]
_env_hint = os.environ.get('BCUT_FFMPEG_EXTRA_DIRS', '')
if _env_hint:
    _DEV_HINTS = [d for d in _env_hint.split(os.pathsep) if d]


def _find_exe(name):
    env = os.environ.get('BCUT_FFMPEG') or os.environ.get('FFMPEG')
    if env:
        if os.path.isdir(env):
            p = os.path.join(env, name)
            if os.path.exists(p):
                return p
        elif os.path.basename(env).lower().startswith(name[:3]):
            if os.path.exists(env):
                return env
    cands = [skill_dir('bin', name)]
    w = shutil.which(name)
    if w:
        cands.append(w)
    for d in _DEV_HINTS:
        cands.append(os.path.join(d, name))
    for d in (r'C:\ffmpeg\bin', r'C:\Program Files\ffmpeg\bin',
              os.path.join(HOME, 'scoop', 'shims')):
        cands.append(os.path.join(d, name))
    return _first_existing(cands)


def ffmpeg():
    if 'ffmpeg' not in _CACHE:
        _CACHE['ffmpeg'] = _find_exe('ffmpeg.exe')
    return _CACHE['ffmpeg']


def ffprobe():
    if 'ffprobe' not in _CACHE:
        p = _find_exe('ffprobe.exe')
        # 没有 ffprobe 时用 ffmpeg -i 也能凑合，但这里明确返回 None 让上层报错
        _CACHE['ffprobe'] = p
    return _CACHE['ffprobe']


# ---------------------------------------------------------------- 临时目录
def tmp_dir():
    """临时产物目录。**不要**用 %TEMP% 下新建的子目录（DSH 沙箱里那种目录不可写）。"""
    d = os.environ.get('BCUT_EDIT_TMP') or skill_dir('.tmp')
    os.makedirs(d, exist_ok=True)
    return d


# ---------------------------------------------------------------- 占位符
def resolve(s):
    """把打包时写进模板/目录的占位符还原成本机真实路径。

    模板里原本是作者机器的绝对路径，转发出去必须换成占位符，用的时候再解析回本机路径。
    """
    if not isinstance(s, str):
        return s
    if '__BCUT_FONT__' in s:
        f = font_dir()
        return s.replace('__BCUT_FONT__', (f or '').replace('\\', '/')) if f else ''
    if '__LOCAL_RES__' in s:
        return s.replace('__LOCAL_RES__', local_res().replace('\\', '/'))
    if '__CACHE_COVER__' in s:
        return s.replace('__CACHE_COVER__', cover_cache().replace('\\', '/'))
    return s


def resolve_deep(o):
    if isinstance(o, dict):
        return {k: resolve_deep(v) for k, v in o.items()}
    if isinstance(o, list):
        return [resolve_deep(v) for v in o]
    return resolve(o)


def report():
    h = bcut_home()
    L = ['skill 目录 : %s' % SKILL_ROOT,
         '必剪安装   : %s' % (h or '❌ 没找到（可用环境变量 BCUT_HOME 指定）'),
         '必剪字体   : %s' % (font_dir() or '❌ 没找到'),
         '本地素材库 : %s %s' % (local_res(), '✅' if os.path.isdir(local_res()) else '❌'),
         '草稿目录   : %s %s' % (draft_root(), '✅' if os.path.isdir(draft_root()) else '❌'),
         'ffmpeg     : %s' % (ffmpeg() or '❌ 没找到，跑 python get_ffmpeg.py 下载'),
         'ffprobe    : %s' % (ffprobe() or '❌ 没找到'),
         '临时目录   : %s' % tmp_dir()]
    return '\n'.join(L)


if __name__ == '__main__':
    print(report())

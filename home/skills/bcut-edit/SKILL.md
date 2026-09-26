---
name: bcut-edit
description: 用代码做视频剪辑 —— 生成/编辑必剪(Bcut)草稿工程（多轨道、转场、字幕、配乐），或用 ffmpeg 直接渲染出成品 mp4。必剪的 .bjson 草稿是明文 JSON，所以工程可以完全脚本化；两条后端共用同一份「剪辑方案」。
whenToUse: 用户要做视频剪辑、拼剪素材、加字幕、配 BGM、做图片+音乐宣传片、游戏录像切片，或提到必剪 / Bcut / 视频工程 / 时间线 / 转场 / 字幕烧录时。
---

# bcut-edit —— 用代码剪辑视频

两条后端，**同一份方案**：

| 后端 | 命令 | 特点 |
|---|---|---|
| **必剪草稿** | `bcut.py build plan.json` | 生成一个必剪工程，你在必剪里看效果、微调、导出。**转场是必剪原版效果** |
| **ffmpeg 直出** | `bcut.py render plan.json -o out.mp4` | 全自动出成品。转场用 ffmpeg 的 xfade（视觉上不是必剪那些专有转场） |

两者可以同时用：先 `build` 出草稿对着看，定了再 `render` 批量出片。

---

## 一、关键事实（实测，别再重新踩）

### 必剪的工程就是明文 JSON

```
C:\Users\<用户>\Documents\Bcut Drafts\<GUID>\<时-分-秒-毫秒>--{uuid}.bjson
C:\Users\<用户>\Documents\Bcut Drafts\draftInfo.json        <- 草稿索引
```

- **无加密、无压缩**，`json.load` 直接读。
- 必剪每 15 秒自动存一份快照到同一文件夹。
- 索引 `draftInfo.json` 里每条：`id`(=文件夹名 GUID) / `name` / `modifyTime`(epoch ms) / `duration`(**微秒**)。
- 必剪**没有命令行、没有 COM、没有脚本 API**（Qt 应用，主程序 `BCUT.exe`）。
  所以"生成工程"能自动化，**"点导出"要么人手点，要么用 screen-control 去点**（脆弱，当兜底）。

### 时间单位

| 字段 | 含义 | 单位 |
|---|---|---|
| `inPoint` / `outPoint` | 在**时间线**上的起止 | 毫秒 |
| `trimIn` / `trimOut` | 在**源素材**里截哪一段 | 毫秒 |

恒有 `outPoint - inPoint == trimOut - trimIn`（不加速的话）。

### 多轨结构

```
timelineWidget
├── timeline
│   ├── config{videoRes, videoFps, audioRes}
│   ├── videoTracks[]    每轨 {clips[], transitions[]}
│   ├── audioTracks[]    每轨 {audioClips[]}
│   ├── captionTracks[]  每轨 {captions[]}
│   └── stickerTracks[] / filterTracks[] / timelineVideoFxTracks[] / adjustTracks[]
├── tracking{tracks{...}}   <- ⚠️ 是 timelineWidget 的子节点，**不在 timeline 里**
└── tts{} / ttv{}
```

**转场怎么挂**：`videoTracks[i].transitions[].srcIndex = k` 表示"片段 k-1 和 k 之间"。

### 素材类型枚举（从真实草稿统计出来的，60+ 个片段）

| 类型 | assetItemType | type | mediaType | videoType | browserPanelFiles.itemType |
|---|---|---|---|---|---|
| 用户视频 | 4 | 1 | 0 | 1 | 4 |
| 用户图片 | 6 | 3 | 1 | 0 | 6 |
| 必剪内置素材（片头/片尾） | 9 | 1 | 0 | 0 | — |
| 必剪内置图片（黑场） | 10 | 3 | 1 | 0 | — |

其他实测值：`volume` 恒为 `{"leftVolume":0,"rightVolume":0}`（0 = 原音量，不是静音）；
每条片段都带一组标准效果（`Transform 2D` + 2×`Mask Generator` + `Transform 2D`），**别删**；
`deNoiseLevel` 3=开 / -1=关，两种真实草稿里都有。

### ⚠️ 转场：必剪认的是 `assetInfo`，不是顶层字段

转场对象里有**两套并行字段**：

```
顶层:      materialId / transitionName / packagePath / cover
assetInfo: realMaterialId / displayName / srcPath / coverPath
```

**必剪以 `assetInfo` 为准**。真机验证踩过：只改顶层字段把转场指定成「交叉褪化」(2744)，
必剪按 assetInfo 里的 5565464 解析成了「折叠翻页」—— **不报错，就是悄悄换掉**。

另外：`transitionName` 在不同草稿里对同一个 materialId 有过不同取值（必剪改过素材但没同步名字），
所以**名字也只信 `assetInfo.displayName`**。`catalog/transitions.json` 就是按这个规则生成的，
并且每个 materialId 都存了一份完整原型 `templates/transition_<id>.json`，生成时整块换掉。

### 必剪加载外来工程后的两个自动行为

真机实测（把代码生成的草稿在必剪里打开）：

1. **它会在 `videoTracks[0]` 插一条空轨**，把内容挪到 `videoTracks[1]`。数据不丢，只是轨道编号变了。
   所以读回工程时别假设内容一定在 `videoTracks[0]`。
2. **每 15 秒自动存一份新快照**到同一个文件夹，并重新生成 `cover.jpg`。
   所以"必剪有没有真的接收这个工程"有个零成本判据：**看快照数有没有变多**。

### 必剪的坑

| 症状 | 原因 / 解法 |
|---|---|
| `Decoder (codec png) not found` | 在用 ACLOS 那个精简版，它没有图片解码器。跑 `get_ffmpeg.py` 装完整版 |

| 来源 | 情况 |
|---|---|
| 必剪自带 | FFmpeg **7.1** 全套 DLL（avcodec/avfilter/avformat…）但**没有 CLI**，用不了 |
| 录屏软件附带的 | 常见的是**精简版**：没有 libx264、没有 `subtitles`/`drawtext` 滤镜，有的**连 png/jpeg 解码器都砍了** → 剪不了图、烧不了字幕 |
| 完整版 | 跑 `python get_ffmpeg.py` 下到本技能目录的 `bin\`（约 190 MB）。已装过的用环境变量 `BCUT_FFMPEG` 指过去 |

`python bcut.py doctor` 会把这些能力实际探测出来，别猜。

---

## 二、快速上手

```powershell
# $S = 本技能目录（装好后一般在 DSH_HOME\skills\bcut-edit）
$PY = 'python'
$S  = "$env:DSH_HOME\skills\bcut-edit"

& $PY "$S\bcut.py" doctor           # 环境体检（先看这个，有 ❌ 先解决）
& $PY "$S\bcut.py" drafts           # 列出现有草稿
& $PY "$S\bcut.py" show <GUID>      # 打印某草稿的骨架（不开 GUI 也能看清）
& $PY "$S\bcut.py" transitions      # 本机装了哪些转场
& $PY "$S\bcut.py" builtins         # 内置素材（片头/片尾/黑场）
& $PY "$S\bcut.py" probe D:\a.mp4   # 探测素材时长/分辨率/帧率

# 生成一份示例方案，照着改
& $PY "$S\bcut.py" demo   -o plan.json
& $PY "$S\bcut.py" check    plan.json
& $PY "$S\bcut.py" build    plan.json      # -> 必剪草稿
& $PY "$S\bcut.py" render   plan.json -o out.mp4 --gpu
```

### 在代码里用

```python
import sys; sys.path.insert(0, r'<本技能目录>')
import bcut_draft as bd, edit_plan as ep

# A) 直接搭时间线
tl = bd.Timeline(1080, 1920, 30)                  # 竖屏短视频
tl.add_clip(r'D:\素材\a.mp4', trim_in=1000, trim_out=5000)   # 截 1~5 秒
tl.add_clip(r'D:\素材\b.mp4')                                # 接在后面
tl.add_transition(1, '折叠翻页', 800)                        # 片段 0/1 之间
tl.add_caption('第一句话', 0, 2000, y=-0.76)
tl.add_audio(r'D:\音乐\bgm.mp3', volume_db=-6, fade_out=2000)
tl.add_video_track()                              # 加第二条视频轨（画中画/叠加）
guid = tl.save('我的片子')                          # 返回 GUID，必剪里就能看到

# B) 走方案（两条后端共用）
plan = ep.load(r'plan.json')
ep.to_bcut(plan)                                  # 生成必剪草稿
ep.to_mp4(plan, r'out.mp4', gpu=True)             # 或直接出片
```

---

## 三、剪辑方案（plan）格式

```json
{
  "name": "千年学院宣传片",
  "width": 1080, "height": 1920, "fps": 30,
  "keep_original_audio": true, "original_volume_db": -3,
  "subtitle_size": 54,
  "clips": [
    {"path": "D:/素材/a.mp4", "trim_in": 0, "trim_out": 3000},
    {"path": "D:/素材/封面.png", "kind": "image", "duration_ms": 4000, "motion": "zoom_in"},
    {"path": "D:/素材/b.mp4", "trim_in": 1000,
     "transition": {"name": "折叠翻页", "xffade": "wipeleft", "dur": 800}}
  ],
  "captions": [
    {"text": "第一句", "start": 0, "end": 2000, "x": 0.0, "y": -0.76}
  ],
  "audio": [
    {"path": "D:/音乐/bgm.mp3", "start": 0, "volume_db": -6, "fade_in": 1500, "fade_out": 3000}
  ]
}
```

- `motion`：`none` / `zoom_in` / `zoom_out` / `pan_left` / `pan_right`（图片的推拉摇，**只对 ffmpeg 直出生效**；必剪草稿里图片就是静止的，动效得在必剪里加关键帧）
- `transition.name`：必剪的转场名（`bcut.py transitions` 看有哪些）。生成草稿用它；直出时按内置对照表映射到 ffmpeg 的 xfade 类型，也可以用 `xffade` 直接指定
- `captions` 的 `x`/`y` 是**相对画面中心的位移**（-1..1），对应必剪的 `transX`/`transY`。`y=-0.76` ≈ 画面下方
- 第一段 `clips[0]` 的 `transition` 会被忽略（前面没东西可转）

---

## 四、安全约定

- `save()` **永远新建一个 GUID 文件夹**，绝不改动你已有的草稿。
- 自己创建的草稿会写 `.dsh-created` 标记；**只有带标记的**才允许用 `guid=` 覆盖。
- 自检 (`selftest.py`) 把 `BCUT_DRAFT_ROOT` 指到临时目录，**不会往你真实草稿列表里塞东西**。

---

## 五、排错

| 症状 | 原因 / 解法 |
|---|---|
| `Decoder (codec png) not found` | 在用 ACLOS 那个精简版，它没有图片解码器。跑 `get_ffmpeg.py` 装完整版 |
| `这个 ffmpeg 没有 subtitles/ass 滤镜` | 同上。没有 libass 时 `render` 会自动退化成"PIL 渲染文字成 PNG + overlay"，效果差一些 |
| 没有可用的 H.264 编码器 | 装完整版（会带 libx264） |
| 生成的草稿在必剪里看不到 | 检查 `draftInfo.json` 里有没有这条记录；或名字重了 |
| 必剪里片段显示"素材丢失" | `sourcePath` 是绝对路径，素材移动过就会丢。重新生成或把素材放回原位置 |
| 转场加不上 | 该转场本机没装（`bcut.py transitions` 看"已装"列）。必剪要先用过一次那个转场才会下到本地 |
| 生成的草稿打开是空的 | 必剪版本变了导致 schema 变。对照一份你手改过的草稿重新抽模板（`build_templates.py`） |
| **转场被换成了别的** | 只改了顶层 `materialId`。必剪认 `assetInfo`，必须整块换（见上文） |
| 生成的草稿时长对、画面也对，就是没声音 | 图片片段用了 `-an` 生成 → 没有音轨 → `acrossfade` 会 EINVAL。本 skill 已强制所有片段都带音轨 |

### ffmpeg 的坑

一句话：**默认优先 libx264，不要优先 nvenc**。`h264_nvenc` 虽然在本机可用，
但对 lavfi 源/奇数尺寸会直接失败，而且**失败时 ffmpeg 会留下一个 0 字节的输出文件** ——
调用方只看 `os.path.exists` 就会拿到假素材，后面全线崩。`pick_encoder()` 已处理这个偏好。

---

## 六、文件

```
bcut.py            命令行入口
bcut_draft.py      草稿读写：Timeline / list_drafts / load / summarize / probe
ffmpeg_edit.py     ffmpeg 底座：cut / concat / xfade_concat / make_still / 字幕 / 混音 / render_plan
edit_plan.py       方案 schema + check + to_bcut / to_mp4 + 时间轴映射
env_paths.py       路径发现（必剪安装目录 / 草稿目录 / 素材库 / ffmpeg）
selftest.py        自检（沙箱里跑，不动真实草稿）
get_ffmpeg.py      下载完整版 ffmpeg
templates/         从真实草稿抽出来的原型对象（片段/转场/字幕/音频/素材表项/画布配置）
catalog/           transitions.json（转场目录）、builtin_materials.json（片头/片尾/黑场）
```

`templates/` 和 `catalog/` 里的本机路径是**占位符**（`__LOCAL_RES__` / `__BCUT_FONT__` /
`__CACHE_COVER__`），运行时由 `env_paths` 还原成本机路径 —— 所以这套文件可以跨机器用。
想加别的转场/内置素材：在必剪里用一次，让素材下到本地，再把对应的 json 补进 `catalog/`。

所有路径都能用环境变量覆盖，方便调试或非标准安装：
`BCUT_HOME` / `BCUT_LOCAL_RES` / `BCUT_DRAFT_ROOT` / `BCUT_FFMPEG` / `BCUT_EDIT_TMP`。

# quick-logcat 竖屏解说视频

把小红书图文帖《只看一行Logcat？不用开IDE》重做成 1080×1920 / 52s 的解说视频。
视觉沿用原帖（米色底、黑标题、蓝 accent、绿代码色），真实工具截图全部保留。

## 产物

- `quick-logcat.mp4` — 成片（H.264 + AAC，52.0s，3.0 MB）
- `cover.jpg` — 首帧封面
- `POST.md` — 配套的小红书文案

## 素材

| 文件 | 来源 | 用在哪 |
|---|---|---|
| `assets/ui_full.png` | 仓库 `docs/images/image-1.png` | S2 底图、S5 主图与三处放大 |
| `assets/ui_empty.png` | 仓库 `docs/images/image.png` | S4 浏览器窗里的空状态 |
| `assets/gh_full.png` | GitHub 仓库页整页截图（Playwright 抓的，2880×9074） | S8 README 滚动 |
| `assets/gh_top.png` | 同上，首屏 | 取 Star 按钮坐标用 |
| `assets/card1.png` | 原帖首图 | 备用，成片未用 |

GitHub 那段不是录屏文件，是整页长截图在合成里用 CSS 位移推，这样每一帧都能被
`__seekToTime` 精确复现——嵌 `<video>` 反而会有 seek 不准的问题。

## 重新生成

```bash
# 1) 改文案 → 重出配音和字幕时间轴（timeline.json / timeline.js 一起更新）
../../.venv/bin/python build_audio.py

# 2) 改画面后先看静帧，参数是秒
../../.venv/bin/python stills.py 2 14 25 47

# 3) 出片
../../.venv/bin/python render.py
```

浏览器里直接打开 `index.html#preview` 可以自播预览；不带 `#preview` 时页面是静止的，
由渲染器逐帧 `seek`。

## 时间轴怎么对齐的

`build_audio.py` 逐句跑 edge-tts，量出每句真实时长后用 `adelay` 摆到绝对位置，
再写出 `timeline.js`。合成页面的字幕直接读它，所以改文案不用手抄时间码。
`index.html` 里的 `SCENES` 是手写的分幕边界，对着 `timeline.json` 的句子起止取的整。

## 踩过的坑

- `amix` 在最后一句结束时就收尾，`-t` 只能截不能补。要留收尾镜头必须 `apad`，
  否则 `-shortest` 会把视频一起截短。
- 每一幕的内容动画都从 local 0 起算，如果幕切换时才开始淡入，会露出一瞬空白底。
  解决办法是内容时钟提前 `lead` 起跑（首幕 0.9s，其余 0.45s），
  这样新一幕的标题浮出来时上一幕才淡出。
- 清单和对照表的行高由 JS 从 0 撑开。留着固定高度再淡入的话，
  没出现的行会在卡片里留一块空白。

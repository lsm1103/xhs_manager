# Frame packet: 03-four-gates

## Project inputs

- Project: /Users/xm/Desktop/xm_project/code/ai_agent_project/xhs_manager/videos/ai-plus-business-value
- Design tokens: /Users/xm/Desktop/xm_project/code/ai_agent_project/xhs_manager/videos/ai-plus-business-value/frame.md
- RULES_DIR: /Users/xm/.agents/skills/hyperframes-animation/rules

## Assigned storyboard block

## Frame 3 — 四道商业闸门

- scene: 一条连续传送带依次穿过需求、流程、责任、复用四道闸门
- voiceover: "判断一个 AI 项目，先过四道闸门：问题是否高频，能否嵌入现有流程，出错后谁负责，以及第二个客户能不能复用。"
- duration: 12s
- poster: 10.2s
- transition_in: push-slide UP
- status: outline
- src: compositions/frames/03-four-gates.html
- type: feature_showcase
- persuasion: Numbered enumeration + progressive disclosure
- beat: comprehension + mastery
- blueprint: spatial-pan-stations (Adapt)
- focal: 四道竖直闸门
- roles: 闸门 = foreground subject · 传送带对象 = supporting · 底部流程轨道 = background
- sfx: mechanical-click, pass-through

narrativeRole: 把抽象商业判断压缩成可以逐项检查的四道门。
keyMessage: 前三道决定能否上线，第四道决定它是产品还是项目制服务。

Adapt: 保留空间站点逐个进入主视区的签名动作，把站点变成四道竖门并维持同一传送带。
Scene 1 (0.0–2.5s): 编号 01 的“需求”闸门从下方升起，橙色物块穿过后留下“高频 / 有预算”两枚小印章。
Scene 2 (2.5–5.0s): 舞台向上推进至 02“流程”，物块嵌入一条既有轨道，而不是分叉到第二工作台。
Scene 3 (5.0–7.5s): 03“责任”出现，审核、拒绝、回滚三个小符号依次点亮，物块获得可追踪编号。
Scene 4 (7.5–10.2s): 04“复用”展开，单个物块复制为第二个客户的同构模块，只有少量标签变化。
Scene 5 (10.2–12.0s): 四道闸门缩成纵向清单，前三项标“上线”，第四项单独以火橙标“产品化”，稳定持有。

## Selected blueprint: spatial-pan-stations

# spatial-pan-stations — Spatial Pan / Stations

**intent**: Pre-place a sequence of labeled stations on one oversized canvas, then traverse it with a single virtual camera — repeated lateral/diagonal pans that center each station in turn and reveal a callout at every stop, landing held on a final station.

**roles served**

- Hook (from hook-pan-timeline): a horizontal timeline of evenly-spaced milestones, left-panned beat by beat, each marker getting a spring-popped callout, landing on the present moment ("evolution / milestone walk leading up to us").
- Problem (from problem-camera-pan-stations): a connected web of pain "stations" linked by hand-drawn leading lines, diagonally panned station to station, ending on a tangled scribble knot ("too many disconnected steps — it's a mess").
- Product_Intro (from concept-demo-decode-pan): a two-shot strip bridged by ONE lateral pan — shot 1 holds a static phrase whose accent word 3D-flap-DECODES (the concept lands), then the camera pans across the strip (with background parallax) into shot 2, where a cursor drives a live typing demo. Pairs this pan with `cursor-ui-demo`'s focal-locked tracked typing.

**duration**: 7–10s (union of Hook 8–10s, Problem ~7s, concept-demo ~7s)

**shot structure**
One oversized flat canvas on a solid `[bg color]`; all stations/markers pre-placed in world space; `[accent color]` text + simple line-icons; one virtual `.world` camera pans ease-in-out between stops. Each station holds ~1.0s.

- Scene 1 (0.0–~1.0s): Camera opens on station 1 — `[label 1 / first step]` centered. A reveal lands on it (see variants). Camera then begins to PAN toward station 2, sliding station 1 out of frame.
- Scene 2 → Scene N-1 (~1.0s each): Camera PANS (ease-in-out) to center the next station; on arrival its `[label k]` (+ optional `[secondary label]`) is REVEALED with the role reveal. Repeat per station.
- Scene N (final, ~last beat): One last pan lands on the terminal station; the final `[callout / landing element]` reveals and HOLDS to the end. Camera goes static on the punchline.

- Variant — Hook: stations sit as evenly-spaced `[markers]` on a thin horizontal `[timeline]` (lower third); pans are LEFT-only along the single axis (timeline scrolls left). Each callout is a bordered `[callout box]` + downward triangle (offset drop-shadow) that SPRING-POPS up (scale 0→100%, bouncy overshoot, transform-origin at triangle tip) reading `[label k]`; a `[secondary label, e.g. year]` fades in and RISES above it. Some mid markers arrive as plain static text revealed by the pan alone (no box). Final scene lands on the `[present-day label]`, springs, holds.
- Variant — Problem: stations are scattered across a 2D web; pans are DIAGONAL, STEERED by `[accent color]` hand-drawn lines — each station has a rough write-on line/arrow that draws toward the next and the camera follows it (Scene 1 also draws a loop/circle around the headline's key word). Each station = a white `[line-icon]` above its `[label]`, revealed plainly by the pan (no spring box). Final scene: the accent line spirals into a dense chaotic SCRIBBLE KNOT centered on the field; camera holds static on the tangle (visual punchline).

**motion vocabulary**
repeated ease-in-out camera pans (horizontal-left for Hook, diagonal-steered for Problem) across one large static canvas; pre-placed stations sliding through frame via the pan; spring-overshoot callout pop with triangle-tip origin (Hook); rise-and-fade secondary label (Hook); plain labels/icons arriving via the pan alone; rough hand-drawn "write-on" leading lines/arrows + loop/circle key-word mark (Problem); terminal chaotic-scribble knot draw (Problem); static hold on the final station/punchline.

**rule mapping**

- camera pan / traverse across the canvas (primary) → `viewport-change` (single `.world` wrapper transform; PAN mode)
- sequencing the repeated pan beats into stops → `multi-phase-camera`
- centering each station as the pan target → `coordinate-target-zoom` (used as pan-to-target, no zoom)
- spring-overshoot callout pop, triangle-tip origin (Hook) → `spring-pop-entrance`
- rise-and-fade secondary label + plain per-station label/icon reveals via the pan → `discrete-text-sequence`
- hand-drawn leading lines / arrows / loop-circle key-word mark / terminal scribble knot (Problem) → `svg-path-draw`
- station line-icons (Problem) → `svg-icon-enrichment`
- static hold on the final station / punchline → (no motion; sustained held frame, no rule needed)

**camera modifier**: The pan IS the camera. One `.world` virtual-camera transform in PAN mode — `viewport-change` — sequenced across stops by `multi-phase-camera`, each stop targeted via `coordinate-target-zoom` (pan-to-target). No depth push-in (that distinguishes this from the cluster-push-in / dataviz-pushthrough blueprints).

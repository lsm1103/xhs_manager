# Frame packet: 06-platforms

## Project inputs

- Project: /Users/xm/Desktop/xm_project/code/ai_agent_project/xhs_manager/videos/ai-plus-business-value
- Design tokens: /Users/xm/Desktop/xm_project/code/ai_agent_project/xhs_manager/videos/ai-plus-business-value/frame.md
- RULES_DIR: /Users/xm/.agents/skills/hyperframes-animation/rules

## Assigned storyboard block

## Frame 6 — 各平台真正的共识

- scene: B站、微博、X 的不同关键词汇入同一条“回到业务”主线，小红书以缺样本标记留白
- voiceover: "公开讨论的措辞不同：B站谈数据和流程，微博谈结果与交易，X 更强调治理和回滚。但它们都在把 AI 拉回真实业务。"
- duration: 10s
- poster: 8.2s
- transition_in: push-slide LEFT
- status: outline
- src: compositions/frames/06-platforms.html
- type: benefit_highlight
- persuasion: Synthesis + contrast
- beat: clarity + confidence
- blueprint: comparison-split (Adapt)
- focal: 汇流后的“真实业务”主线
- roles: 三个平台词组 = supporting · 汇流线与结论 = foreground subject · 小红书缺样本注记 = background
- sfx: paper-shuffle, merge-soft

narrativeRole: 交代跨平台调研结论，同时保留证据缺口，不把稀疏样本包装成共识。
keyMessage: 渠道语言不同，但有效讨论都越过了能力展示。

Adapt: 保留多栏对照后汇聚的签名动作，增加一个明确的“样本不足”空栏。
Scene 1 (0.0–3.2s): 三条竖栏依次推入：B站“数据 / 流程”、微博“结果 / 交易”、X“治理 / 回滚”；每栏只在旁白点名时点亮。
Scene 2 (3.2–6.8s): 三栏底部的火橙线向中央弯折汇合，词组被压缩成同一条宽线；小红书栏保持空白并盖“样本不足”。
Scene 3 (6.8–10.0s): 汇流线向上顶出巨字“真实业务”，其余平台名降为目录索引；结论保持静止供字幕读完。

## Selected blueprint: comparison-split

# comparison-split — Comparison Split-Cards

**intent**: Two paired items of equal weight shown side-by-side with mirrored 3D "book-open" tilts — the eye reads them as a balanced comparison, then a pill badge lands at each card's inner edge to punctuate. The motion IS the symmetry: two cards arriving from opposite wings into a held spread.

**roles served**

- Key_Feature (from `comparison-split-cards`): when two complementary features / capabilities of equal weight should be presented **simultaneously, not sequentially** — an A/B, a "X + Y together," paired concepts the viewer must weigh side-by-side. Not for >2 items (use `grid-card-assemble`) or sequential steps.

**duration**: 4–6s

**shot structure** (a `[bg]` canvas carrying two faint ambient glow blooms — `[accent A]` near 30%, `[accent B]` near 70% — so each side owns a color identity across a 50% symmetry axis; equal-width cards under one shared perspective parent)

- **Scene 1 (0.0–~0.8s) — title sets the concept.** A centered `[title line]` with an `[accent keyword]` slides DOWN into place from just above (a short smooth settle). The downward arrival is deliberate: it forms a non-conflicting T-shape against the cards, which arrive from the sides next.
- **Scene 2 (~0.4–1.9s) — the split-tilt entry (signature move).** Two equal-width feature cards arrive from opposite wings — `[left card]` from the left, `[right card]` from the right ~0.2s behind — each carrying a **mirrored 3D `rotateY` tilt** (left faces right, right faces left, opening like a book) and scaling ~0.85→1 as it lands. The entry overlaps the title's tail so the whole thing reads as ONE arrival, not two beats. Each card holds `[image / label / subtitle]`; box-shadows fall **outward** from the tilt (left shadow right, right shadow left).
- **Scene 3 (~1.9–end) — badges punctuate, then hold.** A pill `[badge]` lands at each card's **inner edge** (left then right, ~0.3s apart), overlapping its card ~15% so it reads as attached, not orbiting. This is the lone overshoot in the shot — it earns the punctuation. Settles and holds.

**motion vocabulary**: title slide-down from above; mirrored opposite-wing card entry; static book-open `rotateY` tilt (`+tilt` left, `−tilt` right); tilt-matched outward box-shadow; inner-edge badge spring-pop; gentle phase-opposed idle float (left vs right, never synchronized) registered as subtle jitter; dual side-glow ambient.

**rule mapping**

- two cards entering from opposite wings with mirrored `rotateY` tilts + tilt-matched shadow → `split-tilt-cards` (the signature; keep the two-layer split so the entry `x`/`scale` and the idle never collide on one alias)
- title slide-down settle → `gsap-effects` (translate + opacity on a long-tail `power3`)
- inner-edge pill badge pop (the one overshoot) → `spring-pop-entrance` (overshoot register — earns the punctuation)
- phase-opposed idle float on the pair → `sine-wave-loop` (low-amplitude register — subtle jitter, NOT lazy breathing; left `sin(t)`, right `sin(t+π)` so they never conveyor-belt)
- the two faint side glows behind the cards → `ambient-glow-bloom` (un-triggered soft bloom, one per accent)

**camera modifier**: camera-static by default — the symmetry is the subject and a move would break the balance.

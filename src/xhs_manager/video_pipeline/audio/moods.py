"""BGM 情绪体系 —— 脚本场景与曲库之间的公共词汇表。

设计取舍：情绪标签控制在 6 个。
太少无法区分段落，太多会让 LLM 分类不稳、也让 29 首的曲库每格分不到几首。
"""

from enum import Enum


class BgmMood(str, Enum):
    """场景情绪。名字直接对应短视频的叙事功能，便于 LLM 判断。"""

    HOOK = "hook"                # 开场钩子：制造悬念/冲击，紧张有推进感
    EXPLAIN = "explain"          # 平稳讲解：不抢戏，垫底
    TENSION = "tension"          # 问题/矛盾：压抑、有压力
    REVEAL = "reveal"            # 数据揭示/转折：明亮、有惊喜感
    UPLIFT = "uplift"            # 积极展望：昂扬、开阔
    CLOSING = "closing"          # 收尾总结：舒缓、收束


# 每个情绪期望的特征**百分位区间**（0=库内最低，1=库内最高）。
#
# 为什么用百分位而不是绝对值（tempo=120、brightness=2000 这种）：
# 绝对阈值必须假设曲库的风格分布，一旦换库就失效。
# 实测 MPT 自带曲库整体偏暗（brightness 仅 430-597Hz），
# 用通用阈值会让 29 首全部落进同一格，等于没分类。
# 百分位是相对排名，任何曲库都能自动摊开到 6 个情绪上。
MOOD_PROFILES: dict[BgmMood, dict[str, tuple[float, float]]] = {
    #                  tempo(快慢)     energy(强弱)    brightness(明暗)
    BgmMood.HOOK:    {"tempo": (0.60, 1.00), "energy": (0.50, 1.00), "brightness": (0.45, 1.00)},
    BgmMood.EXPLAIN: {"tempo": (0.20, 0.60), "energy": (0.00, 0.50), "brightness": (0.25, 0.75)},
    BgmMood.TENSION: {"tempo": (0.35, 0.80), "energy": (0.45, 1.00), "brightness": (0.00, 0.40)},
    BgmMood.REVEAL:  {"tempo": (0.50, 0.90), "energy": (0.40, 0.95), "brightness": (0.60, 1.00)},
    BgmMood.UPLIFT:  {"tempo": (0.65, 1.00), "energy": (0.55, 1.00), "brightness": (0.50, 1.00)},
    BgmMood.CLOSING: {"tempo": (0.00, 0.35), "energy": (0.00, 0.45), "brightness": (0.15, 0.65)},
}

# LLM 没给 mood 时，按场景在整片中的位置兜底
def default_mood_for_position(index: int, total: int) -> BgmMood:
    """按场景位置推断情绪，用于脚本缺 bgm_mood 时兜底。"""
    if total <= 1:
        return BgmMood.EXPLAIN
    ratio = index / (total - 1)
    if index == 0:
        return BgmMood.HOOK
    if ratio >= 0.85:
        return BgmMood.CLOSING
    if ratio >= 0.6:
        return BgmMood.UPLIFT
    return BgmMood.EXPLAIN

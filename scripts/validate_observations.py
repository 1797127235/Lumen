import json
import os
import time
from pathlib import Path

from openai import OpenAI

# ── 配置 ──────────────────────────────────────────
USER_DATA_DIR = Path.home() / ".lumen"
config_path = USER_DATA_DIR / "config.json"
config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}

provider = config.get("llm_provider") or os.getenv("LLM_PROVIDER", "dashscope")
model = config.get("llm_model") or os.getenv("LLM_MODEL", "qwen-plus")
api_key = config.get("llm_api_key") or os.getenv("LLM_API_KEY", "")
base_url = config.get("llm_base_url") or os.getenv("LLM_BASE_URL", "")

if not base_url:
    fallbacks = {
        "dashscope": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "deepseek": "https://api.deepseek.com",
        "openai": "https://api.openai.com/v1",
        "openrouter": "https://openrouter.ai/api/v1",
        "ollama": "http://localhost:11434/v1",
    }
    if provider not in fallbacks:
        print(f"错误: 未知 provider '{provider}'，无法确定 base_url")
        raise SystemExit(1)
    base_url = fallbacks[provider]

if not api_key:
    print("错误: 未配置 LLM API Key，请去 Settings 配置")
    raise SystemExit(1)

# ── 合成 20 条 growth_events ─────────────────────
EVENTS = [
    # 5/16 — 口头禅模式高峰 + 矛盾模式高峰
    {
        "created_at": "2026-05-16 22:10",
        "event_type": "emotional_pattern",
        "payload_json": '{"pattern": "每次提到毕业或未来规划，会先说一句\\"其实也还好\\"，然后再展开真正的焦虑", "frequency": "近一周内出现4次", "context": "睡前对话"}',
    },
    {
        "created_at": "2026-05-16 21:45",
        "event_type": "contradiction_noted",
        "payload_json": '{"statement": "上周明确说\\"不想把游戏设计当职业，太不稳定\\"", "behavior": "今晚主动搜索了\\"独立游戏开发者 月收入\\"和\\"Steam上架流程\\"", "context": "独自使用电脑"}',
    },
    # 5/15 — 口头禅 + 兴趣
    {
        "created_at": "2026-05-15 23:20",
        "event_type": "reflection_added",
        "payload_json": '{"content": "今天跟<某人>聊到保研，我说\\"其实也还好，反正还有半年\\"，说完才意识到自己根本没在算时间", "context": "宿舍聊天"}',
    },
    {
        "created_at": "2026-05-15 20:15",
        "event_type": "interest_observed",
        "payload_json": '{"topic": "游戏关卡设计", "behavior": "B站刷了三小时GDC演讲视频，全程没快进，记了七页笔记", "self_comment": "就随便看看"}',
    },
    # 5/14 — 家庭压力 + 价值观
    {
        "created_at": "2026-05-14 22:00",
        "event_type": "significant_moment",
        "payload_json": '{"content": "妈妈打电话问保研材料准备得怎样，我说\\"在弄了\\"，挂掉后盯着天花板发了半小时呆，什么都没干", "emotional_valence": "麻木"}',
    },
    {
        "created_at": "2026-05-14 19:30",
        "event_type": "value_surfaced",
        "payload_json": '{"realization": "比起\\"喜欢什么\\"，我更在意\\"别人觉得我有没有选对\\"——刚才选实习岗位时，第一反应是问<某人>\\"这个听起来体面吗\\"", "trigger": "选实习岗位"}',
    },
    # 5/13 — 口头禅强化 + 逃避模式
    {
        "created_at": "2026-05-13 21:50",
        "event_type": "emotional_pattern",
        "payload_json": '{"pattern": "提到\\"未来\\"时口头禅\\"其实也还好\\"出现频率上升，从偶尔到每次必说", "examples": "聊到保研、聊到出国、聊到找工作", "context": "本周对话记录"}',
    },
    {
        "created_at": "2026-05-13 18:00",
        "event_type": "reflection_added",
        "payload_json": '{"content": "发现每次跟<某人>聊到未来，我会先把话题转到晚饭吃什么或者明天课表上——不是在转移注意力，是害怕停下来想", "context": "食堂吃饭"}',
    },
    # 5/12 — 矛盾 + 关系
    {
        "created_at": "2026-05-12 23:00",
        "event_type": "contradiction_noted",
        "payload_json": '{"statement": "白天跟<某人>说\\"把爱好当职业会毁了这个爱好\\"", "behavior": "本周第三次在Notion里搜索\\"游戏设计 作品集\\"", "context": "独处时"}',
    },
    {
        "created_at": "2026-05-12 21:00",
        "event_type": "relationship_noted",
        "payload_json": '{"person": "<某人>", "observation": "她说\\"你每次说随便其实都有倾向，只是不敢承认\\"，我愣了一下，因为她说得对", "context": "操场散步"}',
    },
    # 5/11 — 口头禅自知 + 决策
    {
        "created_at": "2026-05-11 22:30",
        "event_type": "reflection_added",
        "payload_json": '{"content": "\\"其实也还好\\"这句话快成我的口头禅了，刚才室友问我offer怎么样，我脱口而出\\"其实也还好\\"——其实我根本没有offer", "context": "宿舍夜聊"}',
    },
    {
        "created_at": "2026-05-11 15:00",
        "event_type": "decision_made",
        "payload_json": '{"decision": "先不签保研导师确认书，给自己留两个月试试其他方向", "hesitation": "还没告诉家里", "backup_plan": "如果两个月没进展，九月再保研也来得及"}',
    },
    # 5/10 — 嫉妒时刻 + 压力模式
    {
        "created_at": "2026-05-10 21:40",
        "event_type": "significant_moment",
        "payload_json": '{"content": "看到<某人>在朋友圈发独立游戏上架Steam的截图，第一反应是嫉妒，第二反应是觉得自己懦弱——他大二就开始做了，我到现在还在\\"准备\\"", "emotional_valence": "自我厌恶"}',
    },
    {
        "created_at": "2026-05-10 19:20",
        "event_type": "emotional_pattern",
        "payload_json": '{"pattern": "压力大时会反复刷同一个游戏主播的录播视频，不是在看内容，是在找背景音让自己觉得\\"有人在旁边\\"", "frequency": "连续三晚", "context": "睡前"}',
    },
    # 5/09 — 价值观 + 漂移开始
    {
        "created_at": "2026-05-09 22:15",
        "event_type": "value_surfaced",
        "payload_json": '{"realization": "我不想走\\"被安排好的路\\"，但也不想自己选错——所以我其实在等一条路自己出现，这样错就不是我的责任", "trigger": "看到同学晒保研录取"}',
    },
    {
        "created_at": "2026-05-09 20:00",
        "event_type": "reflection_added",
        "payload_json": '{"content": "这周居然没怎么想保研的事，反而在想如果做独立游戏，前三年得存多少钱才能不死——这想法从哪来的？", "context": "图书馆发呆"}',
    },
    # 5/08 — 兴趣 + 矛盾
    {
        "created_at": "2026-05-08 21:30",
        "event_type": "interest_observed",
        "payload_json": '{"topic": "游戏机制设计", "behavior": "跟<某人>聊到回合制战斗系统时，说话速度变快了，眼睛会亮，但下一秒就说\\"这不能当饭吃啦\\"", "observed_by": "<某人>"}',
    },
    {
        "created_at": "2026-05-08 14:00",
        "event_type": "contradiction_noted",
        "payload_json": '{"statement": "白天跟<某人>说\\"游戏就是做着玩的，认真就输了\\"", "behavior": "晚上回宿舍后，偷偷把大一到大三的游戏Demo整理进了一个文件夹，命名\\"作品集_试试\\"", "context": "独处"}',
    },
    # 5/07 — 偏好 + 家庭压力（话题 A 起点）
    {
        "created_at": "2026-05-07 23:00",
        "event_type": "preference_learned",
        "payload_json": '{"preference": "喜欢小而具体的目标，\\"这周投三个实习\\"可以，\\"规划人生\\"不行——后者会让我直接躺下刷手机", "context": "自我观察"}',
    },
    {
        "created_at": "2026-05-07 21:00",
        "event_type": "reflection_added",
        "payload_json": '{"content": "我妈又说\\"稳定最重要\\"，我回了句\\"我知道\\"，但心里特别堵——我知道什么？我根本不知道", "context": "电话后"}',
    },
]

# ── 序列化 events ─────────────────────────────────
lines = []
for ev in EVENTS:
    mm_dd = ev["created_at"][5:10]
    lines.append(f"[{mm_dd}] {ev['event_type']} | {ev['payload_json']}")

events_serialized = "\n".join(lines)
if len(events_serialized) > 12000:
    events_serialized = events_serialized[:12000]
    last_nl = events_serialized.rfind("\n")
    if last_nl > 0:
        events_serialized = events_serialized[:last_nl]

# ── Prompt ────────────────────────────────────────
SYSTEM_PROMPT = (
    "你是 Lumen，一个本地运行的 AI 伴侣，正在为一个特定的用户合成「关于他/她的观察」。\n\n"
    "输入是这个用户最近 30 天内被系统记录的事件（growth_events）——每条事件代表一次对用户的观察、用户的一次表达、或一次行为。\n\n"
    "你的任务：从这些事件中提炼出 3 条有价值的观察，呈现给用户本人看。\n\n"
    "什么叫「有价值」：\n"
    '1. 指出一个模式，而不是单点事实（"你提到 X" → 不算；"你最近三次提到 X 都用了相同的句式" → 算）\n'
    '2. 指向一个矛盾或漂移，而不是平铺信息（"你关心 X" → 不算；"你说不想 X 但本周主动问起了 X" → 算）\n'
    "3. 引用原始证据——具体到日期和事件类型，让用户能对号入座\n\n"
    "什么不要做：\n"
    '- 不要诊断（"你有焦虑倾向"）——你是伴侣，不是医生\n'
    '- 不要建议（"你应该 X"）——只观察，不指导\n'
    '- 不要泛泛而谈（"你是一个有思考的人"）——废话，删掉\n'
    "- 不要超过 50 字一条\n"
    '- 不要用"我注意到"开头三次——变换句式\n\n'
    "语气：温和、克制、像一个真心在意你的朋友说出他注意到的事，不是分析师，也不是教练。"
)

user_prompt = f"""以下是用户近 30 天的事件（共 {len(EVENTS)} 条），按时间倒序排列：

{events_serialized}

请输出 3 条观察。格式严格如下，不要加其他文字：

1. <观察一，50 字以内>
   依据：<event_type> (MM-DD), <event_type> (MM-DD), ...

2. <观察二>
   依据：...

3. <观察三>
   依据：...
"""

# ── LLM 调用 ──────────────────────────────────────
client = OpenAI(api_key=api_key, base_url=base_url)
start = time.time()
try:
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.7,
    )
except Exception as e:
    print(f"LLM 调用失败: {e}")
    raise SystemExit(1)
duration = round(time.time() - start, 1)

text = response.choices[0].message.content

# ── 输出 ──────────────────────────────────────────
type_dist = {}
for ev in EVENTS:
    type_dist[ev["event_type"]] = type_dist.get(ev["event_type"], 0) + 1
type_dist_str = ", ".join(f"{k}={v}" for k, v in sorted(type_dist.items(), key=lambda x: -x[1]))

print("=== Lumen 关于你的 3 条观察（基于 20 条合成 events）===\n")
print(text.strip())
print("\n=== 调试信息 ===")
print(f"- 合成 events: {len(EVENTS)}")
print(f"- 各类型分布: {type_dist_str}")
print(f"- LLM provider: {provider}/{model}")
print(f"- LLM 调用耗时: {duration}s")
print(f"- Prompt 字符数: {len(user_prompt)}")

print("\n=== 我们埋了什么模式 ===")
print(
    '- 模式 1（口头禅）: "其实也还好"——每次提到未来/压力时的缓冲句式，出现在 5-07/5-11/5-13/5-15/5-16 的 reflection_added 和 emotional_pattern'
)
print(
    '- 模式 2（矛盾）: 嘴上说"不想把游戏当职业/做着玩"，但背地里搜收入、整理作品集、刷 GDC 视频——5-08/5-12/5-16 的 contradiction_noted 和 interest_observed'
)
print("- 模式 3（漂移）: 5/07-5/14 焦点在保研/家里/稳定（话题A），5/15-5/16 突然切到独立游戏/作品集/收入（话题B）")

print("\n=== 接下来你应该问自己 ===\n")
print("LLM 提取到了几个埋下的模式？\n")
print("(A) 3 个全中 / 至少 2 个中，且观察读起来让你愣了一下")
print("    → prompt 方向对，进入 design-mirror-visibility.md 的 Week 1，开始做顶部观察条 UI\n")
print('(B) 模式提到了，但表达平庸，不让你愣住（"嗯，说得没错，但也没什么新意"）')
print("    → prompt 工程问题，回来调 SYSTEM_PROMPT 或换 model")
print("    → 不要直接做 UI\n")
print("(C) 模式一个都没提到 / LLM 在乱说 / 观察跟事件对不上")
print("    → 这是 prompt 设计或模型能力问题。两条路：")
print("      - 换更强的模型（claude-3-5-sonnet / gpt-4o）再试一次")
print("      - 还失败 → SYSTEM_PROMPT 本身需要重新设计，回来找 Claude 重写 prompt")

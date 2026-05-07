"""Cognee dataset 命名常量 — 新内容类型加入时在此注册。"""

# 用户本人档案（简历、项目描述、经历）
DATASET_PROFILE = "lumen_profile"

# 外部参考（学长帖、公司调研、行业报告）
DATASET_REFERENCE = "lumen_reference"

# 反思与情绪（日记、复盘、随手想法）
DATASET_REFLECTION = "lumen_reflection"

# 对话摘要
DATASET_CHAT = "lumen_chat"

# 所有 dataset 列表，供 cognify loop 遍历
ALL_DATASETS = [
    DATASET_PROFILE,
    DATASET_REFERENCE,
    DATASET_REFLECTION,
    DATASET_CHAT,
]

# Agent scope 参数 → Cognee datasets 映射
# scope=None 时使用 ALL_DATASETS
SCOPE_DATASETS: dict[str, list[str]] = {
    "profile": [DATASET_PROFILE],
    "emotions": [DATASET_REFLECTION],
    "reference": [DATASET_REFERENCE],
    "chat": [DATASET_CHAT],
    "all": ALL_DATASETS,
}

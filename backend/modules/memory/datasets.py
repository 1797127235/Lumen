"""Cognee dataset 命名常量。"""

DATASET_PROFILE = "lumen_profile"
DATASET_REFERENCE = "lumen_reference"
DATASET_REFLECTION = "lumen_reflection"
DATASET_CHAT = "lumen_chat"
DATASET_KNOWLEDGE = "lumen_knowledge"

ALL_DATASETS = [
    DATASET_PROFILE,
    DATASET_REFERENCE,
    DATASET_REFLECTION,
    DATASET_CHAT,
    DATASET_KNOWLEDGE,
]

SCOPE_DATASETS: dict[str, list[str]] = {
    "profile": [DATASET_PROFILE],
    "emotions": [DATASET_REFLECTION],
    "reference": [DATASET_REFERENCE],
    "chat": [DATASET_CHAT],
    "knowledge": [DATASET_KNOWLEDGE],
    "all": ALL_DATASETS,
}

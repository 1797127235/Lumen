"""日期修复 — 从原始 Markdown 恢复 LLM 丢失的月份信息"""

import logging
import re

logger = logging.getLogger(__name__)

# 匹配日期范围: "Jan 2020 - Dec 2023", "May 2021 - Present", "2020.09 - 2021.06"
_DATE_RE = re.compile(
    r"(?:"
    # 英文月份格式: Jan 2020, January 2020, Jan. 2020
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?"
    r"|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?"
    r"|Dec(?:ember)?)\.?\s+\d{4}"
    r"|"
    # 数字格式: 2020.09, 2020-09, 2020/09
    r"\d{4}[\.\-/]\d{1,2}"
    r")"
    r"(?:\s*[-–—]\s*"
    r"(?:"
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?"
    r"|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?"
    r"|Dec(?:ember)?)\.?\s+\d{4}"
    r"|"
    r"\d{4}[\.\-/]\d{1,2}"
    r"|"
    r"Present|Current|Now|Ongoing|至今"
    r"))?",
    re.IGNORECASE,
)

_YEAR_ONLY_RE = re.compile(r"\d{4}")
_MONTH_RE = re.compile(
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec|" r"\d{4}[\.\-/]\d{1,2})",
    re.IGNORECASE,
)


def restore_dates(parsed_data: dict, markdown: str) -> dict:
    """修复 parsed_data 中 LLM 丢失的月份信息。

    思路：从原始 Markdown 提取完整日期，构建 "年份-only → 完整日期" 映射，
    然后修复 parsed_data.workExperience / education / personalProjects 中的 years 字段。
    """
    # 从 Markdown 提取完整日期
    md_dates = _DATE_RE.findall(markdown)
    if not md_dates:
        return parsed_data

    # 构建映射: "2020 - 2021" → "Jun 2020 - Aug 2021"
    year_to_full: dict[str, str] = {}
    for md_date in md_dates:
        years = _YEAR_ONLY_RE.findall(md_date)
        if not years:
            continue
        year_key = " - ".join(years)
        if year_key not in year_to_full:
            year_to_full[year_key] = md_date

    if not year_to_full:
        return parsed_data

    # 修复关键 section
    patched = 0
    for section in ("workExperience", "education", "personalProjects"):
        for entry in parsed_data.get(section, []):
            if not isinstance(entry, dict):
                continue
            years = entry.get("years", "")
            if not isinstance(years, str) or not years:
                continue
            # 跳过已有月份/具体日期的
            if _MONTH_RE.search(years):
                continue
            # 尝试修复
            if years in year_to_full:
                entry["years"] = year_to_full[years]
                patched += 1

    if patched:
        logger.info("Restored months in %d date field(s)", patched)

    return parsed_data

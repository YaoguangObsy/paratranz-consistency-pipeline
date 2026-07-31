#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
导出 ParaTranz 项目术语表到本地 CSV 文件。

用法一：
    1. pip install requests
    2. python scripts/1_fetch_paratranz_terms.py

用法二：对一份已有的完整 CSV 做裁剪，
        只保留 FIELDS 里指定的列，不需要重新调用 API
    python scripts/1_fetch_paratranz_terms.py --trim 完整文件.csv 输出文件.csv
"""

import ast
import csv
import sys
import requests
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import PARATRANZ_TOKEN, PROJECT_ID
# =================================
OUTPUT_FILE = "paratranz_terms.csv"

# 只导出这几个字段（顺序即 CSV 列的顺序）
FIELDS = ["pos", "uid", "term", "translation", "note", "variants"]
# =================================

API_BASE = "https://paratranz.cn/api"


def fetch_terms(project_id: int, token: str) -> list[dict]:
    """调用 ParaTranz API 获取项目全部术语（自动翻页）"""
    url = f"{API_BASE}/projects/{project_id}/terms"
    headers = {"Authorization": token}

    all_terms: list[dict] = []
    page = 1
    page_size = 100  # 每页拉取数量，可按需调大

    while True:
        resp = requests.get(
            url,
            headers=headers,
            params={"page": page, "pageSize": page_size},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        # 兼容两种可能的返回格式：
        # 1) {"results": [...], "page": 1, "pageCount": 9, "total": 823}
        # 2) 直接是列表 [...]
        if isinstance(data, dict) and "results" in data:
            batch = data["results"]
            all_terms.extend(batch)

            page_count = data.get("pageCount")
            if page_count is not None:
                print(f"已获取第 {page}/{page_count} 页，累计 {len(all_terms)} 条")
                if page >= page_count:
                    break
            else:
                # 没有 pageCount 字段时，靠“这页是否还有数据”判断
                print(f"已获取第 {page} 页，累计 {len(all_terms)} 条")
                if not batch or len(batch) < page_size:
                    break

            page += 1

        elif isinstance(data, list):
            # 直接返回列表，说明接口本身不分页，直接拿到全部数据
            all_terms.extend(data)
            break

        else:
            raise ValueError(f"未识别的返回格式: {data!r}")

    return all_terms


def stringify(value) -> str:
    """把 list/dict 之类的字段值转成适合放进 CSV 单元格的字符串"""
    # 如果是字符串，且看起来像 Python 的 list 字面量（比如从已有 CSV 里读出来的
    # "['Andromeda Viability Points', 'AVP']" 或 "[]"），先尝试还原成真正的 list
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            try:
                value = ast.literal_eval(stripped)
            except (ValueError, SyntaxError):
                pass  # 解析失败就当普通字符串处理

    if value is None or value == "":
        return ""
    if isinstance(value, list):
        if not value:
            return ""
        # variants 常见形式：["译法A", "译法B"] 或 [{"term": "..."}] 之类
        parts = []
        for item in value:
            if isinstance(item, dict):
                # 常见 key 优先取 term/translation/value，否则整体转成字符串
                parts.append(
                    item.get("term")
                    or item.get("translation")
                    or item.get("value")
                    or str(item)
                )
            else:
                parts.append(str(item))
        return " | ".join(parts)
    if isinstance(value, dict):
        return str(value)
    return str(value)


def save_to_csv(terms: list[dict], filepath: str) -> None:
    """将术语列表保存为 CSV 文件，只保留 FIELDS 中指定的字段"""
    if not terms:
        print("术语列表为空，未生成文件。")
        return

    with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for term in terms:
            row = {field: stringify(term.get(field)) for field in FIELDS}
            writer.writerow(row)

    print(f"已导出 {len(terms)} 条术语到 {filepath}")


def trim_csv(input_path: str, output_path: str) -> None:
    """读取一份已有的完整 CSV（字段更多），裁剪成只含 FIELDS 的精简 CSV"""
    with open(input_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        missing = [field for field in FIELDS if field not in (reader.fieldnames or [])]
        if missing:
            sys.exit(f"输入文件里缺少这些列，无法裁剪: {missing}\n实际列名: {reader.fieldnames}")

        rows = list(reader)

    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: stringify(row.get(field)) for field in FIELDS})

    print(f"已从 {input_path} 裁剪 {len(rows)} 条术语，输出到 {output_path}")


def main() -> None:
    # 用法二：python export_paratranz_terms.py --trim 输入.csv 输出.csv
    if len(sys.argv) == 4 and sys.argv[1] == "--trim":
        trim_csv(sys.argv[2], sys.argv[3])
        return

    # 用法一：直接从 API 拉取
    if not PARATRANZ_TOKEN :
        sys.exit("请先配置 config")
    if not PROJECT_ID:
        sys.exit("请先配置 config")

    try:
        terms = fetch_terms(PROJECT_ID, PARATRANZ_TOKEN)
    except requests.HTTPError as e:
        sys.exit(f"请求失败: {e}\n响应内容: {e.response.text}")

    save_to_csv(terms, OUTPUT_FILE)


if __name__ == "__main__":
    main()
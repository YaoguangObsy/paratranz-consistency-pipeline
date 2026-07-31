#!/usr/bin/env python3
"""
把 check_consistency.py 产出的两份报告合并成一张统一的"审核决策表"。
这张表是人工审核 / AI 辅助 / 纯人工三种方案共用的唯一接口。

用法:
    python ./scripts/3_build_review_sheet.py `
        --duplicate ./reports/inconsistency_duplicate_source.csv `
        --glossary ./reports/inconsistency_glossary.csv `
        --out review_decisions.csv

同一个 key 如果在两份报告里都出现，只保留 term_count/group_size 更大的那条
（更高优先级），避免重复审核同一行。

若 --out 指向的文件已存在（也就是重新生成/刷新），会先读取旧文件，把已有的
decision / final_translation / reviewer_note / excluded 按 key 带过来，
不会因为重新跑这个脚本就把之前人工填的东西或标记的"排除"清空。

final_translation 预填充：继承完旧值之后，如果这一列仍然是空的（不管
decision 是什么），会自动填成 current_translation，方便你在页面上直接改
个别字，而不用先手动复制一遍原译文。已有内容（不管是你自己填的还是从旧表
继承来的）不会被覆盖。
"""
import argparse
import csv
from pathlib import Path


def load_duplicate(path):
    rows = []
    with open(path, encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            rows.append({
                'key': r['key'],
                'row_id': f"{r['key']}::duplicate_source::group_size={r['group_size']}",
                'source_report': 'duplicate_source',
                'group_or_term': f"group_size={r['group_size']}",
                'source_text': r['source_text'],
                'current_translation': r['current_translation'],
                'suggested_translation': r['majority_translation'],
                'confidence': f"n/a(count={r['majority_count']})",
                'priority': int(r['group_size']),
            })
    return rows


def load_glossary(path):
    rows = []
    with open(path, encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            rows.append({
                'key': r['key'],
                'row_id': f"{r['key']}::glossary::{r['term']}",
                'source_report': 'glossary',
                'group_or_term': r['term'],
                'source_text': r['source_text'],
                'current_translation': r['current_translation'],
                'suggested_translation': r['expected_translation'],
                'confidence': r['confidence'],
                'priority': int(r['term_count']),
            })
    return rows


def load_previous_decisions(out_path):
    p = Path(out_path)
    if not p.exists():
        return {}
    prev = {}
    with open(p, encoding='utf-8-sig', newline='') as f:
        for r in csv.DictReader(f):
            # 兼容旧文件（没有 row_id 列）：退化成用 key 当 row_id，
            # 保证从旧版升级时至少 duplicate_source 那类（key 本来就唯一）能对上号
            rid = r.get('row_id') or r['key']
            prev[rid] = {
                'decision': r.get('decision', ''),
                'final_translation': r.get('final_translation', ''),
                'reviewer_note': r.get('reviewer_note', ''),
                'excluded': r.get('excluded', ''),
                'source_text': r.get('source_text', ''),
                'current_translation': r.get('current_translation', ''),
            }
    return prev


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--duplicate', required=True)
    ap.add_argument('--glossary', required=True)
    ap.add_argument('--out', default='review_decisions.csv')
    args = ap.parse_args()

    rows = load_duplicate(args.duplicate) + load_glossary(args.glossary)

    # 之前是按 key 去重（一个 key 只留优先级最高的一条），这正是"同一个 key
    # 的第二个问题会被吞掉"的根源。现在改成按 row_id 去重——row_id 已经把
    # key+report+term/group 都编码进去了，只有真正完全重复的同一条问题才会
    # 被合并，同一 key 的不同问题（不同 term，或 duplicate_source 与
    # glossary 同时命中）都会各自保留一行。
    best = {}
    for r in rows:
        rid = r['row_id']
        if rid not in best or r['priority'] > best[rid]['priority']:
            best[rid] = r

    out_rows = sorted(best.values(), key=lambda r: (r['key'], -r['priority']))

    prev = load_previous_decisions(args.out)
    carried = 0
    prefilled = 0
    resynced = 0
    stale_reset = 0

    fieldnames = ['row_id', 'key', 'source_report', 'group_or_term', 'source_text',
                  'current_translation', 'suggested_translation', 'confidence',
                  'decision', 'final_translation', 'reviewer_note', 'excluded']
    with open(args.out, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in out_rows:
            old = prev.get(r['row_id'])
            if old:
                carried += 1

            decision = old['decision'] if old else ''
            reviewer_note = old['reviewer_note'] if old else ''
            carried_final = old['final_translation'] if old else ''

            text_changed = bool(old) and (
                old.get('source_text', '') != r['source_text']
                or old.get('current_translation', '') != r['current_translation']
            )
            was_untouched_prefill = bool(old) and carried_final == old.get('current_translation', '')

            if not carried_final:
                final_translation = r['current_translation']
                prefilled += 1
            elif text_changed and was_untouched_prefill:
                final_translation = r['current_translation']
                prefilled += 1
                resynced += 1
            else:
                final_translation = carried_final

            if text_changed and decision in ('accept', 'reject'):
                stale_reset += 1
                note_flag = '[⚠️原文/现译文已变化，请重新核对，原结论已重置]'
                reviewer_note = (note_flag + ' ' + reviewer_note).strip() if reviewer_note else note_flag
                decision = ''

            w.writerow({
                'row_id': r['row_id'],
                'key': r['key'],
                'source_report': r['source_report'],
                'group_or_term': r['group_or_term'],
                'source_text': r['source_text'],
                'current_translation': r['current_translation'],
                'suggested_translation': r['suggested_translation'],
                'confidence': r['confidence'],
                'decision': decision,
                'final_translation': final_translation,
                'reviewer_note': reviewer_note,
                'excluded': old['excluded'] if old else '',
            })
    print(f'Merged {len(rows)} -> {len(out_rows)} rows (row_id-level; keys may repeat). Wrote {args.out}')
    if prev:
        print(f'检测到已有 {args.out}，其中 {carried}/{len(out_rows)} 行按 row_id 带过来了旧的 decision/排除标记。')
    print(f'final_translation 预填充: {prefilled}/{len(out_rows)} 行（其中 {resynced} 行是文本变化后重刷新的）。')
    if stale_reset:
        print(f'⚠️ {stale_reset} 行因文本变化被重置为未审核。')
    print('接下来: 打开 webapp 决策工作台继续审核，或跑 ai_assist_fill.py 让 AI 先填一遍。')


if __name__ == '__main__':
    main()
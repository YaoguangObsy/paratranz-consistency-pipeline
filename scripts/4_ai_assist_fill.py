#!/usr/bin/env python3
"""
AI 辅助方案：对 review_decisions.csv 里 decision 仍为空的行，调用 Claude API
给出建议，自动填 decision / final_translation / ai_confidence / reviewer_note。

只是"先填一遍"——ai_confidence=low 或 decision=reject 的行仍需要你人工过一遍，
脚本本身不会替你做最终判断。

用法:
    export ANTHROPIC_API_KEY=xxxxx
    python3 ai_assist_fill.py --in review_decisions.csv --out review_decisions_ai.csv
"""
import argparse
import csv
import json
import os
import time
import anthropic

PROMPT_TMPL = """你在做游戏本地化的翻译一致性审核。请判断下面这一条该怎么处理。

来源报告类型: {source_report}
涉及的词条/分组: {group_or_term}
原文: {source_text}
当前译文: {current_translation}
系统建议译文: {suggested_translation}

请判断：
1. "accept" - 采纳系统建议译文（当前译文确实有问题，建议译文更好/更统一）
2. "reject" - 不改（当前译文其实没问题，是误报，比如语境特殊）
3. "edit" - 都不对，你给出一个更好的译文

只输出一个 JSON 对象，不要有其他文字：
{{"decision": "accept|reject|edit", "final_translation": "...(仅edit时填，否则留空)", "confidence": "high|low", "reason": "一句话说明理由"}}
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--in', dest='inp', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--model', default='claude-sonnet-4-6')
    ap.add_argument('--limit', type=int, default=None, help='只处理前N行，先小批量试跑')
    args = ap.parse_args()

    client = anthropic.Anthropic()  # 读取 ANTHROPIC_API_KEY 环境变量

    with open(args.inp, encoding='utf-8-sig') as f:
        rows = list(csv.DictReader(f))

    todo = [r for r in rows if not r['decision'].strip()]
    if args.limit:
        todo = todo[:args.limit]
    print(f'{len(todo)} 行待 AI 处理（共 {len(rows)} 行）')

    for i, r in enumerate(todo):
        prompt = PROMPT_TMPL.format(**r)
        try:
            resp = client.messages.create(
                model=args.model,
                max_tokens=300,
                messages=[{"role": "user", "content": prompt}],
            )
            text = resp.content[0].text.strip()
            text = text.strip('`').lstrip('json').strip()
            data = json.loads(text)
            r['decision'] = data.get('decision', '')
            r['final_translation'] = data.get('final_translation', '')
            r['reviewer_note'] = f"[AI:{data.get('confidence','?')}] {data.get('reason','')}"
        except Exception as e:
            r['reviewer_note'] = f"[AI ERROR] {e}"
        if (i + 1) % 20 == 0:
            print(f'  {i+1}/{len(todo)}')
        time.sleep(0.2)  # 避免打满速率限制

    with open(args.out, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    low_conf = sum(1 for r in rows if 'AI:low' in r.get('reviewer_note', ''))
    print(f'完成。写入 {args.out}。其中 {low_conf} 行 AI 标注低置信度，建议优先人工抽查这些。')


if __name__ == '__main__':
    main()

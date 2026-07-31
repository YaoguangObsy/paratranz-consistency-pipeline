#!/usr/bin/env python3
"""
读 review_decisions.csv，把 decision 为 accept/edit 的行推送到 Paratranz。

Paratranz 的翻译更新接口按内部数字 id（不是 key）定位字符串，所以要先拉一遍
全量 key -> id 映射，再逐条 PUT。

默认 --dry-run：只打印将要执行的改动和统计，不真的调用写接口。
确认无误后加 --no-dry-run 才会真正提交。

用法:
     python scripts/5_push_to_paratranz.py --decisions review_decisions.csv
      python scripts/5_push_to_paratranz.py --decisions review_decisions_ai.csv
    # 确认没问题后：
     python scripts/5_push_to_paratranz.py --decisions review_decisions.csv --no-dry-run
    python scripts/5_push_to_paratranz.py --decisions review_decisions_ai.csv --no-dry-run
"""
import argparse
import csv
import json
import os
import sys
import time
import unicodedata
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import PARATRANZ_TOKEN, PROJECT_ID

API_BASE = 'https://paratranz.cn/api'


def api_get(url, token):
    req = urllib.request.Request(url, headers={'Authorization': token})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode('utf-8'))


def normalize_key(k):
    # strip首尾空白/不可见字符，并做unicode正规化，避免因编码差异导致key对不上
    return unicodedata.normalize('NFC', k.strip())


def find_id_by_key(project_id, key, token):
    url = f'{API_BASE}/projects/{project_id}/strings?key={urllib.parse.quote(key)}'
    data = api_get(url, token)
    for item in data.get('results', []):
        if normalize_key(item['key']) == normalize_key(key):
            return item['id']
    return None


def load_patch_rows(decisions_path):
    rows = []
    skipped_excluded = 0
    with open(decisions_path, encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            if (r.get('excluded') or '').strip():
                decision = r['decision'].strip().lower()
                if decision in ('accept', 'edit'):
                    skipped_excluded += 1
                continue
            decision = r['decision'].strip().lower()
            if decision == 'accept':
                new_t = r['suggested_translation']
            elif decision == 'edit':
                new_t = r['final_translation']
            else:
                continue  # reject 或空，跳过
            if not new_t:
                print(f"  警告: key={r['key']} decision={decision} 但没有可用译文，跳过", file=sys.stderr)
                continue
            rows.append({'key': r['key'], 'new_translation': new_t,
                         'old_translation': r['current_translation']})
    return rows, skipped_excluded


def patch_one(project_id, string_id, new_translation, token):
    url = f'{API_BASE}/projects/{project_id}/strings/{string_id}'
    body = json.dumps({'translation': new_translation}).encode('utf-8')
    req = urllib.request.Request(url, data=body, method='PUT',
                                  headers={'Authorization': token,
                                           'Content-Type': 'application/json'})
    with urllib.request.urlopen(req) as resp:
        return resp.status


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--project-id', type=int, default=PROJECT_ID, help='Paratranz Project ID')
    ap.add_argument('--decisions', required=True)
    ap.add_argument('--token', default=None)
    ap.add_argument('--no-dry-run', action='store_true', help='不加此项则只演练不提交')
    ap.add_argument('--log', default='push_receipt.csv')
    args = ap.parse_args()

    token = PARATRANZ_TOKEN #args.token or os.environ.get('PARATRANZ_TOKEN')
    if not token:
        sys.exit('需要 Paratranz API token：--token 或环境变量 PARATRANZ_TOKEN')

    patch_rows, skipped_excluded = load_patch_rows(args.decisions)
    print(f'待推送 {len(patch_rows)} 条 (decision=accept/edit)')
    if skipped_excluded:
        print(f'（另有 {skipped_excluded} 条因 excluded=1 被跳过，未推送）')
    if not patch_rows:
        return

    dry_run = not args.no_dry_run
    if dry_run:
        print('=== DRY RUN，不会真正提交，加 --no-dry-run 才会执行 ===')

    log_rows = []
    for i, row in enumerate(patch_rows):
        key = row['key']
        sid = find_id_by_key(args.project_id, key, token)
        time.sleep(0.3)  # 每次查询都要限速，别省
        if sid is None:
            log_rows.append({**row, 'status': 'KEY_NOT_FOUND', 'http_code': ''})
            continue
        if dry_run:
            log_rows.append({**row, 'status': 'DRY_RUN', 'http_code': ''})
        else:
            try:
                code = patch_one(args.project_id, sid, row['new_translation'], token)
                log_rows.append({**row, 'status': 'OK', 'http_code': code})
            except urllib.error.HTTPError as e:
                log_rows.append({**row, 'status': f'HTTP_ERROR', 'http_code': e.code})
            time.sleep(0.4)  # 保持在速率限制内
        if (i + 1) % 50 == 0:
            print(f'  {i+1}/{len(patch_rows)}')

    with open(args.log, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['key', 'old_translation', 'new_translation', 'status', 'http_code'])
        w.writeheader()
        w.writerows(log_rows)

    not_found = sum(1 for r in log_rows if r['status'] == 'KEY_NOT_FOUND')
    print(f'完成。日志写入 {args.log}。其中 {not_found} 条在项目里没找到对应 key（可能已被删除/改名）。')


if __name__ == '__main__':
    main()
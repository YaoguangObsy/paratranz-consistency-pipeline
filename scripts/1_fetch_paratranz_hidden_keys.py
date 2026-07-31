 #!/usr/bin/env python3
"""
从 Paratranz 项目获取已隐藏的词条 key。

Usage:
    python scripts/1_fetch_paratranz_hidden_keys.py --stage -1 --out hidden_keys.txt

Stage values: 0=未翻译 1=已翻译 2=有疑问 3=已检查 5=已审核 9=已锁定 -1=已隐藏
"""
import argparse
import sys
import time
import urllib.request
import urllib.error
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import PARATRANZ_TOKEN, PROJECT_ID

API_BASE = 'https://paratranz.cn/api'


def fetch_page(project_id, stage, page, page_size, token):
    url = f'{API_BASE}/projects/{project_id}/strings?stage={stage}&page={page}&page_size={page_size}'
    req = urllib.request.Request(url, headers={'Authorization': token})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode('utf-8'))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--project-id', type=int, default=None, help=f'overrides hardcoded PROJECT_ID ({PROJECT_ID})')
    ap.add_argument('--stage', default=-1, type=int, help='-1=hidden (default), 0/1/2/3/5/9 for other stages')
    ap.add_argument('--page-size', default=100, type=int)
    ap.add_argument('--out', default='excluded_keys.txt')
    ap.add_argument('--token', default=None, help='overrides hardcoded TOKEN')
    args = ap.parse_args()

    token = args.token or PARATRANZ_TOKEN
    project_id = args.project_id if args.project_id is not None else PROJECT_ID

    if not token:
        sys.exit("请先配置 config 或 --token")
    if not project_id:
        sys.exit("请先配置 config 或 --project-id")

    keys = []
    page = 1
    while True:
        data = fetch_page(project_id, args.stage, page, args.page_size, token)
        results = data.get('results', [])
        if not results:
            break
        keys.extend(item['key'] for item in results)
        row_count = data.get('rowCount', data.get('row_count', 0))
        page_count = data.get('pageCount', data.get('page_count', 0))
        print(f'page {page}/{page_count}: +{len(results)} (running total {len(keys)}/{row_count})')
        if page >= page_count or not results:
            break
        page += 1
        time.sleep(0.3)  # stay well under the 120 req/min rate limit

    with open(args.out, 'w', encoding='utf-8') as f:
        for k in keys:
            f.write(k + '\n')
    print(f'Wrote {len(keys)} keys to {args.out}')


if __name__ == '__main__':
    main()



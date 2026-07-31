#!/usr/bin/env python3
"""
调用 Paratranz 的"导出"接口，把整个项目的文本文件打包下载，解压后按 key
合并成一份 data/Merged.csv（key,text 两列，无表头，utf-8-sig），供
2_check_consistency.py 的 --translated 参数使用。

对应 Paratranz Artifacts API（见 https://paratranz.cn/api-docs）：
    GET  /projects/{id}/artifacts/download    直接下载最近一次打包好的 zip
    GET  /projects/{id}/artifacts             查询最近一次打包的信息（不是任务状态！
                                               返回的是 total/translated/hidden/size/
                                               createdAt 等统计字段，没有 status）
    POST /projects/{id}/artifacts             手动触发重新打包——仅项目管理员可用，
                                               普通成员会收到 403

Paratranz 后台每小时会自动重新打包一次，所以正常情况下不需要（也没权限）手动
触发，直接下载最新的自动打包结果就行；脚本默认就是这么做的，只是顺便把这份包
的 createdAt/total/translated 等信息打印出来，方便你自己判断新不新鲜。
如果你本来就是项目管理员、想强制刷新成最新状态再下载，加 --trigger：
脚本会先记下触发前的 createdAt 作为基准，POST 触发后轮询 GET /artifacts，
靠 createdAt 是否变成更新的时间来判断新包是否打包完成（这个接口没有明确的
"进行中/已完成"状态字段，只能用时间戳变化间接判断）。

合并规则沿用旧逻辑
项目的 key 就是普通字符串，不做这个变换，只做首尾空白清理：
    - 每个 csv 文件里，每一行第一列是 key，最后一列是文本
        2 列   -> 未汉化，第二列(=最后一列)是原文
        3+ 列  -> 已汉化（或原文本身含未转义逗号），最后一列是译文
    - 同一个 key 在多个文件/多行出现：优先保留"已汉化"的那条；
      若汉化状态相同，按文件名排序后处理，后处理的覆盖前面的
    - 用 Python csv 模块解析（自动正确处理引号内的逗号/换行），
      不需要像 C# 那样手写状态机

用法:
    # 直接下载最新的自动打包结果并合并（推荐，普通成员就能用）
    python scripts/1_fetch_paratranz_artifacts.py --out data/Merged.csv
    # 项目管理员：先强制触发一次新打包，等它完成后再下载合并
    python scripts/1_fetch_paratranz_artifacts.py --out data/Merged.csv --trigger
    # 只想看合并结果、不想联网（比如已经手动下载过 zip）：
    python scripts/1_fetch_paratranz_artifacts.py --from-zip 下载好的.zip --out data/Merged.csv
"""
import argparse
import csv
import io
import json
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import PARATRANZ_TOKEN, PROJECT_ID

API_BASE = 'https://paratranz.cn/api'


def api_request(method, url, token, timeout=60):
    req = urllib.request.Request(url, method=method, headers={'Authorization': token})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def get_artifact_info(project_id, token):
    """GET /projects/{id}/artifacts —— 注意这个不是任务状态查询接口，返回的是
    最近一次打包结果的统计信息，形如：
        {'id': ..., 'createdAt': '2026-07-30T09:36:30.094Z', 'project': ...,
         'total': 104054, 'translated': 82292, 'disputed': 15, 'checked': 36534,
         'reviewed': 3200, 'hidden': 21762, 'size': 5268376, 'duration': 2359}
    """
    url = f'{API_BASE}/projects/{project_id}/artifacts'
    data = api_request('GET', url, token)
    return json.loads(data.decode('utf-8'))


def print_artifact_info(info):
    created = info.get('createdAt', '?')
    total = info.get('total')
    translated = info.get('translated')
    print(f'  最近一次打包时间: {created}'
          + (f'（共 {total} 条，已译 {translated} 条）' if total is not None else ''))


def trigger_export(project_id, token):
    """POST /projects/{id}/artifacts —— 手动触发重新打包，仅项目管理员可用，
    普通成员调用会拿到 403。"""
    url = f'{API_BASE}/projects/{project_id}/artifacts'
    api_request('POST', url, token)


# def wait_for_fresh_export(project_id, token, baseline_created_at, timeout=300, interval=5):
#     """这个接口没有明确的任务状态字段，只能靠 createdAt 是否变成比触发前更新的
#     时间戳来间接判断打包是否完成。"""
#     start = time.time()
#     while time.time() - start < timeout:
#         info = get_artifact_info(project_id, token)
#         created = info.get('createdAt')
#         if created and created != baseline_created_at:
#             print(f'  检测到新的打包结果: {created}')
#             return info
#         time.sleep(interval)
#     raise TimeoutError(f'等待新打包超过 {timeout} 秒仍未看到 createdAt 更新，'
#                         f'可能打包时间比较长，可以稍后用不带 --trigger 的方式直接下载')


def download_artifact_zip(project_id, token, out_zip_path):
    url = f'{API_BASE}/projects/{project_id}/artifacts/download'
    data = api_request('GET', url, token, timeout=120)
    Path(out_zip_path).write_bytes(data)


class _Entry:
    __slots__ = ('text', 'has_translation')

    def __init__(self, text, has_translation):
        self.text = text
        self.has_translation = has_translation


def merge_zip(zip_path):
    """解压 zip，递归找所有 .csv 并按文件名排序合并。返回 (merged_dict, file_count)。"""
    entries = {}
    with zipfile.ZipFile(zip_path) as zf:
        names = sorted(n for n in zf.namelist() if n.lower().endswith('.csv'))
        for name in names:
            raw = zf.read(name)
            text = raw.decode('utf-8-sig', errors='replace')
            for row in csv.reader(io.StringIO(text)):
                if len(row) < 2:
                    continue
                key = row[0].strip()
                if not key:
                    continue
                if len(row) == 2:
                    value, has_translation = row[1], False
                else:
                    value, has_translation = row[-1], True
                if not value:
                    continue
                old = entries.get(key)
                # 优先取有汉化的；汉化状态相同则取遍历顺序靠后的（覆盖）
                if old is None or has_translation or not old.has_translation:
                    entries[key] = _Entry(value, has_translation)
    merged = {k: v.text for k, v in entries.items()}
    return merged, len(names)


def save_merged_csv(merged, out_path):
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        for k in sorted(merged.keys()):
            w.writerow([k, merged[k]])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--project-id', type=int, default=PROJECT_ID)
    ap.add_argument('--token', default=None)
    ap.add_argument('--out', default='data/Merged.csv')
    ap.add_argument('--timeout', type=int, default=300, help='等待远程打包完成的最长秒数')
    ap.add_argument('--interval', type=int, default=3, help='轮询间隔秒数')
    ap.add_argument('--from-zip', default=None, help='跳过触发导出，直接用本地已有的 zip 文件合并')
    ap.add_argument('--keep-zip', default=None, help='顺便把下载的 zip 存一份到这个路径，方便排查问题')
    args = ap.parse_args()

    if args.from_zip:
        zip_path = args.from_zip
    else:
        token = args.token or PARATRANZ_TOKEN
        project_id = args.project_id
        if not token:
            sys.exit('请先配置 config 或 --token')
        if not project_id:
            sys.exit('请先配置 config 或 --project-id')

        print('触发导出...')
        try:
            trigger_export(project_id, token)
        except urllib.error.HTTPError as e:
            sys.exit(f'触发导出失败: HTTP {e.code} {e.reason}\n{e.read().decode("utf-8", "ignore")}')

        print('等待打包完成...')
        # try:
        #     wait_for_fresh_export(project_id, token, timeout=args.timeout, interval=args.interval)
        # except (RuntimeError, TimeoutError) as e:
        #     sys.exit(str(e))

        zip_path = args.keep_zip or str(Path(tempfile.gettempdir()) / f'paratranz_{project_id}_artifacts.zip')
        print('下载打包文件...')
        try:
            download_artifact_zip(project_id, token, zip_path)
        except urllib.error.HTTPError as e:
            sys.exit(f'下载失败: HTTP {e.code} {e.reason}')

    print('解压并合并...')
    merged, file_count = merge_zip(zip_path)
    save_merged_csv(merged, args.out)
    print(f'完成。合并了 {file_count} 个 CSV 文件，共 {len(merged)} 条 key，写入 {args.out}')

    if not args.from_zip and not args.keep_zip:
        try:
            Path(zip_path).unlink()
        except OSError:
            pass


if __name__ == '__main__':
    main()
#!/usr/bin/env python3
"""
调用 Paratranz 的"导出"接口，把整个项目的文本文件打包下载，解压后按 key
合并成一份 data/Merged.csv（key,text 两列，无表头，utf-8-sig），供
2_check_consistency.py 的 --translated 参数使用。

对应 Paratranz Artifacts API（见 https://paratranz.cn/api-docs）：
    GET  /projects/{id}/artifacts/download    302 重定向到 OSS 上最近一次打包好的 zip
    GET  /projects/{id}/artifacts             查询最近一次打包的信息（total/translated/
                                               createdAt 等统计字段，没有 status）
    POST /projects/{id}/artifacts             手动触发重新打包——仅项目管理员可用，
                                               普通成员会收到 403

Paratranz 每小时自动打包一次，默认直接下载最新的自动打包结果（普通成员即可）。
项目管理员想强制刷新时加 --trigger：先记下触发前的 createdAt，POST 触发后轮询
GET /artifacts，靠 createdAt 变化判断新包是否完成。

合并规则：
    - 每个 csv 文件里，每一行第一列是 key，最后一列是文本
        2 列   -> 未汉化，第二列是原文
        3+ 列  -> 已汉化，最后一列是译文
    - 同一个 key 多次出现：优先保留"已汉化"的；状态相同则按文件名排序后靠后的覆盖

用法:
    python scripts/1_fetch_paratranz_artifacts.py --out data/Merged.csv
    python scripts/1_fetch_paratranz_artifacts.py --out data/Merged.csv --trigger
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
from urllib.parse import urljoin

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import PARATRANZ_TOKEN, PROJECT_ID

API_BASE = 'https://paratranz.cn/api'
SITE_BASE = 'https://paratranz.cn'


def api_request(method, url, token, timeout=60):
    req = urllib.request.Request(url, method=method, headers={'Authorization': token})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def get_artifact_info(project_id, token):
    """GET /projects/{id}/artifacts —— 返回最近一次打包结果的统计信息。"""
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
    """POST /projects/{id}/artifacts —— 仅项目管理员可用，普通成员会 403。"""
    url = f'{API_BASE}/projects/{project_id}/artifacts'
    api_request('POST', url, token)


def wait_for_fresh_export(project_id, token, baseline_created_at, timeout=300, interval=5):
    """接口没有任务状态字段，靠 createdAt 是否变化间接判断打包完成。"""
    start = time.time()
    while time.time() - start < timeout:
        info = get_artifact_info(project_id, token)
        created = info.get('createdAt')
        if created and created != baseline_created_at:
            print(f'  检测到新的打包结果: {created}')
            return info
        time.sleep(interval)
    raise TimeoutError(f'等待新打包超过 {timeout} 秒仍未看到 createdAt 更新')


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def download_artifact_zip(project_id, token, out_zip_path):
    """下载接口返回 302 指向 OSS 签名 URL。手动跟随重定向，且不把 Authorization
    带给 OSS（签名 URL 已自带认证，多余的头可能导致 400）。"""
    url = f'{API_BASE}/projects/{project_id}/artifacts/download'
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(url, headers={'Authorization': token})
    try:
        with opener.open(req, timeout=120) as resp:
            data = resp.read()  # 非重定向（直接返回内容）的情况
    except urllib.error.HTTPError as e:
        if e.code not in (301, 302, 303, 307, 308):
            raise
        location = e.headers.get('Location')
        if not location:
            raise RuntimeError('下载接口返回重定向但没有 Location 头')
        real_url = urljoin(SITE_BASE, location)
        with urllib.request.urlopen(real_url, timeout=300) as resp:
            data = resp.read()

    if not data.startswith(b'PK'):
        raise RuntimeError(f'下载结果不是 zip 文件（前 100 字节: {data[:100]!r}）')
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
    ap.add_argument('--timeout', type=int, default=300, help='--trigger 时等待打包完成的最长秒数')
    ap.add_argument('--interval', type=int, default=5, help='--trigger 时的轮询间隔秒数')
    ap.add_argument('--trigger', action='store_true', help='管理员专用：先强制触发重新打包再下载')
    ap.add_argument('--from-zip', default=None, help='跳过联网，直接用本地已有的 zip 合并')
    ap.add_argument('--keep-zip', default=None, help='把下载的 zip 另存一份到该路径')
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

        info = {}
        try:
            info = get_artifact_info(project_id, token)
            print('当前最新打包：')
            print_artifact_info(info)
        except urllib.error.HTTPError as e:
            print(f'  查询打包信息失败 (HTTP {e.code})，继续尝试直接下载')

        if args.trigger:
            print('触发导出（需要管理员权限）...')
            try:
                trigger_export(project_id, token)
            except urllib.error.HTTPError as e:
                sys.exit(f'触发导出失败: HTTP {e.code} {e.reason}\n'
                         f'（403 说明不是项目管理员，去掉 --trigger 直接下载最新自动打包即可）')
            print('等待打包完成...')
            try:
                wait_for_fresh_export(project_id, token, info.get('createdAt'),
                                      timeout=args.timeout, interval=args.interval)
            except TimeoutError as e:
                sys.exit(str(e))

        zip_path = args.keep_zip or str(Path(tempfile.gettempdir()) / f'paratranz_{project_id}_artifacts.zip')
        print('下载打包文件...')
        try:
            download_artifact_zip(project_id, token, zip_path)
        except (urllib.error.HTTPError, RuntimeError) as e:
            sys.exit(f'下载失败: {e}')

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
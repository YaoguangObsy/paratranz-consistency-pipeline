#!/usr/bin/env python3
"""
Translation consistency checker.

Compares a source-language CSV, a translated-language CSV (same key format),
and an optional glossary CSV, and produces two reports:

  1. <outdir>/inconsistency_duplicate_source.csv
     Groups entries whose SOURCE text is identical but whose TRANSLATION
     differs. Only the minority ("odd one out") rows are listed as fix
     candidates, alongside the majority translation for reference.
     Needs no glossary - catches AI/MT drift directly.

  2. <outdir>/inconsistency_glossary.csv
     For every glossary term (plus its variants) found in the source text,
     checks whether ANY of the glossary's accepted translations (split on
     "/" and "、") appears in the translated text. Lines that are still
     fully untranslated (source == translation) are excluded - that is a
     completeness problem, not a consistency problem.
     Each row is tagged confidence=high/low:
       high  -> term looks like a proper noun (name/place/species) with no
                documented context-dependent rule in its glossary note.
                These are the most reliable hits.
       low   -> generic word, ALL-CAPS UI token, or the glossary note
                itself says the translation depends on context. Expect a
                much higher false-positive rate here; spot check before
                batch-fixing.

Source/translation CSVs are expected as two plain columns with NO header:
    key,text
(the exact format produced by most localization export tools, e.g.
Paratranz). The glossary CSV is expected to have a header row with at
least: term,translation,note,variants (variants pipe-separated "a | b").
Column names are configurable via flags if yours differ.

Usage:
    python ./scripts/2_check_consistency.py `
            --source ./data/Original_En.csv `
            --translated ./data/Merged.csv `
            --glossary ./data/paratranz_terms.csv `
            --exclude-keys ./data/excluded_keys.txt `
            --outdir ./reports

All args except --source/--translated are optional; without --glossary,
only the duplicate-source report is produced.
"""
import argparse
import csv
import re
from collections import defaultdict
from pathlib import Path

CONTEXT_KEYWORDS = [
    '语境', '根据', '视情况', '仅限', '按键', '尽量', '优先',
    '全大写', '出现在', '按语境', '视文本', '视上下文',
    'context', 'depending on', 'only when',
]


def load_kv_csv(path):
    """Load a headerless key,text CSV into a dict."""
    d = {}
    with open(path, encoding='utf-8-sig', newline='') as f:
        for row in csv.reader(f):
            if len(row) < 2:
                continue
            d[row[0]] = row[1]
    return d


def is_proper_noun(term):
    if re.match(r'^[A-Z][a-z]', term):
        return True
    if ' ' in term and term.replace(' ', '').isalpha() and term == term.title():
        return True
    return False


def make_pattern(src):
    esc = re.escape(src)
    left = r'(?<!\w)' if src[0].isalnum() else ''
    right = r'(?!\w)' if src[-1].isalnum() else ''
    return re.compile(left + esc + right, re.IGNORECASE)


def check_duplicate_source(source, translated, common_keys):
    """Report A: identical source text, divergent translation."""
    by_src = defaultdict(list)
    untranslated = 0
    for k in common_keys:
        s = source[k].strip()
        if len(s) < 2:
            continue
        if translated[k].strip() == s:
            untranslated += 1
            continue
        by_src[s].append(k)

    groups = []
    for src_text, keys in by_src.items():
        if len(keys) < 2:
            continue
        variants = defaultdict(list)
        for k in keys:
            variants[translated[k].strip()].append(k)
        if len(variants) > 1:
            groups.append({
                'src': src_text,
                'total_keys': len(keys),
                'variants': [{'text': t, 'keys': ks} for t, ks in variants.items()],
            })
    groups.sort(key=lambda g: g['total_keys'], reverse=True)

    rows = []
    for g in groups:
        variants = sorted(g['variants'], key=lambda v: len(v['keys']), reverse=True)
        majority = variants[0]
        for v in variants[1:]:
            for k in v['keys']:
                rows.append({
                    'group_size': g['total_keys'],
                    'source_text': g['src'],
                    'majority_translation': majority['text'],
                    'majority_count': len(majority['keys']),
                    'key': k,
                    'current_translation': v['text'],
                })
    return rows, untranslated


def check_glossary(source, translated, common_keys, glossary_rows,
                    term_col, trans_col, note_col, variants_col):
    terms = []
    for row in glossary_rows:
        term = row[term_col].strip()
        trans = row[trans_col].strip()
        if not term or not trans:
            continue
        variants = [v.strip() for v in row.get(variants_col, '').split('|') if v.strip()]
        sources = list(dict.fromkeys([term] + variants))
        terms.append({
            'sources': sources,
            'translation': trans,
            'note': row.get(note_col, ''),
        })

    # inverted index: first word (lowercased) -> set(keys) containing it,
    # so we don't scan every term against every line.
    word_index = defaultdict(set)
    tokenizer = re.compile(r"[A-Za-z']+")
    for k in common_keys:
        for w in tokenizer.findall(source[k]):
            word_index[w.lower()].add(k)

    def candidates(src):
        m = re.match(r"[A-Za-z']+", src.strip())
        if m:
            return word_index.get(m.group(0).lower(), set())
        return set(common_keys)

    mismatches = defaultdict(list)
    for t in terms:
        patterns = [(s, make_pattern(s)) for s in t['sources']]
        cand = set()
        for s in t['sources']:
            cand |= candidates(s)
        trans_opts = [o.strip() for o in re.split(r'[/、]', t['translation']) if o.strip()]
        for k in cand:
            etext = source[k]
            matched = next((s for s, pat in patterns if pat.search(etext)), None)
            if not matched:
                continue
            ztext = translated[k]
            if ztext.strip() == etext.strip():
                continue  # untranslated leftover, not a consistency issue
            if any(o in ztext for o in trans_opts):
                continue
            mismatches[t['sources'][0]].append({
                'key': k, 'matched_term': matched, 'expected': t['translation'],
                'source_text': etext.strip(), 'current_translation': ztext.strip(),
                'note': t['note'],
            })

    groups = []
    for term, items in mismatches.items():
        note = items[0]['note']
        ctx = any(kw in note for kw in CONTEXT_KEYWORDS)
        confidence = 'high' if (is_proper_noun(term) and not ctx) else 'low'
        groups.append({'term': term, 'count': len(items), 'confidence': confidence, 'items': items})
    groups.sort(key=lambda g: (g['confidence'] != 'high', -g['count']))

    rows = []
    for g in groups:
        for it in g['items']:
            rows.append({
                'term_count': g['count'],
                'confidence': g['confidence'],
                'term': g['term'],
                'matched_source_text': it['matched_term'],
                'expected_translation': it['expected'],
                'key': it['key'],
                'source_text': it['source_text'],
                'current_translation': it['current_translation'],
                'glossary_note': it['note'],
            })
    return rows


def write_csv(path, rows, fieldnames):
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--source', required=True, help='Source-language CSV (key,text — no header)')
    ap.add_argument('--translated', required=True, help='Translated-language CSV (key,text — no header)')
    ap.add_argument('--glossary', help='Glossary CSV with header row (optional)')
    ap.add_argument('--outdir', default='.', help='Output directory for reports')
    ap.add_argument('--term-col', default='term')
    ap.add_argument('--translation-col', default='translation')
    ap.add_argument('--note-col', default='note')
    ap.add_argument('--variants-col', default='variants')
    ap.add_argument('--exclude-keys', help='Text file, one key per line, to drop before analysis '
                                            '(e.g. hidden/locked entries pulled via fetch_paratranz_hidden_keys.py)')
    args = ap.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    source = load_kv_csv(args.source)
    translated = load_kv_csv(args.translated)
    common = [k for k in source if k in translated]

    if args.exclude_keys:
        with open(args.exclude_keys, encoding='utf-8-sig') as f:
            excluded = {line.strip() for line in f if line.strip()}
        before = len(common)
        common = [k for k in common if k not in excluded]
        print(f'Excluded {before - len(common)} keys (of {len(excluded)} in exclude list) before analysis')

    print(f'Common keys: {len(common)}')

    dup_rows, untranslated = check_duplicate_source(source, translated, common)
    write_csv(
        outdir / 'inconsistency_duplicate_source.csv', dup_rows,
        ['group_size', 'source_text', 'majority_translation', 'majority_count', 'key', 'current_translation']
    )
    print(f'Duplicate-source inconsistencies: {len(dup_rows)} rows')
    print(f'(Untranslated leftover lines, source==translation, excluded from checks: {untranslated})')

    if args.glossary:
        with open(args.glossary, encoding='utf-8-sig', newline='') as f:
            glossary_rows = list(csv.DictReader(f))
        gl_rows = check_glossary(
            source, translated, common, glossary_rows,
            args.term_col, args.translation_col, args.note_col, args.variants_col
        )
        write_csv(
            outdir / 'inconsistency_glossary.csv', gl_rows,
            ['term_count', 'confidence', 'term', 'matched_source_text', 'expected_translation',
             'key', 'source_text', 'current_translation', 'glossary_note']
        )
        high = sum(1 for r in gl_rows if r['confidence'] == 'high')
        print(f'Glossary inconsistencies: {len(gl_rows)} rows ({high} high-confidence)')

    print(f'Reports written to: {outdir}/')


if __name__ == '__main__':
    main()

"""Run the Case Quote rules on fake sample data, with no Google account needed.

    python src/demo.py sample-data/input/case-files.json sample-data/output

Instead of calling Gemini, it uses the saved (made-up) page answers in the input file. Each answer
still goes through reader.clean(), exactly as a live answer would. Writes three CSV files: the quote
table, the Files Read tab and the Where Found tab.
"""
import csv
import json
import os
import sys

import quote
import reader


def main(source, out_dir):
    case = json.load(open(source))
    files, pages = [], []
    for index, f in enumerate(case['files']):
        answer = f.get('saved_ai_answer')
        if answer is None:
            files.append({'id': f['id'], 'name': f['name'], 'mime': f['mime'], 'pages': '',
                          'status': quote.STATUS_NOT_PDF})
            continue
        checked = reader.clean(answer, f['page_count'])
        files.append({'id': f['id'], 'name': f['name'], 'mime': f['mime'], 'pages': f['page_count'],
                      'status': quote.STATUS_READ})
        pages += [dict(p, file=index, file_page=p['page']) for p in checked]

    providers, others = quote.group(pages)
    table = [['Medical Provider', 'Record Pages', 'Billing Lines']] + quote.rows(providers, others)
    os.makedirs(out_dir, exist_ok=True)
    for name, rows in (('quote-table.csv', table),
                       ('files-read.csv', quote.files_rows(files, providers, others)),
                       ('where-found.csv', quote.where_rows(providers, others, files))):
        with open(os.path.join(out_dir, name), 'w', newline='') as fh:
            csv.writer(fh).writerows(rows)
    summary = {'providers': len(providers), 'total_pages': len(pages),
               'billing_lines': sum(p['lines'] for p in providers + others)}
    with open(os.path.join(out_dir, 'run-summary.json'), 'w') as fh:
        json.dump(summary, fh, indent=2)
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main(*(sys.argv[1:] or ['sample-data/input/case-files.json', 'sample-data/output']))

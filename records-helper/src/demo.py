"""Run the Records Helper rules on fake sample data, with no Google account needed.

    python src/demo.py sample-data/input/delivery.json sample-data/output

Instead of calling Gemini, it uses the saved (made-up) answers in the input file, so the run is
repeatable. It writes the index sheet and the rename history as CSV files.
"""
import csv
import json
import os
import sys
from datetime import datetime, timezone

import logic


def run(case, now):
    plaintiff = logic.single_plaintiff(case['plaintiff'])
    plaintiffs_list = '' if plaintiff else case['plaintiff']
    found, done, deliveries = [], set(), []
    for d in case['deliveries']:
        files = d['files']
        if logic.is_recent(files, now):
            deliveries.append({'delivery': d['name'], 'status': 'still uploading, try tomorrow'})
            continue
        done.add(d['folder_id'])
        deliveries.append(dict(delivery=d['name'], status='ok', **logic.summarize(files)))
        for f in sorted(files, key=lambda f: f['createdTime']):
            found.append(dict(f, delivery_folder=d['folder_id'], delivery=d['name'],
                              arrival=d['arrival_date'], plaintiff=plaintiff))

    existing = case.get('existing_rows', [])
    appends, updates = logic.plan(existing, found, done)

    # Suggest a number and a name for each new PDF.
    rows = logic.rows_now(existing, appends, updates)
    for file_id, note in logic.repeat_flags(rows, case['working_folder'], found).items():
        logic.set_cells(rows[file_id][0], {'Notes': note}, appends, updates)
    number = logic.highest(logic.case_numbers(rows, case['working_folder']))
    ai_reads = 0
    for f in logic.to_name(rows, found):
        where = rows[f['id']][0]
        who, kind, who_for, multiple, flag, skip = logic.name_match(f['name'], case['known_providers']), '', '', False, '', ''
        if not who:
            ai_reads += 1
            answer = case['saved_ai_answers'][f['id']]   # live version: reader.ask_gemini(...)
            who, kind, who_for, multiple, flag = logic.from_reading(answer, plaintiffs_list)
            skip = logic.not_medical(answer)
        if flag:
            logic.set_cells(where, {'Notes': logic.LOOK + flag}, appends, updates)
            continue
        if skip:
            logic.set_cells(where, {"Who it's from": logic.text(who), 'Record type': logic.text(kind),
                                    'Notes': skip}, appends, updates)
            continue
        number += 1
        change = {'Suggested name': logic.text(logic.suggested(number, who, multiple, logic.name_date(f['name']))),
                  "Who it's from": logic.text(who)}
        if kind:
            change['Record type'] = logic.text(kind)
        if who_for:
            change['Plaintiff'] = logic.text(who_for)
        logic.set_cells(where, change, appends, updates)

    # Rename in Drive (here: just record it) and log each rename in the History tab.
    rows = logic.rows_now(existing, appends, updates)
    history = []
    for f, where, new in logic.renames(rows, found):
        logic.set_cells(where, {'Name now': logic.link(f['id'], new)}, appends, updates)
        history.append(logic.history_row(now.strftime('%Y-%m-%d'), f['id'], f['name'], new))

    counts = logic.delivery_counts(logic.rows_now(existing, appends, updates), found, set())
    for d, out in zip(case['deliveries'], deliveries):
        out.update(counts.get(d['folder_id'], {}))
    return appends, history, {'rows_added': len(appends), 'files_renamed': len(history),
                              'ai_reads': ai_reads, 'deliveries': deliveries}


def main(source, out_dir):
    case = json.load(open(source))
    now = datetime.fromisoformat(case['run_time'])
    appends, history, summary = run(case, now)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, 'index-sheet.csv'), 'w', newline='') as fh:
        csv.writer(fh).writerows([logic.HEADERS] + appends)
    with open(os.path.join(out_dir, 'rename-history.csv'), 'w', newline='') as fh:
        csv.writer(fh).writerows([logic.HISTORY_HEADERS] + history)
    with open(os.path.join(out_dir, 'run-summary.json'), 'w') as fh:
        json.dump(summary, fh, indent=2)
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main(*(sys.argv[1:] or ['sample-data/input/delivery.json', 'sample-data/output']))

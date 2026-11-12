"""Records Helper rules: decide what goes in the index sheet and what each file is renamed to.

Pure functions only (no Google calls), so every rule here is covered by test_logic.py.

Index sheet columns: see HEADERS. Each incoming file gets one row. Rows are matched to files by
Drive file ID, so re-running never duplicates a row and never overwrites what a person typed.
"""
import os
import re
from collections import Counter
from datetime import datetime, timedelta

HEADERS = ['Number', 'Name now', 'Original name', "Who it's from", 'Record type', 'File size', 'File type',
           'Delivery', 'Arrival date', 'Plaintiff', 'Notes', 'Suggested name', 'File ID', 'Folder ID']
HISTORY_HEADERS = ['When', 'File', 'What happened', 'From', 'To', 'By']
WIDTH = len(HEADERS)
COL = {h: i for i, h in enumerate(HEADERS)}
PDF = 'application/pdf'
NOT_INDEXED = 'not indexed'
REMOVED = 'removed'
LOOK = 'needs a look: '
# A folder with any change this recent is probably still being uploaded; it waits for the next run.
QUIET_HOURS = 2

# A file already numbered by hand: "28 - Example Clinic.pdf", "012 Records.pdf", "12.1 Bill.pdf".
# A date at the start ("2024-10-14 records.pdf", "08-12-2024 records.pdf") is NOT a file number.
LEADING_NUMBER = re.compile(r'^\s*(\d{1,4}(?:\.\d+)?[A-Za-z]?)(?:\s*[-–._)]\s*|\s+)\S')
DATE_START = re.compile(r'^\s*(\d{4}[-_.]\d{1,2}[-_.]\d{1,2}|\d{1,2}[-_.]\d{1,2}[-_.](\d{4}|\d{2})(?!\d))')
MANY_PLAINTIFFS = re.compile(r'\band\b|&|,|;|\bet al\b', re.I)

# Google accounts that belong to the business itself, shown by a friendly name instead of the
# Drive display name. Set OWN_ACCOUNTS="you@example.com=Office,assistant@example.com=Assistant".
OWN_ACCOUNTS = dict(pair.split('=', 1) for pair in os.environ.get('OWN_ACCOUNTS', '').split(',') if '=' in pair)


def leading_number(name):
    if DATE_START.match(name or ''):
        return ''
    m = LEADING_NUMBER.match(name or '')
    if not m:
        return ''
    token = m.group(1)
    whole, _, rest = token.partition('.')
    digits = re.match(r'\d+', whole).group(0)
    return str(int(digits)) + whole[len(digits):] + ('.' + rest if rest else '')


def single_plaintiff(text):
    """The case's plaintiff when there is only one; '' when several (then the AI decides per file)."""
    text = (text or '').strip()
    return '' if not text or MANY_PLAINTIFFS.search(text) else text


def first_name(display, email=''):
    key = (email or '').strip().lower()
    if key in OWN_ACCOUNTS:
        return OWN_ACCOUNTS[key]
    name = (display or '').strip().split(' ')[0]
    return name[:1].upper() + name[1:]


def parse_time(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def is_recent(files, now, hours=QUIET_HOURS):
    cutoff = now - timedelta(hours=hours)
    return any(parse_time(f[k]) > cutoff for f in files for k in ('createdTime', 'modifiedTime') if f.get(k))


def summarize(files):
    """Counts for one delivery. Uploaded by = whoever owns most of the files (ties: the earliest)."""
    ordered = sorted(files, key=lambda f: f['createdTime'])
    owners = [first_name(o.get('displayName'), o.get('emailAddress'))
              for o in ((f.get('owners') or [{}])[0] for f in ordered)]
    counts = Counter(o for o in owners if o)
    by = max(counts, key=lambda o: (counts[o], -owners.index(o))) if counts else ''
    return {
        'pdfs': sum(1 for f in files if f['mimeType'] == PDF),
        'other': sum(1 for f in files if f['mimeType'] != PDF),
        'uploaded_by': by,
        'uploaded_on': ordered[0]['createdTime'] if ordered else '',
    }


def text(value):
    """A cell Google Sheets must keep exactly as typed (no dates or numbers guessed from it)."""
    return "'" + value if value else ''


def file_size(value):
    """Bytes -> '850 KB', '2.4 MB', '1.3 GB'. '' when Drive gives no size (Google Docs)."""
    try:
        size = int(value)
    except (TypeError, ValueError):
        return ''
    for unit, step in (('GB', 1024 ** 3), ('MB', 1024 ** 2)):
        if size >= step:
            return f'{size / step:.1f} {unit}'
    return f'{max(1, round(size / 1024))} KB'


FILE_TYPES = {PDF: 'PDF', 'application/msword': 'Word',
              'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'Word',
              'application/vnd.ms-excel': 'Excel',
              'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'Excel',
              'application/zip': 'Zip', 'application/x-zip-compressed': 'Zip',
              'application/vnd.google-apps.document': 'Google Doc',
              'application/vnd.google-apps.spreadsheet': 'Google Sheet',
              'text/plain': 'Text', 'text/csv': 'CSV'}


def file_type(name, mime):
    """Drive's format -> 'PDF', 'Word', 'Image'...; else the name's own ending, else 'Other'."""
    mime = mime or ''
    if mime in FILE_TYPES:
        return FILE_TYPES[mime]
    for start in ('Image', 'Video', 'Audio'):
        if mime.startswith(start.lower() + '/'):
            return start
    ending = re.search(r'\.([A-Za-z0-9]{1,5})$', name or '')
    return ending.group(1).upper() if ending else 'Other'


def link(file_id, name):
    return '=HYPERLINK("https://drive.google.com/file/d/{}/view","{}")'.format(file_id, name.replace('"', '""'))


def new_row(f, delivery, arrival, plaintiff):
    row = [''] * WIDTH
    row[COL['Number']] = leading_number(f['name'])
    row[COL['Name now']] = link(f['id'], f['name'])
    row[COL['Original name']] = text(f['name'])
    row[COL['File size']] = text(file_size(f.get('size')))
    row[COL['File type']] = text(file_type(f['name'], f['mimeType']))
    row[COL['Plaintiff']] = text(plaintiff)
    row[COL['Delivery']] = text(delivery)
    row[COL['Arrival date']] = arrival
    row[COL['Notes']] = skip_reason(f['name'], f['mimeType'])
    row[COL['File ID']] = f['id']
    row[COL['Folder ID']] = f['delivery_folder']
    return row


def plan(existing, found, folders_done):
    """existing: the sheet's rows below the header (lists, maybe short).
    found: every file seen this run, each with delivery_folder, delivery, arrival, plaintiff.
    folders_done: delivery folders read in full this run.
    Returns (rows to append, {sheet row number: {column name: value}}). Only the helper's own cells
    change on an existing row; anything a person typed into other columns stays."""
    by_id = {}
    for n, row in enumerate(existing):
        row = list(row) + [''] * (WIDTH - len(row))
        if row[COL['File ID']]:
            by_id[row[COL['File ID']]] = (n + 2, row)
    appends, updates = [], {}
    seen = set()
    for f in found:
        seen.add(f['id'])
        if f['id'] not in by_id:
            appends.append(new_row(f, f['delivery'], f['arrival'], f['plaintiff']))
            continue
        number, row = by_id[f['id']]
        change = {'Name now': link(f['id'], f['name'])}
        if not row[COL['Number']] and leading_number(f['name']):
            change['Number'] = leading_number(f['name'])
        if not row[COL['File size']] and file_size(f.get('size')):
            change['File size'] = text(file_size(f.get('size')))
        if not row[COL['File type']]:
            change['File type'] = text(file_type(f['name'], f['mimeType']))
        if row[COL['Notes']] == REMOVED:
            change['Notes'] = skip_reason(f['name'], f['mimeType'])
        if row[COL['Folder ID']] != f['delivery_folder']:
            change['Folder ID'] = f['delivery_folder']
            change['Delivery'] = text(f['delivery'])
        updates[number] = change
    for file_id, (number, row) in by_id.items():
        if file_id not in seen and row[COL['Folder ID']] in folders_done and row[COL['Notes']] != REMOVED:
            updates[number] = {'Notes': REMOVED}
    return appends, updates


# ---- A suggested number and name for each new PDF ----

SENDER_INDEX = re.compile(r'\b(index|table of contents)\b', re.I)
# Words that say what a file is, not who it is from, so they never decide a match.
GENERIC = {'records', 'record', 'bills', 'bill', 'billing', 'billings', 'medical', 'itemized', 'statement',
           'statements', 'copy', 'copies', 'pdf', 'of', 'and', 'the', 'for', 'from', 'inc', 'llc', 'pc', 'md',
           'dr', 'pa', 'apc', 'amc', 'corp', 'group', 'part', 'set', 'final', 'subpoena', 'subpoenaed', 'sdt',
           'cnr', 'certificate', 'no', 'custodian', 'response', 'docs', 'documents'}
BAD_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
MAX_NAME = 80


def skip_reason(name, mime):
    """Why a file is never numbered, or '' when it should be."""
    if mime != PDF:
        return NOT_INDEXED
    if SENDER_INDEX.search(name or ''):
        return NOT_INDEXED + " (sender's index)"
    return ''


def number_value(token):
    """'12.1' -> 12. Four-digit years ('2024 MRI.pdf') are not file numbers."""
    m = re.match(r'\d+', token or '')
    if not m:
        return None
    value = int(m.group(0))
    if len(m.group(0).lstrip('0')) == 4 and 1900 <= value <= 2099:
        return None
    return value


def words(text):
    return [w for w in re.sub(r'[^a-z0-9]+', ' ', (text or '').lower()).split()
            if w not in GENERIC and not w.isdigit()]


def name_match(file_name, known):
    """The one known provider the file name clearly names: every distinctive word of the provider
    (at least two) appears in the file name, and no other known provider fits.
    Returns the provider as spelled in the list, or ''. No AI call is needed when this hits."""
    have = set(words(re.sub(r'\.pdf$', '', file_name or '', flags=re.I)))
    fits = {}
    for name in known:
        need = set(words(name))
        if len(need) >= 2 and need <= have:
            fits.setdefault(frozenset(need), name)
    return next(iter(fits.values())) if len(fits) == 1 else ''


def clean_name(text):
    text = re.sub(r'\s+', ' ', BAD_CHARS.sub(' ', text or '')).strip(' .-')
    return text[:MAX_NAME].rstrip(' .-')


def suggested(number, who, multiple, date=''):
    return f"{number} - {clean_name(who)}{' [Multiple]' if multiple else ''}{' ' + date if date else ''}.pdf"


# ---- A date in the original file name goes on the end of the new name ----

ANY_DATE = re.compile(r'(?<![A-Za-z0-9])(?:(?P<y1>\d{4})(?P<s1>[-_./])(?P<m1>\d{1,2})(?P=s1)(?P<d1>\d{1,2})'
                      r'|(?P<m2>\d{1,2})(?P<s2>[-_./])(?P<d2>\d{1,2})(?P=s2)(?P<y2>\d{4}|\d{2}))(?![A-Za-z0-9])')
BIRTH_BEFORE = re.compile(r'(d\.?o\.?b\.?|born|birth\s?(date|day)?|date of birth)[\s:_#.-]*$', re.I)
RANGE_WORD = re.compile(r'^\s*(to|thru|through|-|–|—|_|&)?\s*$', re.I)
CHAIN = re.compile(r'\d[-_./]$')


def read_date(m, this_year):
    """A date match -> 'MM-DD-YYYY' (US month-first), or '' when it cannot be a real date."""
    year, month, day = (m['y1'] or m['y2']), (m['m1'] or m['m2']), (m['d1'] or m['d2'])
    year = int(year)
    if year < 100:                       # 24 -> 2024, 98 -> 1998
        year += 2000 if 2000 + year <= this_year else 1900
    if not 1950 <= year <= this_year + 1:
        return ''
    try:
        return datetime(year, int(month), int(day)).strftime('%m-%d-%Y')
    except ValueError:
        return ''


def name_date(name, this_year=None):
    """The date a file name carries: '08-12-2024', '01-01-2020 to 03-03-2021' (both ends of a range),
    or ''. A birth date, an impossible date and a date buried in a longer number
    (claim number 12-10-2024-0001) do not count. Two unrelated dates: the first."""
    this_year = this_year or datetime.now().year
    name = name or ''
    found, used_to = [], -1
    for m in ANY_DATE.finditer(name):
        before, after = name[:m.start()], name[m.end():]
        joined = m.start() - 1 == used_to     # the second half of a range written date-date
        if CHAIN.search(before) and not joined:
            continue
        if re.match(r'[-_./]\d', after) and not ANY_DATE.match(after[1:]):
            continue
        if BIRTH_BEFORE.search(before):
            continue
        date = read_date(m, this_year)
        if date:
            found.append((date, m.start(), m.end()))
            used_to = m.end()
    if not found:
        return ''
    first = found[0]
    if len(found) > 1 and found[1][0] != first[0] and RANGE_WORD.match(name[first[2]:found[1][1]]):
        return f'{first[0]} to {found[1][0]}'
    return first[0]


def highest(numbers):
    values = [v for v in (number_value(n) for n in numbers) if v is not None]
    return max(values) if values else 0


def repeats(numbered):
    """numbered: [(file id, number token)]. Returns {file id: number} for every file whose number
    another file on the case also has."""
    holders = {}
    for file_id, token in numbered:
        if token:
            holders.setdefault(token, set()).add(file_id)
    return {fid: token for token, ids in holders.items() if len(ids) > 1 for fid in ids}


def number_range(numbers):
    """[11, 12, 13, 20] -> '11–13, 20'."""
    values = sorted(set(numbers))
    parts, start = [], None
    for i, v in enumerate(values):
        if start is None:
            start = v
        if i + 1 == len(values) or values[i + 1] != v + 1:
            parts.append(str(start) if start == v else f'{start}–{v}')
            start = None
    return ', '.join(parts)


def from_reading(answer, plaintiffs_text):
    """Gemini's answer -> (who it's from, record type, plaintiff, several providers, flag)."""
    providers = [p.strip() for p in answer.get('all_providers') or [] if p and p.strip()]
    main = (answer.get('main_provider') or '').strip()
    if answer.get('from_a_provider') and main:
        who = main
    else:
        who = (answer.get('document_type') or '').strip()
    multiple = len({p.lower() for p in providers + ([main] if main else [])}) > 1
    plaintiff = (answer.get('plaintiff') or '').strip() if plaintiffs_text else ''
    if plaintiff and plaintiff.lower() not in plaintiffs_text.lower():
        plaintiff = ''                    # the AI may only pick a name from the list it was given
    flag = '' if answer.get('confident') and clean_name(who) else 'unsure'
    return who, (answer.get('record_type') or '').strip(), plaintiff, multiple, flag


def rows_now(existing, appends, updates):
    """{file id: (where, row)} with this run's planned changes applied. where = the sheet row number
    of an existing row, or ('new', i) for appends[i]."""
    out = {}
    for n, row in enumerate(existing):
        row = list(row) + [''] * (WIDTH - len(row))
        for col, value in updates.get(n + 2, {}).items():
            row[COL[col]] = value
        if row[COL['File ID']]:
            out[row[COL['File ID']]] = (n + 2, row)
    for i, row in enumerate(appends):
        out[row[COL['File ID']]] = (('new', i), row)
    return out


def set_cells(where, change, appends, updates):
    if isinstance(where, tuple):
        for col, value in change.items():
            appends[where[1]][COL[col]] = value
    else:
        updates.setdefault(where, {}).update(change)


def not_medical(answer):
    """Note for a PDF that is not a medical provider's records or bills (a demand letter,
    interrogatories): listed as not indexed, never numbered. '' when it is medical."""
    if answer.get('from_a_provider'):
        return ''
    kind = clean_name((answer.get('document_type') or '').strip())
    return NOT_INDEXED + (f' ({kind})' if kind else ' (not medical)')


def to_name(rows, found):
    """This run's PDFs still waiting for a suggestion: no number, no suggestion, no note. A row that
    already has any of them is never asked again, so a correction typed beside it stays put."""
    return [f for f in found if f['id'] in rows
            and not any(rows[f['id']][1][COL[c]] for c in ('Number', 'Suggested name', 'Notes'))
            and not skip_reason(f['name'], f['mimeType'])]


def renames(rows, found):
    """Files to rename in Drive: a suggested name, no number yet, and still the name the file
    arrived with, so a file someone renamed by hand is never renamed back.
    Returns [(file, where, new name)]."""
    out = []
    for f in found:
        where, row = rows.get(f['id'], (None, None))
        if row is None or row[COL['Number']]:
            continue
        new = row[COL['Suggested name']].lstrip("'")
        if new and new != f['name'] and f['name'] == row[COL['Original name']].lstrip("'"):
            out.append((f, where, new))
    return out


def history_row(day, file_id, old, new):
    return [text(day), link(file_id, new), 'Renamed', text(old), text(new), 'Helper']


def case_numbers(rows, tree):
    """Numbers already used on the case: every PDF in the case's working folder plus the sheet's
    own numbers and suggestions, so the next number never repeats an old one."""
    used = [leading_number(f['name']) for f in tree if f['mimeType'] == PDF]
    for _, row in rows.values():
        used += [row[COL['Number']], leading_number(row[COL['Suggested name']].lstrip("'"))]
    return used


def repeat_flags(rows, tree, found):
    """Notes for this run's hand-numbered PDFs whose number another file on the case also has.
    Files inside 'Work' folders are copies and do not count."""
    numbered = [(f['id'], leading_number(f['name'])) for f in tree if f['mimeType'] == PDF and not f.get('in_work')]
    seen = {fid for fid, _ in numbered}
    numbered += [(f['id'], leading_number(f['name'])) for f in found if f['mimeType'] == PDF and f['id'] not in seen]
    clash = repeats(numbered)
    return {f['id']: LOOK + 'repeated number ' + clash[f['id']] for f in found
            if f['id'] in clash and f['id'] in rows and not rows[f['id']][1][COL['Notes']]}


def delivery_counts(rows, found, waiting):
    """Per delivery folder: indexed, needs a look, still waiting (daily AI read limit), numbers used."""
    out = {}
    for f in found:
        c = out.setdefault(f['delivery_folder'], {'to_index': 0, 'indexed': 0, 'needs_look': 0, 'waiting': 0,
                                                  'numbers': []})
        if f['id'] in waiting:
            c['waiting'] += 1
        row = rows.get(f['id'], (None, None))[1]
        if row is None or skip_reason(f['name'], f['mimeType']) or row[COL['Notes']].startswith(NOT_INDEXED):
            continue
        c['to_index'] += 1
        token = row[COL['Number']] or leading_number(row[COL['Suggested name']].lstrip("'"))
        if token:
            c['indexed'] += 1
            if number_value(token) is not None:
                c['numbers'].append(number_value(token))
        if row[COL['Notes']].startswith(LOOK):
            c['needs_look'] += 1
    return {k: dict(v, numbers=number_range(v['numbers'])) for k, v in out.items()}

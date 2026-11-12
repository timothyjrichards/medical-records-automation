"""Quote numbers from the AI's page answers, and the Google Sheets writes. No network access.

A provider is the practice or facility a page belongs to (the doctor only when no practice is
named), so one hospital with many doctors is one row. Pages that belong to no provider go to
"Other pages" rows, so the provider rows always add up to the total.
"""
import names

OTHER = 'Other pages'
HEADER = 'Medical Provider'      # the table's header cell in column B; no header = not a quote sheet
TOTALS_LABEL = 'Total Providers' # column B label above the big total numbers
# The layout is found, not fixed, so the template can move its rows without breaking the code.

# What the AI says each page is. SKIPPED is never asked for: it marks a page the AI's answer left out.
KINDS = ['medical_record', 'bill', 'legal_or_admin', 'blank', 'unreadable']
SKIPPED = 'skipped'
# Marks every page of a section the AI never answered for, after all its tries.
AI_FAILED = 'ai_failed'
# Other pages split by why no provider owns them, in this order on the sheet.
REASONS = [
    ('legal_or_admin', OTHER + ' – legal & admin papers'),
    ('blank', OTHER + ' – blank'),
    ('unreadable', OTHER + ' – unreadable'),
    ('medical', OTHER + ' – medical, provider not found'),
    (SKIPPED, OTHER + ' – skipped by the AI'),
    (AI_FAILED, OTHER + ' – AI could not read'),
    ('', OTHER + ' – no reason given'),
]

STATUS_READ = 'read'
STATUS_NOT_PDF = 'not a PDF – not read'
STATUS_TOO_BIG = 'over 1 GB – not read'
STATUS_BROKEN = 'locked or broken – not read'
STATUS_DUPLICATE = 'exact copy, counted once – same as '

FILES_TAB = 'Files Read'
WHERE_TAB = 'Where Found'
FILES_HEAD = ['File Name', 'Page Count', 'Status', 'Providers Found', 'Other Pages', 'Source Link', 'File ID']
WHERE_HEAD = ['Provider', 'Source Document', 'PDF Pages', 'Page Count', 'Billing Lines',
              'Practice Names Seen', 'MD / Provider Names Seen', 'Source Link', 'File ID']


class Refusal(Exception):
    """A request that will fail the same way every time; the stage code is the message."""


def practice_of(page):
    practice = str(page.get('practice_name') or '').strip()
    return names.one_row_system(practice) or practice


def reason_of(page):
    kind = page.get('page_kind') or ''
    return 'medical' if kind in ('medical_record', 'bill') else kind if kind in dict(REASONS) else ''


def too_many_unread(unread, sections):
    """A few unread sections still make a useful quote; past 1 in 10 (or all of them) it would mislead."""
    return unread == sections or unread > max(1, sections // 10)


def fill_unread(results, chunks):
    """Each section the AI never answered becomes pages marked AI_FAILED, so they stay counted and
    show under their own Other pages row. Returns (results, sections unread, pages unread)."""
    filled, sections, pages = [], 0, 0
    for result, spec in zip(results, chunks):
        if result is None:
            sections += 1
            pages += spec['pages']
            result = {'careful': 'not_read', 'pages': [
                {'page': n, 'page_kind': AI_FAILED, 'practice_name': '', 'provider_name': '',
                 'is_bill': False, 'lines': []} for n in range(1, spec['pages'] + 1)]}
        filled.append(result)
    return filled, sections, pages


def new_group():
    return {'practices': [], 'doctors': [], 'pages': 0, 'lines': 0, 'where': {}}


def add_page(target, page, lines):
    """Counts the page, and remembers its file and page number for the Where Found tab."""
    target['pages'] += 1
    target['lines'] += lines
    if page.get('file') is not None:
        spot = target['where'].setdefault(page['file'], {'pages': [], 'lines': 0})
        spot['pages'].append(page.get('file_page'))
        spot['lines'] += lines


def group(pages):
    """[{page, page_kind, practice_name, provider_name, lines, file?, file_page?}] -> (providers,
    others). Matching uses the spelling rules in names.py; the strictest rule that fits wins,
    and when several providers fit equally the first one found takes the page. others holds one
    entry per reason that has pages, in REASONS order."""
    groups = []
    other = {key: new_group() for key, _ in REASONS}
    for page in pages:
        practice = practice_of(page)
        big = practice in {label for label, _ in names.ONE_ROW_SYSTEMS}
        doctor = '' if big else str(page.get('provider_name') or '').strip()
        lines = len(page.get('lines') or [])
        if not (names.name_tokens(practice) or names.person_tokens(doctor)):
            add_page(other[reason_of(page)], page, lines)
            continue
        found = []
        if practice:
            found, _ = names.find_matches([g['practices'] for g in groups], {'practice_name': practice})
        if not found and doctor:
            # A doctor seen first without a practice joins the practice when it turns up; a
            # practice row never takes over another practice's doctor.
            pool = [g['doctors'] if not (practice and g['practices']) else [] for g in groups]
            found, _ = names.find_matches(pool, {'provider_name': doctor})
        if found:
            target = groups[found[0]]
        else:
            target = new_group()
            groups.append(target)
        if practice and not any(p['practice_name'] == practice for p in target['practices']):
            target['practices'].append({'practice_name': practice})
        if doctor and not any(d['provider_name'] == doctor for d in target['doctors']):
            target['doctors'].append({'provider_name': doctor})
        add_page(target, page, lines)
    providers = [dict(g, name=display(g)) for g in groups]
    providers.sort(key=lambda p: (-p['pages'], -p['lines'], p['name'].casefold()))
    others = [dict(other[key], name=label) for key, label in REASONS if other[key]['pages']]
    return providers, others


def display(g):
    """The fullest spelling seen: the practice when there is one, else the doctor."""
    pool = g['practices'] or g['doctors']
    best = min(pool, key=names.fullness)
    return best.get('practice_name') or best.get('provider_name')


def rows(providers, others):
    return [[p['name'], p['pages'], p['lines']] for p in providers + others]


def page_list(numbers):
    """[1, 2, 3, 7] -> '1–3, 7'."""
    out, run = [], []
    for n in sorted(set(n for n in numbers if isinstance(n, int))):
        if run and n == run[-1] + 1:
            run.append(n)
            continue
        if run:
            out.append(str(run[0]) if len(run) == 1 else f'{run[0]}–{run[-1]}')
        run = [n]
    if run:
        out.append(str(run[0]) if len(run) == 1 else f'{run[0]}–{run[-1]}')
    return ', '.join(out)


def link(file_id):
    return f'https://drive.google.com/file/d/{file_id}/view'


def where_rows(providers, others, files):
    """Where Found: one row per provider (and per Other pages reason) per file, in quote order."""
    out = [WHERE_HEAD]
    for p in providers + others:
        practices = '; '.join(x['practice_name'] for x in p['practices'])
        doctors = '; '.join(x['provider_name'] for x in p['doctors'])
        for index in sorted(p['where']):
            spot, f = p['where'][index], files[index]
            out.append([p['name'], f['name'], page_list(spot['pages']), len(spot['pages']), spot['lines'],
                        practices, doctors, link(f['id']), f['id']])
    return out


def is_zip(mime, name):
    return mime in ('application/zip', 'application/x-zip-compressed') or str(name).lower().endswith('.zip')


def skipped_type(mime):
    """What to call a file the reader skipped. Imaging discs can bring thousands of files."""
    mime = mime or ''
    if mime == 'application/dicom':
        return 'Scan images (DICOM)'
    if mime.startswith('video/'):
        return 'Videos'
    if mime.startswith('image/'):
        return 'Pictures'
    if mime.startswith('application/vnd.google-apps.'):
        return 'Google files'
    if mime in ('application/x-dosexec', 'application/x-msdos-program', 'application/x-msi'):
        return 'Disc viewer programs'
    return 'Other files'


def files_rows(files, providers, others, skip_id=''):
    """Files Read: PDFs first, then each zip, then one row per type of other skipped file. `index`
    stays each file's place in `files`, which is what providers and others point at."""
    out = [FILES_HEAD]
    skipped, zips, kinds = [], [], {}
    for index, f in enumerate(files):
        if f['id'] == skip_id:
            continue
        if f['status'] == STATUS_NOT_PDF:
            if is_zip(f.get('mime'), f['name']):
                zips.append([f['name'], '', STATUS_NOT_PDF + ' – unzip it to include it', '', '', link(f['id']), f['id']])
            else:
                kind = skipped_type(f.get('mime'))
                kinds[kind] = kinds.get(kind, 0) + 1
            continue
        found = '; '.join(p['name'] for p in providers if index in p['where'])
        other = sum(len(o['where'][index]['pages']) for o in others if index in o['where'])
        out.append([f['name'], f['pages'], f['status'], found, other if f['status'] == STATUS_READ else '',
                    link(f['id']), f['id']])
    for kind in sorted(kinds):
        skipped.append([f'{kind}: {kinds[kind]:,} file' + ('' if kinds[kind] == 1 else 's'), '', STATUS_NOT_PDF, '', '', '', ''])
    return out + zips + skipped


def tab_requests(sheets, wanted):
    """sheets: the spreadsheet's sheets[].properties; wanted: {title: rows-of-values}. Adds a
    missing tab, or grows an existing one so the values fit. Returns (requests, existing titles)."""
    have = {s['title']: s for s in sheets}
    requests, existing = [], []
    for title, table in wanted.items():
        size = {'rowCount': max(len(table) + 10, 100), 'columnCount': max(len(table[0]), 10)}
        if title not in have:
            requests.append({'addSheet': {'properties': {'title': title,
                             'gridProperties': dict(size, frozenRowCount=1)}}})
            continue
        existing.append(title)
        grid = have[title].get('gridProperties', {})
        bigger = {k: v for k, v in size.items() if grid.get(k, 0) < v}
        if bigger:
            requests.append({'updateSheetProperties': {
                'properties': {'sheetId': have[title]['sheetId'], 'gridProperties': bigger},
                'fields': ','.join('gridProperties.' + k for k in bigger)}})
    return requests, existing


def find_layout(column_b):
    """column_b: column B from row 1 down. Returns {'totals', 'first', 'total'} as 1-based rows
    (the big numbers, the first provider row, the Total row), or None when this is not a quote sheet."""
    cells = [str(c).strip() for c in column_b]
    if HEADER not in cells:
        return None
    header = cells.index(HEADER)
    labels = [i for i in range(header) if cells[i] == TOTALS_LABEL]
    totals = [i for i in range(header + 1, len(cells)) if cells[i] == 'Total']
    if not labels or not totals:
        return None
    return {'totals': labels[-1] + 2, 'first': header + 2, 'total': totals[0] + 1}


def resize_requests(tab_id, total_row, wanted, first_row):
    """Grow or shrink the provider rows to `wanted` (at least 1), always inside the summed range so
    the template's SUM formulas stretch with it. Returns (requests, new total row)."""
    have = total_row - first_row
    wanted = max(wanted, 1)
    if wanted > have:
        # Insert above the last provider row: rows added inside a range widen its SUMs.
        start = total_row - 2
        return [{'insertDimension': {'range': {'sheetId': tab_id, 'dimension': 'ROWS', 'startIndex': start,
                                               'endIndex': start + wanted - have},
                                     'inheritFromBefore': True}}], first_row + wanted
    if wanted < have:
        return [{'deleteDimension': {'range': {'sheetId': tab_id, 'dimension': 'ROWS',
                                               'startIndex': first_row - 1 + wanted,
                                               'endIndex': total_row - 1}}}], first_row + wanted
    return [], total_row


def values(table, total_row, first_row):
    """Cells for the totals row (B:F) and B<first>:G<total>, formulas in the template's own form."""
    last = total_row - 1
    body = []
    for i in range(total_row - first_row):
        r = first_row + i
        name, pages, lines = table[i] if i < len(table) else ['', '', '']
        body.append([name, pages, f'=IFERROR(C{r}/$C${total_row}, 0)', lines,
                     f'=IFERROR(E{r}/$E${total_row}, 0)', f'=IFERROR(E{r}/C{r}, 0)'])
    body.append(['Total', f'=SUM(C{first_row}:C{last})', f'=SUM(D{first_row}:D{last})',
                 f'=SUM(E{first_row}:E{last})', f'=SUM(F{first_row}:F{last})',
                 f'=IFERROR(E{total_row}/C{total_row}, 0)'])
    # The template's COUNTA would count the Other pages rows as providers.
    totals = [f'=COUNTA(B{first_row}:B{last})-COUNTIF(B{first_row}:B{last}, "{OTHER}*")', '',
              f'=C{total_row}', '', f'=E{total_row}']
    return totals, body

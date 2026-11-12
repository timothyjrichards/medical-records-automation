"""Tests for the Records Helper rules. Run with:  python -m pytest src"""
from datetime import datetime, timezone

import logic

PDF = logic.PDF


def f(id_, name, mime=PDF, **kw):
    return dict(id=id_, name=name, mimeType=mime, delivery_folder='fold-a', delivery='Delivery 1',
                arrival='2025-01-02', plaintiff='', createdTime='2025-01-01T10:00:00Z', **kw)


def test_hand_numbers_are_read_but_dates_are_not():
    assert logic.leading_number('28 - Example Clinic.pdf') == '28'
    assert logic.leading_number('012 Records.pdf') == '12'
    assert logic.leading_number('12.1 Bill.pdf') == '12.1'
    assert logic.leading_number('08-12-2024 records.pdf') == ''
    assert logic.leading_number('2024-10-14 records.pdf') == ''
    assert logic.leading_number('Records.pdf') == ''


def test_years_are_not_file_numbers():
    assert logic.number_value('2024') is None
    assert logic.number_value('12.1') == 12
    assert logic.highest(['3', '2024', '', '11']) == 11


def test_one_plaintiff_or_several():
    assert logic.single_plaintiff('Jordan Sample') == 'Jordan Sample'
    assert logic.single_plaintiff('Jordan Sample and Casey Sample') == ''
    assert logic.single_plaintiff('Sample, et al') == ''


def test_dates_read_from_file_names():
    assert logic.name_date('Clinic 08-12-2024.pdf', 2025) == '08-12-2024'
    assert logic.name_date('Clinic 2024-08-12.pdf', 2025) == '08-12-2024'
    assert logic.name_date('Clinic 8.12.24.pdf', 2025) == '08-12-2024'
    assert logic.name_date('Clinic 01-01-2020 to 03-03-2021.pdf', 2025) == '01-01-2020 to 03-03-2021'


def test_dates_that_must_not_count():
    assert logic.name_date('DOB 04-05-1980 records.pdf', 2025) == ''      # a birth date
    assert logic.name_date('Claim 12-10-2024-0001.pdf', 2025) == ''       # part of a longer number
    assert logic.name_date('Clinic 13-45-2024.pdf', 2025) == ''           # impossible date


def test_file_name_names_a_known_provider():
    known = ['Lakeside Orthopedics', 'Lakeside Pediatrics', 'Riverbend Physical Therapy']
    assert logic.name_match('LAKESIDE_ORTHOPEDICS medical records.pdf', known) == 'Lakeside Orthopedics'
    assert logic.name_match('Lakeside records.pdf', known) == ''          # too vague: two Lakesides
    assert logic.name_match('Riverbend PT.pdf', known) == ''              # not every word: ask the AI


def test_suggested_name_is_safe_for_drive():
    assert logic.suggested(7, 'Clinic: A/B', False) == '7 - Clinic A B.pdf'
    assert logic.suggested(8, 'Clinic', True, '08-12-2024') == '8 - Clinic [Multiple] 08-12-2024.pdf'


def test_files_that_are_never_numbered():
    assert logic.skip_reason('photos.zip', 'application/zip') == logic.NOT_INDEXED
    assert logic.skip_reason('Records Index.pdf', PDF) == logic.NOT_INDEXED + " (sender's index)"
    assert logic.skip_reason('Clinic.pdf', PDF) == ''


def test_file_size_and_type():
    assert logic.file_size('2516582') == '2.4 MB'
    assert logic.file_size('') == ''
    assert logic.file_type('scan.jpg', 'image/jpeg') == 'Image'
    assert logic.file_type('notes.xyz', '') == 'XYZ'


def test_recent_folder_waits():
    now = datetime(2025, 1, 1, 12, tzinfo=timezone.utc)
    assert logic.is_recent([{'createdTime': '2025-01-01T11:00:00Z'}], now)
    assert not logic.is_recent([{'createdTime': '2025-01-01T08:00:00Z'}], now)


def test_rerun_adds_nothing_and_keeps_hand_edits():
    first, _ = logic.plan([], [f('x1', 'Clinic.pdf')], {'fold-a'})
    first[0][logic.COL['Notes']] = 'typed by a person'
    appends, updates = logic.plan(first, [f('x1', 'Clinic.pdf')], {'fold-a'})
    assert appends == []
    assert 'Notes' not in updates[2]


def test_removed_only_when_its_folder_was_read():
    rows, _ = logic.plan([], [f('x1', 'Clinic.pdf')], {'fold-a'})
    _, updates = logic.plan(rows, [], {'fold-a'})
    assert updates == {2: {'Notes': logic.REMOVED}}
    _, updates = logic.plan(rows, [], set())
    assert updates == {}


def test_ai_answer_is_checked():
    answer = {'from_a_provider': True, 'main_provider': 'Clinic', 'all_providers': ['Clinic', 'Lab'],
              'record_type': 'Records', 'plaintiff': 'Someone Else', 'confident': True}
    who, kind, plaintiff, multiple, flag = logic.from_reading(answer, 'Jordan Sample; Casey Sample')
    assert (who, kind, plaintiff, multiple, flag) == ('Clinic', 'Records', '', True, '')
    assert logic.from_reading(dict(answer, confident=False), '')[4] == 'unsure'


def test_hand_renamed_file_is_left_alone():
    rows_list, _ = logic.plan([], [f('x1', 'SCAN_1.pdf')], {'fold-a'})
    rows_list[0][logic.COL['Suggested name']] = "'4 - Clinic.pdf"
    rows = logic.rows_now(rows_list, [], {})
    assert [r[2] for r in logic.renames(rows, [f('x1', 'SCAN_1.pdf')])] == ['4 - Clinic.pdf']
    assert logic.renames(rows, [f('x1', 'Renamed by a person.pdf')]) == []


def test_repeated_hand_numbers_are_flagged():
    found = [f('x1', '5 - Clinic.pdf'), f('x2', '5 - Lab.pdf')]
    appends, _ = logic.plan([], found, {'fold-a'})
    rows = logic.rows_now([], appends, {})
    flags = logic.repeat_flags(rows, [], found)
    assert flags == {'x1': logic.LOOK + 'repeated number 5', 'x2': logic.LOOK + 'repeated number 5'}


def test_number_range():
    assert logic.number_range([11, 12, 13, 20]) == '11–13, 20'

"""Tests for the Case Quote rules. Run with:  python -m pytest src"""
import pytest

import names
import quote
import reader


def page(practice='', doctor='', kind='medical_record', lines=0, file=0, n=1):
    return {'page': n, 'page_kind': kind, 'practice_name': practice, 'provider_name': doctor,
            'lines': [{}] * lines, 'file': file, 'file_page': n}


def test_spellings_of_one_practice_are_one_provider():
    providers, _ = quote.group([page('Lakeside Orthopedics'), page('LAKESIDE ORTHOPEDICS, INC.'),
                                page('Lakeside Ortho')])          # a cut-off spelling
    assert [(p['name'], p['pages']) for p in providers] == [('Lakeside Orthopedics', 3)]


def test_different_practices_stay_apart():
    providers, _ = quote.group([page('Lakeside Orthopedics'), page('Lakeside Pediatrics')])
    assert len(providers) == 2


def test_doctor_seen_first_without_practice_joins_it():
    providers, _ = quote.group([page('', 'Pat Placeholder, DC'), page('Riverbend Physical Therapy', 'Pat Placeholder')])
    assert [(p['name'], p['pages']) for p in providers] == [('Riverbend Physical Therapy', 2)]


def test_big_health_system_is_one_row():
    providers, _ = quote.group([page('Example Health - North Clinic', 'Lee Sample, MD'),
                                page('Example Health South', 'Kim Sample, MD')])
    assert [(p['name'], p['pages']) for p in providers] == [('Example Health System', 2)]


def test_unowned_pages_go_to_other_rows_in_fixed_order():
    _, others = quote.group([page(kind='blank'), page(kind='legal_or_admin'), page(kind='medical_record')])
    assert [o['name'] for o in others] == [quote.OTHER + ' – legal & admin papers', quote.OTHER + ' – blank',
                                           quote.OTHER + ' – medical, provider not found']


def test_biggest_provider_first():
    providers, _ = quote.group([page('Small Clinic Group'), page('Big Hospital Center', lines=2),
                                page('Big Hospital Center')])
    assert [p['name'] for p in providers] == ['Big Hospital Center', 'Small Clinic Group']


def test_doctor_suffixes_and_order_do_not_matter():
    assert names.person_tokens('Example, Dana MD') == names.person_tokens('Dana Example, M.D.')


def test_skipped_page_is_still_counted():
    cleaned = reader.clean([{'page': 1, 'page_kind': 'bill', 'practice_name': 'X', 'provider_name': '',
                             'is_bill': True, 'lines': []}], 2)
    assert [p['page_kind'] for p in cleaned] == ['bill', quote.SKIPPED]


def test_page_outside_the_section_is_refused():
    with pytest.raises(ValueError):
        reader.clean([{'page': 9, 'lines': []}], 8)


def test_a_page_answered_twice_counts_once():
    p = {'page': 1, 'page_kind': 'blank', 'lines': []}
    assert len(reader.clean([p, p], 1)) == 1


def test_too_many_unread_sections():
    assert not quote.too_many_unread(1, 20)
    assert quote.too_many_unread(3, 20)
    assert quote.too_many_unread(2, 2)


def test_page_list_joins_runs():
    assert quote.page_list([1, 2, 3, 7]) == '1–3, 7'


def test_finds_the_layout_or_refuses():
    column_b = ['', 'Total Providers', '', 'Medical Provider', 'A', 'B', 'Total']
    assert quote.find_layout(column_b) == {'totals': 3, 'first': 5, 'total': 7}
    assert quote.find_layout(['Something else']) is None


def test_grows_inside_the_summed_range():
    requests, total = quote.resize_requests(0, total_row=7, wanted=4, first_row=5)
    assert total == 9
    assert requests[0]['insertDimension']['range']['startIndex'] == 5


def test_totals_ignore_other_pages_rows():
    totals, body = quote.values([['A', 3, 1]], total_row=6, first_row=5)
    assert body[-1][0] == 'Total'
    assert 'Other pages' in totals[0]

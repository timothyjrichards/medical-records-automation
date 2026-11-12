"""The AI step: Gemini (on Google Cloud Vertex AI) reads a section of pages and, for every page,
says what it is, whose it is, and lists each billing line on it.

Big files are cut into 8-page sections so each answer stays complete. A section that holds bills is
read a second time in "careful" mode. Every answer is checked by clean() before it is counted.
"""
import base64
import json
import os

import quote

PROJECT = os.environ.get('GCP_PROJECT_ID', '')
MODEL = os.environ.get('GEMINI_MODEL', '')
CHUNK_PAGES = 8

PROMPT = """These pages come from documents a law firm sent a medical expert before hiring them:
the medical records and medical bills of one or more providers, plus letters, subpoenas and
other papers. The expert prices the job from three numbers: providers, record pages, billing lines.

For EVERY page of this section return one entry, in page order:
- page: the page number within this section (1 = its first page).
- page_kind: what the page is:
  "medical_record": part of a patient's chart (visit or progress note, report, test or imaging
    result, intake or history form, prescription, therapy log);
  "bill": a bill, claim form, itemized statement or ledger (see is_bill);
  "legal_or_admin": an attorney's or records company's letter, a fax cover, a subpoena or
    notice, an index, a certificate or declaration of the custodian of records, an
    authorization, or any other paper that is not a medical record or bill;
  "blank": no content, or only a stamp, a page number or a separator;
  "unreadable": too faint, cut off or garbled to tell what it is.
- practice_name: the practice, clinic, hospital or facility whose record or bill this page is,
  from its letterhead, form header or billing provider. Empty for legal_or_admin, blank and
  unreadable pages, and for a medical record or bill you cannot tie to any provider.
- provider_name: the doctor or clinician named as the treating or billing provider on this page;
  empty when none is named.
- is_bill: true when the page is a bill, a claim form (CMS-1500, UB-04), an itemized statement or
  a ledger of charges.
- lines: every charge line printed on this page, top to bottom. A charge line is one service with
  an amount charged: CMS-1500 box 24 rows, UB-04 rows (form locator 42-47), itemized statement and
  ledger rows. Never a payment, adjustment, write-off, balance, subtotal or total line, and nothing
  from a page that is not a bill. For each line:
  - date_of_service: YYYY-MM-DD (the "from" date when a range is printed); empty if none.
  - code: the CPT, HCPCS or revenue code exactly as printed; empty when the line has none.
  - billed: the charge for the line as printed, digits and a decimal point only ("1234.50").

Read every row; the number of lines is checked against the bills. Never invent a line."""

LINE_KEYS = ['date_of_service', 'code', 'billed']
SCHEMA = {'type': 'OBJECT', 'required': ['pages'], 'properties': {'pages': {'type': 'ARRAY', 'items': {
    'type': 'OBJECT', 'required': ['page', 'page_kind', 'practice_name', 'provider_name', 'is_bill', 'lines'],
    'properties': {
        'page': {'type': 'INTEGER'},
        'page_kind': {'type': 'STRING', 'format': 'enum', 'enum': quote.KINDS},
        'practice_name': {'type': 'STRING'},
        'provider_name': {'type': 'STRING'}, 'is_bill': {'type': 'BOOLEAN'},
        'lines': {'type': 'ARRAY', 'items': {'type': 'OBJECT', 'required': LINE_KEYS,
                  'properties': {k: {'type': 'STRING'} for k in LINE_KEYS}}}}}}}}


def ask(session, pdf, careful):
    """session: an authorised requests session (google.auth AuthorizedSession)."""
    url = (f'https://aiplatform.googleapis.com/v1/projects/{PROJECT}/locations/global/'
           f'publishers/google/models/{MODEL}:generateContent')
    config = {'responseMimeType': 'application/json', 'responseSchema': SCHEMA, 'maxOutputTokens': 32768,
              'thinkingConfig': {'thinkingLevel': 'HIGH' if careful else 'LOW'}}
    if careful:
        config['mediaResolution'] = 'MEDIA_RESOLUTION_HIGH'
    response = session.post(url, timeout=420, json={
        'systemInstruction': {'parts': [{'text': PROMPT}]},
        'contents': [{'role': 'user', 'parts': [{'text': 'Read every page.'},
            {'inlineData': {'mimeType': 'application/pdf', 'data': base64.b64encode(pdf).decode('ascii')}}]}],
        'generationConfig': config})
    response.raise_for_status()
    candidates = response.json().get('candidates', [])
    if not candidates or candidates[0].get('finishReason') != 'STOP':
        raise ValueError('incomplete answer')
    return json.loads(''.join(p.get('text', '') for p in candidates[0]['content']['parts']
                              if not p.get('thought')))['pages']


def clean(answer, count):
    """The AI's pages, checked: right shapes, page numbers inside the section, one entry a page.
    A page the AI skipped becomes an empty entry marked 'skipped', so it is still counted and lands
    on its own Other pages row instead of silently disappearing."""
    if not isinstance(answer, list):
        raise ValueError('not_a_list')
    kept = {}
    for p in answer:
        if not isinstance(p, dict) or type(p.get('page')) is not int or not 1 <= p['page'] <= count:
            raise ValueError('bad_page')
        lines = p.get('lines') or []
        if not isinstance(lines, list) or len(lines) > 200:
            raise ValueError('bad_lines')
        if any(not isinstance(l, dict) or any(not isinstance(l.get(k, ''), str) for k in LINE_KEYS)
               for l in lines):
            raise ValueError('bad_line')
        kind = p.get('page_kind')
        kept.setdefault(p['page'], {
            'page': p['page'], 'page_kind': kind if kind in quote.KINDS else '',
            'practice_name': str(p.get('practice_name') or '')[:200],
            'provider_name': str(p.get('provider_name') or '')[:200],
            'is_bill': p.get('is_bill') is True,
            'lines': [{k: l.get(k, '')[:60] for k in LINE_KEYS} for l in lines]})
    return [kept.get(n, {'page': n, 'page_kind': quote.SKIPPED, 'practice_name': '', 'provider_name': '',
                         'is_bill': False, 'lines': []})
            for n in range(1, count + 1)]

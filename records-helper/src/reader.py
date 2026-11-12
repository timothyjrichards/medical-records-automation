"""The AI step: Gemini (on Google Cloud Vertex AI) reads the first pages of one PDF and says who it is from.

Only the first few pages are sent, which keeps each read small and fast. The answer comes back as
JSON in a fixed shape (SCHEMA), so the code never has to parse free text.
"""
import base64
import io
import json
import os

from pypdf import PdfReader, PdfWriter

PROJECT = os.environ.get('GCP_PROJECT_ID', '')
MODEL = os.environ.get('GEMINI_MODEL', '')
GEMINI_URL = (f'https://aiplatform.googleapis.com/v1/projects/{PROJECT}/locations/global/'
              f'publishers/google/models/{MODEL}:generateContent')
READ_PAGES = 3
MAX_READ_BYTES = 500 * 1024 * 1024

PROMPT = """You read the first pages of one PDF from a delivery of case records sent to a medical expert.
Say who the PDF is from: the medical provider (practice, hospital, clinic or facility) whose records
or bills these are. Use the practice or facility name, not a person, unless it is a doctor with no
practice name. Copy services and law firms that sent the records on are never the provider: name the
provider whose records they are. If a provider matches one in KNOWN PROVIDERS, use exactly that
spelling. If the pages hold several providers' records, main_provider is the one with the most pages
and all_providers lists every one.
If the PDF is not a medical provider's records or bills (interrogatories, a deposition transcript, a
police report, a letter), set from_a_provider false and give document_type as a short plain title
in Title Case, for example "Interrogatories".
record_type is one of the listed values. plaintiff: which name in PLAINTIFFS the pages are about,
copied exactly from that list; empty when there is no list or you cannot tell.
confident is false when the pages are blank, unreadable, or you would be guessing."""

SCHEMA = {'type': 'OBJECT', 'properties': {
    'from_a_provider': {'type': 'BOOLEAN'}, 'main_provider': {'type': 'STRING'},
    'all_providers': {'type': 'ARRAY', 'items': {'type': 'STRING'}}, 'document_type': {'type': 'STRING'},
    'record_type': {'type': 'STRING', 'enum': ['Records', 'Bills', 'Records and Bills', 'Imaging', 'Legal', 'Other']},
    'plaintiff': {'type': 'STRING'}, 'confident': {'type': 'BOOLEAN'}},
    'required': ['from_a_provider', 'main_provider', 'all_providers', 'document_type', 'record_type',
                 'plaintiff', 'confident']}


def first_pages(data, size=0):
    """The PDF's first few pages as a small PDF, or (None, reason) when it cannot be read."""
    if int(size or 0) > MAX_READ_BYTES:
        return None, 'too big to read, name by hand'
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                if not reader.decrypt(''):
                    return None, 'locked PDF, replace with an unlocked copy'
            except Exception:
                return None, 'locked PDF, replace with an unlocked copy'
        writer = PdfWriter()
        for page in reader.pages[:READ_PAGES]:
            writer.add_page(page)
        out = io.BytesIO()
        writer.write(out)
    except Exception:
        return None, 'unreadable PDF'
    return out.getvalue(), ''


def ask_gemini(session, pdf, known_providers, plaintiffs):
    """session: an authorised requests session (google.auth AuthorizedSession)."""
    instruction = ('KNOWN PROVIDERS:\n' + ('\n'.join(known_providers[:200]) or '(none)') +
                   '\n\nPLAINTIFFS: ' + (plaintiffs or '(no list)'))
    response = session.post(GEMINI_URL, timeout=300, json={
        'systemInstruction': {'parts': [{'text': PROMPT}]},
        'contents': [{'role': 'user', 'parts': [{'text': instruction},
            {'inlineData': {'mimeType': 'application/pdf', 'data': base64.b64encode(pdf).decode('ascii')}}]}],
        'generationConfig': {'responseMimeType': 'application/json', 'responseSchema': SCHEMA,
                             'maxOutputTokens': 4096}})
    response.raise_for_status()
    candidates = response.json().get('candidates', [])
    if not candidates or candidates[0].get('finishReason') != 'STOP':
        raise ValueError('incomplete answer')
    return json.loads(''.join(p.get('text', '') for p in candidates[0]['content']['parts'] if not p.get('thought')))

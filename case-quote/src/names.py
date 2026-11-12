"""Provider-name rules: decide when two spellings are the same practice or the same doctor.

Records arrive with the same provider spelled many ways ("Lakeside Ortho", "LAKESIDE ORTHOPEDICS, INC.",
"Lakeside Orthopaedic Ctr"). These rules collapse them, strictest rule first, so a quote never
counts one provider twice."""
import re
import unicodedata


def norm(value):
    return re.sub(r'[^a-z0-9]', '', unicodedata.normalize('NFKD', str(value)).casefold())

def person_key(value):
    value = str(value).strip()
    # Strip professional suffixes, not initials within the actual name.
    suffix = r"(?:,?\s+|,)(?:m\.?\s*d\.?|d\.?\s*o\.?|ph\.?\s*d\.?|m\.?\s*b\.?\s*b\.?\s*s\.?|np|pa-c|pa|rn|dc|dds|dpm|facs)\s*$"
    while re.search(suffix, value, re.I):
        value = re.sub(suffix, '', value, flags=re.I).strip(' ,')
    if ',' in value:
        parts = [p.strip() for p in value.split(',')]
        if len(parts) == 2 and all(parts):
            value = parts[1] + ' ' + parts[0]
    return norm(value)

def practice_key(value):
    tokens = re.findall(r'[a-z0-9]+', unicodedata.normalize('NFKD', str(value)).casefold())
    replacements = {'hosp': 'hospital', 'ctr': 'center', 'centre': 'center',
                    'assoc': 'associates', 'med': 'medical'}
    while tokens and tokens[-1] in {'llc', 'inc', 'pc', 'pllc', 'corp'}:
        tokens.pop()
    return ''.join(replacements.get(t, t) for t in tokens)

def matches(a, b):
    # Location and NPI variations do not define a new practice/doctor identity.
    if practice_key(a.get('practice_name', '')) != practice_key(b.get('practice_name', '')):
        return False
    if person_key(a.get('provider_name', '')) != person_key(b.get('provider_name', '')):
        return False
    return bool(practice_key(a.get('practice_name', '')) or person_key(a.get('provider_name', '')))

ABBREVIATIONS = {'hosp': 'hospital', 'ctr': 'center', 'ct': 'center', 'centre': 'center',
                 'assoc': 'associates', 'med': 'medical', 'chiro': 'chiropractic', 'sx': 'surgery',
                 'surg': 'surgery', 'surger': 'surgery', 'sc': 'surgerycenter', 'grp': 'group'}

ENDINGS = {'llc', 'inc', 'pc', 'pllc', 'corp', 'md', 'do', 'dc', 'phd', 'pa', 'pac', 'np', 'dds',
           'dpm', 'rn', 'facs', 'mbbs'}

def words(value):
    found = re.findall(r'[a-z0-9]+', unicodedata.normalize('NFKD', str(value)).casefold())
    joined = []  # "P.C." and "M.D." arrive as single letters; rejoin runs of them
    for w in found:
        if len(w) == 1 and joined and joined[-1][1]:
            joined[-1] = (joined[-1][0] + w, True)
        else:
            joined.append((w, len(w) == 1))
    found = [w for w, _ in joined]
    while found and found[-1] in ENDINGS:
        found.pop()
    if found and found[0] in ('dr', 'doctor'):
        found = found[1:]
    return found

def name_tokens(value):
    """A practice name in the form every spelling of it shares."""
    found = [ABBREVIATIONS.get(w, w) for w in words(value)
             if len(w) > 1 and w not in ('and', 'the')]   # middle initials go
    return [w[:-1] if len(w) > 4 and w.endswith('s') and not w.endswith('ss') else w for w in found]

def person_tokens(value):
    """A doctor's name: credentials and middle initials go, a first initial stays
    ("J Example" is not assumed to be "Jane Example")."""
    value = str(value).strip()
    suffix = r"(?:,?\s+|,)(?:m\.?\s*d\.?|d\.?\s*o\.?|ph\.?\s*d\.?|m\.?\s*b\.?\s*b\.?\s*s\.?|np|pa-c|pa|rn|dc|dds|dpm|facs)\s*$"
    while re.search(suffix, value, re.I):
        value = re.sub(suffix, '', value, flags=re.I).strip(' ,')
    if ',' in value:
        parts = [p.strip() for p in value.split(',')]
        if len(parts) == 2 and all(parts):
            value = parts[1] + ' ' + parts[0]
    found = words(value)
    return found[:1] + [w for w in found[1:] if len(w) > 1]

def same_names(a, b):
    practice = name_tokens(a.get('practice_name', ''))
    doctor = person_tokens(a.get('provider_name', ''))
    return bool(practice or doctor) and ''.join(practice) == ''.join(name_tokens(b.get('practice_name', ''))) \
        and ''.join(doctor) == ''.join(person_tokens(b.get('provider_name', '')))

def shortened(full, short):
    """True when `short` is a cut-off ("...SURGER") or surname-only ("Smith") form of `full`."""
    if len(short) >= 2:
        return len(''.join(full)) > len(''.join(short)) and ''.join(full).startswith(''.join(short))
    return len(short) == 1 and len(full) >= 2 and full[-1] == short[0]

def similar_names(known, incoming):
    """Only the incoming record may be the less complete one, so a short name can never pull
    two different full names together."""
    kp, ip = name_tokens(known.get('practice_name', '')), name_tokens(incoming.get('practice_name', ''))
    kd, idr = person_tokens(known.get('provider_name', '')), person_tokens(incoming.get('provider_name', ''))
    if not (kd or idr):
        # Two practice-only records with one organisation NPI and the same first word.
        npi = norm(known.get('npi', ''))
        if npi and npi == norm(incoming.get('npi', '')) and kp and ip and kp[0] == ip[0]:
            return True
    if not (''.join(kd) == ''.join(idr) or (idr and shortened(kd, idr))):
        return False
    if ''.join(kp) == ''.join(ip):
        return bool(kp or kd)
    if not ip:
        return bool(idr)   # the same doctor, found without a practice
    return shortened(kp, ip)

MATCH_RULES = [('exact', matches), ('same spelling', same_names), ('similar', similar_names)]

def find_matches(pool, incoming):
    """pool: the known spellings of each existing record. Returns (indexes, rule). The strictest
    rule that finds anything wins, so a rerun on an older sheet keeps choosing the row it chose
    before. Several indexes means unsure: the caller keeps the record apart and flags it."""
    for rule, fits in MATCH_RULES:
        found = [i for i, spellings in enumerate(pool) if any(fits(s, incoming) for s in spellings)]
        if found:
            return found, rule
    return [], ''

def fullness(provider):
    return -len(''.join(name_tokens(provider.get('practice_name', '')) +
                        person_tokens(provider.get('provider_name', ''))))

# Large health systems whose many clinics and doctors should count as ONE provider row.
# Edit for your region: (display name, pattern matched against the practice name).
ONE_ROW_SYSTEMS = [
    ('Example Health System', re.compile(r'\bexample health\b', re.I)),
]


def one_row_system(practice):
    """The display name of the big system this practice belongs to, or ''."""
    return next((label for label, pattern in ONE_ROW_SYSTEMS if pattern.search(practice or '')), '')

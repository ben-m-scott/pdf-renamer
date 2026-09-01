# v260830.1
# Installable via uv tool; runs as `pdf_renamer` from any folder.
# v260830
# Removed review detection and suffixing, as it was causing issues with some journals and is not essential for the renaming process.
# Also added some additional journal terms to the detection list.
import difflib
import glob
import os
import re
import sys
from datetime import datetime

import pdfplumber
import requests
from PyPDF2 import PdfReader

# ============================================================
# CONFIGURATION
# ============================================================

CURRENT_YEAR = datetime.now().year

MIN_YEAR = 1900
MAX_YEAR = CURRENT_YEAR

AUTHOR_CONFIDENCE_THRESHOLD = 0.68

# Minimum similarity between a locally detected title and
# CrossRef before considering them to be the same title.
CROSSREF_TITLE_SIMILARITY = 0.65

# Maximum vertical distance between lines that may form
# a multi-line title.
TITLE_LINE_DISTANCE = 28

# Maximum difference in average font size for two lines
# to be considered part of the same title.
TITLE_FONT_SIZE_TOLERANCE = 1.5

# Maximum number of lines merged into one title. Real titles
# often wrap over three or four printed lines.
MAX_TITLE_LINES = 4

# Minimum font size for a line to count as large print. Large
# print lines are allowed to be title fragments; body text is not.
STRONG_TITLE_FONT_SIZE = 12

# Maximum length of the title part of the filename. Keeps the full
# filename inside the 255-character component limit on Windows and
# macOS, with headroom for the folder path.
MAX_TITLE_CHARS = 150

PDF_EXTENSIONS = {'.pdf'}

# Upper bound on the numeric suffix appended when a target
# filename is already taken.
MAX_NAME_SUFFIX = 999


# ============================================================
# GENERAL UTILITIES
# ============================================================

def clean_filename(text):

    if not text:
        return ""

    text = re.sub(r'[<>:"/\\|?*]', ' ', text)
    text = re.sub(r'\s+', ' ', text)

    # Superscript signs detach from their words in PDF layout
    # ('Gzmk⁺' becomes '⁺ A feed-forward ... Gzmk CD8').
    text = text.replace('⁺', '+').replace('⁻', '-')

    return text.strip().strip('+-').strip().rstrip('.')


def truncate_title(text):

    if not text:
        return ""

    if len(text) <= MAX_TITLE_CHARS:
        return text

    cut = text[:MAX_TITLE_CHARS]

    if ' ' in cut:
        cut = cut.rsplit(' ', 1)[0]

    return cut.rstrip(' ,;:-')


def normalize_text(text):

    if not text:
        return ""

    text = text.lower()

    replacements = {
        'ﬁ': 'fi',
        'ﬂ': 'fl',
        'ﬀ': 'ff',
        'ﬃ': 'ffi',
        'ﬄ': 'ffl'
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'[^\w\s]', '', text)

    return text.strip()


def stripped_marker_text(text):

    if not text:
        return ""

    return normalize_text(text).replace('+', '').replace(' ', '')


def differs_only_by_marker_punctuation(a, b):

    if not a or not b:
        return False

    return (
        stripped_marker_text(a)
        == stripped_marker_text(b)
    )


def similarity(a, b):

    if not a or not b:
        return 0.0

    return difflib.SequenceMatcher(
        None,
        normalize_text(a),
        normalize_text(b)
    ).ratio()


# ============================================================
# YEAR HANDLING
# ============================================================

def valid_year(year):

    try:
        year = int(year)
    except (ValueError, TypeError):
        return None

    if MIN_YEAR <= year <= MAX_YEAR:
        return year

    return None


def years_in_text(text):

    if not text:
        return []

    results = []

    for match in re.findall(
        r'\b(19\d{2}|20\d{2})\b',
        text
    ):

        year = valid_year(match)

        if year:
            results.append(year)

    return results


# ============================================================
# PDF METADATA
# ============================================================

def extract_metadata(pdf_path):

    try:

        reader = PdfReader(pdf_path)

        meta = reader.metadata or {}

        title = clean_pdf_metadata_value(
            meta.get('/Title')
        )
        author = clean_pdf_metadata_value(
            meta.get('/Author')
        )

        year = None

        creation_date = meta.get('/CreationDate')

        if creation_date:

            for match in re.findall(
                r'(19\d{2}|20\d{2})',
                str(creation_date)
            ):

                candidate = valid_year(match)

                if candidate:
                    year = candidate
                    break

        return title, author, year

    except Exception:

        return None, None, None


def clean_pdf_metadata_value(value):

    if value is None:
        return None

    if isinstance(value, bytes):
        try:
            value = value.decode(
                'utf-8',
                errors='replace'
            )
        except Exception:
            return None

    text = str(value).strip()

    return text or None


# ============================================================
# FIRST PAGE EXTRACTION
# ============================================================

def extract_first_page(pdf_path):

    try:

        with pdfplumber.open(pdf_path) as pdf:

            if not pdf.pages:
                return [], None

            page = pdf.pages[0]

            words = page.extract_words(
                x_tolerance=2,
                y_tolerance=3,
                keep_blank_chars=False,
                use_text_flow=False,
                extra_attrs=[
                    'size',
                    'fontname'
                ]
            )

            return words, page

    except Exception:

        return [], None


# ============================================================
# RECONSTRUCT LINES FROM PDF WORDS
# ============================================================

def group_words_into_lines(words):

    if not words:
        return []

    words = sorted(
        words,
        key=lambda w: (
            float(w.get('top', 0)),
            float(w.get('x0', 0))
        )
    )

    lines = []

    for word in words:

        top = float(
            word.get('top', 0)
        )

        placed = False

        for line in lines:

            if abs(top - line['top']) <= 3.5:

                line['words'].append(word)

                line['top'] = sum(
                    float(
                        w.get('top', 0)
                    )
                    for w in line['words']
                ) / len(line['words'])

                placed = True
                break

        if not placed:

            lines.append({
                'top': top,
                'words': [word]
            })

    for line in lines:

        line['words'].sort(
            key=lambda w: float(
                w.get('x0', 0)
            )
        )

        line['text'] = ' '.join(
            w.get('text', '')
            for w in line['words']
        ).strip()

        line['bottom'] = max(
            float(
                w.get(
                    'bottom',
                    line['top']
                )
            )
            for w in line['words']
        )

        line['x0'] = min(
            float(
                w.get('x0', 0)
            )
            for w in line['words']
        )

        line['x1'] = max(
            float(
                w.get('x1', 0)
            )
            for w in line['words']
        )

        sizes = []

        for word in line['words']:

            try:

                size = float(
                    word.get(
                        'size',
                        0
                    )
                )

                if size > 0:
                    sizes.append(size)

            except (
                ValueError,
                TypeError
            ):
                pass

        if sizes:

            line['font_size'] = (
                sum(sizes) / len(sizes)
            )

            line['max_font_size'] = max(
                sizes
            )

            line['min_font_size'] = min(
                sizes
            )

        else:

            line['font_size'] = None
            line['max_font_size'] = None
            line['min_font_size'] = None

    lines.sort(
        key=lambda x: x['top']
    )

    return lines


# ============================================================
# BIO RXIV DETECTION
# ============================================================

def is_biorxiv_preprint(
    first_page_text,
    doi=None
):

    if not first_page_text:
        return False

    text = first_page_text.lower()

    explicit_patterns = [

        r'\bbiorxiv\b',

        r'\bbio\s*rxiv\b',

        r'\bbio\s*archive\b',

        r'\bbiorxiv\s+preprint\b',

        r'\bthis article is a preprint\b',

        r'\bpreprint\b.*\bbiorxiv\b',

        r'\bbiorxiv\b.*\bpreprint\b'
    ]

    for pattern in explicit_patterns:

        if re.search(
            pattern,
            text,
            re.IGNORECASE
        ):
            return True

    if doi:

        doi_lower = doi.lower().strip()

        if (
            doi_lower.startswith('10.1101/')
            or 'biorxiv' in doi_lower
        ):
            return True

    return False


# ============================================================
# BIO RXIV / PUBLISHER HEADER REJECTION
# ============================================================

def is_biorxiv_header_or_license(text):

    if not text:
        return False

    lower = text.lower()

    forbidden_phrases = [

        'made available under',

        'cc-by-nc',

        'cc by nc',

        'cc-by',

        'creative commons',

        'international license',

        'license',

        'biorxiv preprint',

        'biorxiv preprint doi',

        'this version posted',

        'this preprint',

        'not certified by peer review',

        'the copyright holder',

        'copyright holder',

        'preprint doi',

        'which was not certified by peer review'
    ]

    for phrase in forbidden_phrases:

        if phrase in lower:
            return True

    return False


# ============================================================
# JOURNAL / PUBLISHER DETECTION
# ============================================================

# Multi-word phrases are specific, so substring matching is safe.
JOURNAL_PHRASES = [

    'taylor & francis',
    'american chemical society',
    'royal society',
    'academic press',
    'biomed central',
    'journal of',
    'proceedings of',
    'annals of',
    'letters in',
    'reviews in',
    'current opinion in',
    'scientific reports',
    'communications biology',
    'communications chemistry',
    'communications medicine',
    'international journal of',
    'european journal of',
    'american journal of',
    'british journal of',
    'transactions of',
    'advances in'
]

# Single words are only matched with word boundaries. Without
# them, 'cell' matches 'cells', 'sage' matches 'dosage', and
# 'acs' matches 'surfaces'.
#
# Generic words are not in this list: nature, science, cell,
# oxford, cambridge, sage, and acs. They also appear inside
# genuine titles ('T cell', 'the nature of', 'data science').
# Journal header lines that contain them also carry volume,
# page, or year numbers, and the numeric checks in
# looks_like_journal_header catch those lines.
JOURNAL_WORDS = [

    'plos',
    'elsevier',
    'springer',
    'wiley',
    'frontiers',
    'mdpi',
    'bentham'
]


def looks_like_journal_header(text):

    if not text:
        return False

    lower = text.lower().strip()

    for term in JOURNAL_PHRASES:

        if term in lower:
            return True

    for term in JOURNAL_WORDS:

        if re.search(rf'\b{re.escape(term)}\b', lower):
            return True

    if re.search(
        r'\bjournal\b',
        lower
    ):
        return True

    if re.search(
        r'\bvol(?:ume)?\.?\s*\d+',
        lower
    ):
        return True

    if re.search(
        r'\b\d{1,4}\s*\(\d{1,4}\)',
        lower
    ):
        return True

    return False


def looks_like_publisher_line(text):

    if not text:
        return False

    lower = text.lower()

    terms = [

        'published by',
        'copyright',
        'open access',
        'creative commons',
        'available online',
        'online version',
        'publisher',
        'editorial'
    ]

    return any(
        term in lower
        for term in terms
    )


# ============================================================
# AFFILIATION DETECTION
# ============================================================

AFFILIATION_TERMS = [

    'university',
    'department',
    'institute',
    'laboratory',
    'laboratories',
    'lab',
    'school',
    'faculty',
    'hospital',
    'centre',
    'center',
    'college',
    'academy',
    'research',
    'division',
    'program',
    'programme',
    'campus',
    'email',
    'corresponding author',
    'correspondence',
    'address'
]


def affiliation_score(text):

    if not text:
        return 0

    lower = text.lower()

    hits = sum(
        1
        for term in AFFILIATION_TERMS
        if term in lower
    )

    return min(
        hits / 3,
        1.0
    )


def looks_like_affiliation(text):

    if not text:
        return False

    lower = text.lower()

    strong_terms = [

        'university',
        'department',
        'institute',
        'laboratory',
        'faculty',
        'hospital',
        'corresponding author',
        'correspondence',
        'email'
    ]

    if any(
        term in lower
        for term in strong_terms
    ):
        return True

    return affiliation_score(text) >= 0.67


# ============================================================
# ARTICLE STRUCTURE DETECTION
# ============================================================

def is_article_boundary(text):

    if not text:
        return False

    lower = text.lower().strip()

    boundary_patterns = [

        r'^abstract\b',
        r'^keywords?\b',
        r'^key words?\b',
        r'^introduction\b',
        r'^background\b',
        r'^materials and methods\b',
        r'^methods\b',
        r'^results\b',
        r'^discussion\b',
        r'^references\b',
        r'^bibliography\b',
        r'^supplementary\b'
    ]

    return any(
        re.search(
            pattern,
            lower
        )
        for pattern in boundary_patterns
    )


# ============================================================
# KEYWORD DETECTION
# ============================================================

def is_keyword_line(text):

    if not text:
        return False

    lower = text.strip().lower()

    return bool(
        re.match(
            r'^(keywords?|key\s+words?)\b',
            lower
        )
    )


# ============================================================
# SCIENTIFIC TITLE LANGUAGE
# ============================================================

SCIENTIFIC_TERMS = {

    'analysis',
    'analyses',
    'characterization',
    'characterisation',
    'engineering',
    'engineered',
    'design',
    'designed',
    'development',
    'developed',
    'identification',
    'expression',
    'production',
    'synthesis',
    'genome',
    'genomic',
    'protein',
    'proteins',
    'enzyme',
    'enzymes',
    'cell',
    'cells',
    'yeast',
    'bacterial',
    'bacteria',
    'plant',
    'plants',
    'microbial',
    'microbe',
    'microbes',
    'metabolic',
    'metabolism',
    'pathway',
    'pathways',
    'cloning',
    'assembly',
    'system',
    'systems',
    'method',
    'methods',
    'technology',
    'technologies',
    'biosynthesis',
    'fermentation',
    'biological',
    'biotechnology',
    'biomanufacturing',
    'sequence',
    'sequences',
    'sequencing',
    'mutation',
    'mutations',
    'variant',
    'variants',
    'genetic',
    'genetics',
    'cultivation',
    'activity',
    'binding',
    'structure',
    'structures',
    'evolution',
    'evolutionary',
    'screening',
    'high-throughput',
    'highthroughput',
    'synthetic',
    'metabolite',
    'metabolites',
    'molecular',
    'cellular',
    'organism',
    'organisms',
    'recombinant',
    'crystallization',
    'spectrometry',
    'chromatography',
    'assay',
    'assays',
    'phenotype',
    'phenotypic',
    'genotype',
    'genotypic'
}


def scientific_term_count(text):

    if not text:
        return 0

    words = re.findall(
        r"[A-Za-zÀ-ÖØ-öø-ÿ'-]+",
        text.lower()
    )

    return sum(
        1
        for word in words
        if word in SCIENTIFIC_TERMS
    )


# ============================================================
# TITLE DETECTION
# ============================================================

def looks_like_title(text):

    if not text:
        return False

    text = re.sub(
        r'\s+',
        ' ',
        text
    ).strip()

    if len(text) < 15:
        return False

    if len(text) > 300:
        return False

    lower = text.lower()

    # --------------------------------------------------------
    # Explicitly reject bioRxiv header/license material.
    # --------------------------------------------------------

    if is_biorxiv_header_or_license(text):
        return False

    # --------------------------------------------------------
    # Explicitly reject article structure.
    # --------------------------------------------------------

    if is_keyword_line(text):
        return False

    if is_article_boundary(text):
        return False

    # --------------------------------------------------------
    # Reject obvious metadata.
    # --------------------------------------------------------

    rejected_prefixes = [

        'doi:',
        'doi ',
        'http://',
        'https://',
        'www.',
        'received',
        'accepted',
        'published',
        'copyright',
        'preprint',
        'abstract',
        'keywords',
        'key words',
        'acknowledg'
    ]

    if any(
        lower.startswith(prefix)
        for prefix in rejected_prefixes
    ):
        return False

    if looks_like_journal_header(text):
        return False

    if looks_like_publisher_line(text):
        return False

    if looks_like_affiliation(text):
        return False

    if '@' in text:
        return False

    if re.match(
        r'^\d+\b',
        text
    ):
        remainder = text.split(
            None,
            1
        )

        if len(remainder) == 1 or len(remainder[1].split()) < 3:
            return False

    # --------------------------------------------------------
    # Reject DOI-like text.
    # --------------------------------------------------------

    if re.search(
        r'10\.\d{4,9}/',
        text,
        re.IGNORECASE
    ):
        return False

    # --------------------------------------------------------
    # Reject lines dominated by numbers.
    # --------------------------------------------------------

    digits = sum(
        c.isdigit()
        for c in text
    )

    if digits > len(text) * 0.15:
        return False

    # --------------------------------------------------------
    # Reject likely author blocks.
    # --------------------------------------------------------

    if looks_like_single_person(text):
        return False

    # --------------------------------------------------------
    # Scientific-language requirement.
    #
    # A valid title should normally contain either:
    #   1. scientific terminology, or
    #   2. a recognizable scientific/title structure.
    # --------------------------------------------------------

    scientific_hits = scientific_term_count(
        text
    )

    words = text.split()

    if scientific_hits == 0:

        title_structures = [

            r'\bof\b',
            r'\bin\b',
            r'\bfor\b',
            r'\bwith\b',
            r'\busing\b',
            r'\bfrom\b',
            r'\bthrough\b',
            r'\bvia\b',
            r'\bby\b',
            r'\bbetween\b',
            r'\bunder\b',
            r'\bwithout\b',
            r':',
            # Verb-structured titles ('X reveals Y',
            # 'Gene drives Z') carry no preposition, so the
            # preposition list above scores them zero.
            r'\b(reveals?|uncovers?|identifies|drives?|'
            r'regulates?|controls?|mediates?|shapes?|'
            r'enables?|improves?|requires?|defines?|'
            r'links?|governs?|promotes?|suppresses?|'
            r'inhibits?|activates?|targets?|determines?|'
            r'maps?|predicts?|explains?|limits?|'
            r'facilitates?|orchestrates?|impairs?|'
            r'protects?|induces?|shapes?)\b'
        ]

        structure_hits = sum(
            bool(
                re.search(
                    pattern,
                    lower
                )
            )
            for pattern in title_structures
        )

        if structure_hits < 1:
            return False

    if len(words) < 4:
        return False

    if len(words) > 45:
        return False

    return True


def title_score(
    text,
    index,
    font_size=None,
    max_font_size=None
):

    if not looks_like_title(text):
        return -999

    words = text.split()

    score = 0.0

    # Position on first page.
    if index <= 2:
        score += 6

    elif index <= 5:
        score += 4

    elif index <= 10:
        score += 2

    # Reasonable title length.
    if 6 <= len(words) <= 25:
        score += 5

    elif 4 <= len(words) <= 35:
        score += 2

    # Scientific language.
    score += min(
        scientific_term_count(text) * 1.5,
        7
    )

    # Colon is common in scientific titles.
    if ':' in text:
        score += 1

    # Font size is a useful signal.
    if font_size:

        if font_size >= 16:
            score += 4

        elif font_size >= 14:
            score += 3

        elif font_size >= 12:
            score += 1

    # A line with mixed font sizes is less likely to be
    # a clean title.
    if (
        font_size
        and max_font_size
        and max_font_size - font_size > 2
    ):
        score -= 1

    return score


# ============================================================
# MULTI-LINE TITLE DETECTION
# ============================================================

def compatible_title_lines(first, second):

    if not first or not second:
        return False

    distance = (
        second['top']
        - first['bottom']
    )

    if distance < -2:
        return False

    if distance > TITLE_LINE_DISTANCE:
        return False

    size1 = first.get(
        'font_size'
    )

    size2 = second.get(
        'font_size'
    )

    if size1 and size2:

        # Tolerance grows with font size. Large print lines can
        # differ by a point or two and still be one title (a
        # 22pt line over a 20pt line), while body text must
        # stay within the base tolerance.
        tolerance = max(
            TITLE_FONT_SIZE_TOLERANCE,
            0.12 * max(size1, size2)
        )

        if abs(size1 - size2) > tolerance:
            return False

    return True


def strong_title_font(line):

    size = line.get('font_size')

    return bool(
        size
        and size >= STRONG_TITLE_FONT_SIZE
    )


def line_title_score(line, index):

    score = title_score(
        line['text'],
        index,
        line.get('font_size'),
        line.get('max_font_size')
    )

    if score <= -999 and strong_title_font(line):
        score = 0

    return score


def extract_title(lines):

    if not lines:
        return None

    candidates = []

    # --------------------------------------------------------
    # Single-line candidates
    # --------------------------------------------------------

    for i, line in enumerate(
        lines[:30]
    ):

        text = line['text']

        score = title_score(
            text,
            i,
            line.get('font_size'),
            line.get('max_font_size')
        )

        if score > -999:

            candidates.append({

                'text': text,

                'score': score,

                'index': i,

                'top': line['top'],

                'bottom': line['bottom'],

                'font_size': line.get(
                    'font_size'
                )
            })

    # --------------------------------------------------------
    # Multi-line candidates.
    #
    # We only combine lines when:
    #   - they are close vertically
    #   - their font sizes are similar
    #   - both lines independently look title-like
    # --------------------------------------------------------

    limit = min(
        29,
        len(lines) - 1
    )

    for start in range(limit):

        first = lines[start]

        first_ok = looks_like_title(
            first['text']
        )

        # A chain starts on a normal title line, or on a large
        # print line. Titles are often printed large, and their
        # first line alone can be a fragment ('Single-cell
        # profiling reveals pathogenic').
        if not first_ok and not strong_title_font(first):
            continue

        # Long chains only start on large print. Small print text
        # that looks like a title can still form a pair, as
        # before. This keeps abstract sentences from chaining
        # into a fake multi-line title.
        if strong_title_font(first):
            chain_end = start + MAX_TITLE_LINES
        else:
            chain_end = start + 2

        running_score = line_title_score(
            first,
            start
        )

        combined = first['text']

        for j in range(
            start + 1,
            min(chain_end, len(lines))
        ):

            second = lines[j]

            if not compatible_title_lines(
                lines[j - 1],
                second
            ):
                break

            # Continuation lines follow the same rule as start
            # lines: title-like text, or large print. This keeps
            # body text (small font) out of the chain.
            if not (
                looks_like_title(second['text'])
                or strong_title_font(second)
            ):
                break

            candidate_text = (
                combined
                + ' '
                + second['text']
            )

            if not looks_like_title(
                candidate_text
            ):
                break

            score_second = line_title_score(
                second,
                j
            )

            if score_second <= -999:
                break

            combined = candidate_text
            running_score += score_second

            combined_score = (
                running_score
                + 5 * (j - start)
            )

            # Strong bonus for matching font sizes.
            if (
                first.get('font_size')
                and second.get('font_size')
            ):

                size_difference = abs(
                    first['font_size']
                    - second['font_size']
                )

                if size_difference <= 0.5:
                    combined_score += 4

                elif size_difference <= 1.0:
                    combined_score += 2

            candidates.append({

                'text': combined,

                'score': combined_score,

                'index': start,
                'end_index': j,

                'top': first['top'],

                'bottom': second['bottom'],

                'font_size': first.get(
                    'font_size'
                )
            })

    if not candidates:
        return None

    # --------------------------------------------------------
    # Prefer the strongest title-like candidate.
    # --------------------------------------------------------

    candidates.sort(
        key=lambda x: (
            x['score'],
            -x['index']
        ),
        reverse=True
    )

    return candidates[0]


# ============================================================
# AUTHOR TEXT NORMALIZATION
# ============================================================

def clean_author_text(text):

    if not text:
        return ""

    text = text.strip()

    text = re.sub(
        r'\S+@\S+',
        '',
        text
    )

    text = re.sub(
        r'https?://\S+|www\.\S+',
        '',
        text,
        flags=re.IGNORECASE
    )

    text = re.sub(
        r'\bdoi:\S+',
        '',
        text,
        flags=re.IGNORECASE
    )

    # 'et al.' / 'et al' is a truncation marker, not a surname.
    # Left in place, format_author() reads it as the last author
    # and produces '(al. 2020) ...' filenames.
    text = re.sub(
        r'[,\s]*\bet\s+al\.?\b\.?[,\s]*',
        ' ',
        text,
        flags=re.IGNORECASE
    )

    text = re.sub(
        r'[⁰¹²³⁴⁵⁶⁷⁸⁹]+',
        '',
        text
    )

    text = re.sub(
        r'(?<=[A-Za-z])\d+(?=[,\s;]|$)',
        '',
        text
    )

    text = re.sub(
        r'[\\*†‡§¶]+',
        '',
        text
    )

    text = re.sub(
        r'\s+',
        ' ',
        text
    )

    return text.strip()


# ============================================================
# PERSON NAME RECOGNITION
# ============================================================

NAME_PARTICLES = {

    'de',
    'da',
    'del',
    'van',
    'von',
    'der',
    'den',
    'la',
    'le'
}

# Lowercase or title-case function words that mark title-case
# prose. Their presence means the text is a title, not a person
# name, even when every word is capitalized.
TITLE_FUNCTION_WORDS = {

    'a',
    'an',
    'the',
    'of',
    'in',
    'on',
    'at',
    'to',
    'for',
    'from',
    'by',
    'with',
    'without',
    'and',
    'or',
    'but',
    'nor',
    'via',
    'through',
    'between',
    'under',
    'over',
    'into',
    'onto',
    'within',
    'across',
    'during',
    'after',
    'before',
    'against',
    'among',
    'toward',
    'towards',
    'upon',
    'per',
    'than',
    'that',
    'which',
    'when',
    'where',
    'while',
    'is',
    'are',
    'was',
    'were',
    'be',
    'been',
    'not',
    'no',
    'as',
    'its',
    'it',
    'their',
    'our',
    'this',
    'these',
    'those',
    'can',
    'may',
    'does',
    'do',
    'reveals',
    'reveal',
    'mediates',
    'regulates',
    'drives',
    'shapes',
    'enables',
    'improves',
    'requires',
    'controls',
    'defines',
    'links',
    'protein',
    'gene',
    'cell',
    'cells',
    'yeast',
    'human',
    'data',
    'analysis',
    'design',
    'engineering',
}


def is_initial(token):

    token = token.strip(
        '.,;:()[]{}'
    )

    # Concatenated initials ('J', 'AB', 'J.P.'), or a digraph
    # initial ('Ch.', 'Ph.', 'Th.'). Note strip() above already
    # removed the trailing period. The digraph first letter is a
    # closed set so two-letter English words ('In', 'Of', 'At')
    # and longer words ('Cha', 'Smith') stay words.
    return bool(
        re.fullmatch(
            r'(?:[A-ZÁÉÍÓÚÀ-ÖØ-Þ]\.?){1,3}'
            r'|[CPTSGKRW]h?',
            token
        )
    )


def is_name_word(token):

    token = token.strip(
        '.,;:()[]{}'
    )

    if not token:
        return False

    if is_initial(token):
        return True

    if token.lower() in NAME_PARTICLES:
        return True

    return bool(
        re.fullmatch(
            r"[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’\-]+",
            token
        )
    )


def name_word_count(text):

    if not text:
        return 0

    return sum(
        1
        for token in text.split()
        if is_name_word(token)
    )


def looks_like_single_person(text):

    text = clean_author_text(text)

    if not text:
        return False

    if looks_like_journal_header(text):
        return False

    if looks_like_publisher_line(text):
        return False

    if looks_like_affiliation(text):
        return False

    if is_article_boundary(text):
        return False

    if is_keyword_line(text):
        return False

    if '@' in text:
        return False

    words = text.split()

    if len(words) < 2:
        return False

    if len(words) > 6:
        return False

    if words and words[0][0].isdigit():
        return False

    # A person name is all capitalized (or a lowercase particle).
    # A lowercase function word ('of', 'in', 'for', ...) means this
    # is title-case prose, not a name. Without this, title-case
    # titles such as 'Engineering of Metabolic Pathways in Yeast'
    # parse as a single person and get rejected as titles.
    for word in words:

        stripped = word.strip(
            '.,;:()[]{}"\'`'
        )

        if not stripped or not stripped[0].isalpha():
            continue

        lowered = stripped.lower()

        # Initials ('A.', 'J.P.') belong to names, never to
        # title prose, even though 'a' is also a function word.
        if is_initial(stripped):
            continue

        if lowered in NAME_PARTICLES:
            continue

        if lowered in TITLE_FUNCTION_WORDS:
            return False

        if stripped[0].islower():
            return False

    if name_word_count(text) < 2:
        return False

    return True


# ============================================================
# AUTHOR SEPARATOR / FORMAT DETECTION
# ============================================================

def has_author_separator(text):

    if not text:
        return False

    lower = text.lower()

    if ',' in text:
        return True

    if '&' in text:
        return True

    if re.search(
        r'\band\b',
        lower
    ):
        return True

    initials = re.findall(
        r'\b[A-Z]\.?\b',
        text
    )

    if len(initials) >= 2:
        return True

    return False


def parse_author_line(text):

    text = clean_author_text(text)

    if not text:
        return []

    text = re.sub(
        r'\s+(?:and|&)\s+',
        ',',
        text,
        flags=re.IGNORECASE
    )

    text = text.replace(
        ';',
        ','
    )

    comma_parts = [
        p.strip()
        for p in text.split(',')
        if p.strip()
    ]

    if len(comma_parts) >= 2:

        valid_parts = []

        for part in comma_parts:

            if looks_like_single_person(
                part
            ):
                valid_parts.append(
                    part
                )

        if len(valid_parts) >= 2:
            return valid_parts

    if looks_like_single_person(text):
        return [text]

    return []


# ============================================================
# AUTHOR BLOCK DETECTION
# ============================================================

def author_line_strength(
    text,
    line,
    title_bottom
):

    text = clean_author_text(text)

    if not text:
        return 0

    if looks_like_journal_header(text):
        return 0

    if looks_like_publisher_line(text):
        return 0

    if looks_like_affiliation(text):
        return 0

    if is_article_boundary(text):
        return 0

    if is_keyword_line(text):
        return 0

    authors = parse_author_line(text)

    if not authors:
        return 0

    distance = (
        line['top']
        - title_bottom
    )

    score = 0.0

    if distance < 10:
        score += 0.35

    elif distance < 25:
        score += 0.40

    elif distance < 45:
        score += 0.32

    elif distance < 70:
        score += 0.18

    elif distance < 100:
        score += 0.05

    else:
        return 0

    if len(authors) == 1:
        score += 0.18

    elif len(authors) == 2:
        score += 0.28

    elif len(authors) >= 3:
        score += 0.25

    if has_author_separator(text):
        score += 0.15

    if re.search(
        r'\b[A-Z]\.',
        text
    ):
        score += 0.12

    if len(text.split()) > 12:
        score -= 0.25

    if re.search(
        r'\d',
        text
    ):
        score -= 0.15

    return max(
        0,
        min(
            score,
            1
        )
    )


def extract_author_block(
    lines,
    title
):

    if not title:
        return None, 0, []

    title_index = title['index']
    title_bottom = title['bottom']

    if title.get('end_index') is not None:
        title_index = title['end_index']

    candidates = []

    search_end = min(
        title_index + 8,
        len(lines)
    )

    for i in range(
        title_index + 1,
        search_end
    ):

        line = lines[i]
        text = line['text']

        lower = text.lower()

        if is_article_boundary(text):
            break

        if is_keyword_line(text):
            break

        if 'doi:' in lower:
            break

        if re.search(
            r'https?://|www\.',
            lower
        ):
            break

        if looks_like_affiliation(text):
            break

        strength = author_line_strength(
            text,
            line,
            title_bottom
        )

        if strength > 0:

            candidates.append({

                'text': clean_author_text(
                    text
                ),

                'authors': parse_author_line(
                    text
                ),

                'score': strength,

                'index': i
            })

    if not candidates:
        return None, 0, []

    candidates.sort(
        key=lambda x: x['score'],
        reverse=True
    )

    best = candidates[0]

    return (
        best['text'],
        best['score'],
        candidates
    )


# ============================================================
# DOI
# ============================================================

def extract_doi(text):

    if not text:
        return None

    # Greedy over DOI characters, then back off to the last
    # character that can legitimately end a DOI. A DOI may end in
    # ')' (e.g. 10.1577/1551-5028(2007)); \b cannot match after
    # ')', so trailing punctuation is handled by the rstrip below.
    match = re.search(
        r'\b10\.\d{4,9}/[-._;()/:\w]+',
        text,
        re.IGNORECASE
    )

    if not match:
        return None

    doi = match.group(0)

    return doi.rstrip(
        '.,;'
    )


def fetch_crossref(doi):

    if not doi:
        return None

    url = (
        'https://api.crossref.org/works/'
        + doi
    )

    try:

        response = requests.get(
            url,
            headers={
                'Accept': 'application/json',
                'User-Agent':
                    'PDF-Renamer/1.0'
            },
            timeout=10
        )

        if response.status_code != 200:
            return None

        data = response.json()

        return data.get(
            'message'
        )

    except Exception:

        return None


def fetch_crossref_by_title(title):

    """Find a CrossRef record with a bibliographic title search.

    Used when the DOI is missing or broken, which happens when
    publishers interleave the DOI footer with body text.
    """

    if not title:
        return None

    url = 'https://api.crossref.org/works'

    try:

        response = requests.get(
            url,
            params={
                'query.bibliographic': title,
                'rows': '3'
            },
            headers={
                'Accept': 'application/json',
                'User-Agent':
                    'PDF-Renamer/1.0'
            },
            timeout=10
        )

        if response.status_code != 200:
            return None

        items = (
            response.json()
            .get('message', {})
            .get('items', [])
        )

        best = None
        best_similarity = 0.0

        for item in items:

            titles = item.get('title', [])

            if not titles:
                continue

            ratio = similarity(
                title,
                titles[0]
            )

            if ratio > best_similarity:
                best_similarity = ratio
                best = item

        if best_similarity < CROSSREF_TITLE_SIMILARITY:
            return None

        return best

    except Exception:

        return None


def crossref_title(data):

    if not data:
        return None

    titles = data.get(
        'title',
        []
    )

    if titles:
        title = titles[0]

        if title and looks_like_title(
            title
        ):
            return title.strip()

    return None


def crossref_authors(data):

    if not data:
        return []

    results = []

    for author in data.get(
        'author',
        []
    ):

        given = author.get(
            'given',
            ''
        ).strip()

        family = author.get(
            'family',
            ''
        ).strip()

        if not family:
            continue

        if given:

            results.append(
                f"{given} {family}"
            )

        else:

            results.append(
                family
            )

    return results


def crossref_year(data):

    if not data:
        return None

    fields = [
        'published-print',
        'published-online',
        'published',
        'issued'
    ]

    years = []

    for field in fields:

        info = data.get(
            field
        )

        if not info:
            continue

        parts = info.get(
            'date-parts',
            []
        )

        if not parts:
            continue

        if not parts[0]:
            continue

        year = valid_year(
            parts[0][0]
        )

        if year:
            years.append(
                year
            )

    if not years:
        return None

    return max(
        years
    )


# ============================================================
# AUTHOR COMPARISON
# ============================================================

def split_local_authors(text):

    return parse_author_line(
        text
    )


def last_name(author):

    if not author:
        return None

    author = author.strip()

    if ',' in author:

        first = author.split(
            ','
        )[0].strip()

        if first:
            return first

    parts = author.split()

    if not parts:
        return None

    if len(parts) >= 2:

        # Surname-first ordering ('Smith J. A.') ends in initials.
        # The surname is the leading capitalized token.
        if all(
            is_initial(p)
            for p in parts[1:]
        ):
            return parts[0]

        # Prefix a single particle, matching the established
        # convention: 'de la Cruz' -> 'la Cruz',
        # 'van der Berg' -> 'van der Berg'.
        if parts[-2].lower() in NAME_PARTICLES:

            return (
                parts[-2]
                + ' '
                + parts[-1]
            )

    return parts[-1]


def author_agreement(
    local_author_text,
    crossref_list
):

    if not local_author_text:
        return 0

    if not crossref_list:
        return 0

    local_authors = split_local_authors(
        local_author_text
    )

    if not local_authors:
        return 0

    local_names = [
        surname.lower()
        for a in local_authors
        if (surname := last_name(a))
    ]

    cross_names = [
        surname.lower()
        for a in crossref_list
        if (surname := last_name(a))
    ]

    if not local_names:
        return 0

    matches = 0

    for name in local_names:

        for cross_name in cross_names:

            if similarity(
                name,
                cross_name
            ) >= 0.80:

                matches += 1
                break

    return matches / len(
        local_names
    )


# ============================================================
# AUTHOR DECISION
# ============================================================

def choose_authors(
    local_author,
    local_confidence,
    crossref_list,
    metadata_author
):

    if (
        local_author
        and
        local_confidence
        >= AUTHOR_CONFIDENCE_THRESHOLD
    ):

        agreement = author_agreement(
            local_author,
            crossref_list
        )

        if crossref_list and agreement >= 0.50:
            return local_author, agreement

        if not crossref_list:
            return local_author, agreement

    if crossref_list:

        return (
            ', '.join(
                crossref_list
            ),
            0
        )

    if metadata_author:

        return metadata_author, 0

    return None, 0


# ============================================================
# AUTHOR FORMAT
# ============================================================

def format_author(author_text):

    if not author_text:
        return "Unknown"

    authors = parse_author_line(
        author_text
    )

    if not authors:

        cleaned = clean_author_text(
            author_text
        )

        if cleaned:

            surname = last_name(
                cleaned
            )

            if surname:
                return surname

        return "Unknown"

    surnames = []

    for author in authors:

        surname = last_name(
            author
        )

        if surname:
            surnames.append(
                surname
            )

    if not surnames:
        return "Unknown"

    if len(surnames) == 1:
        return surnames[0]

    if len(surnames) == 2:

        return (
            f"{surnames[0]} and "
            f"{surnames[1]}"
        )

    return (
        f"{surnames[0]} et al."
    )


# ============================================================
# TITLE VALIDATION
# ============================================================

def title_is_credible(title):

    if not title:
        return False

    if not looks_like_title(
        title
    ):
        return False

    return True


# ============================================================
# TITLE DECISION
# ============================================================

def choose_title(
    local_title,
    metadata_title,
    crossref_title_value,
    is_biorxiv=False
):

    local_valid = title_is_credible(
        local_title
    )

    metadata_valid = title_is_credible(
        metadata_title
    )

    crossref_valid = title_is_credible(
        crossref_title_value
    )

    # --------------------------------------------------------
    # BIO RXIV
    #
    # Local PDF title remains authoritative.
    # CrossRef is NOT allowed to replace it.
    # --------------------------------------------------------

    if is_biorxiv:

        if local_valid:

            return local_title

        if metadata_valid:

            # Metadata is only a fallback.
            # It must not contain bioRxiv header material.
            if not is_biorxiv_header_or_license(
                metadata_title
            ):
                return metadata_title

        return None

    # --------------------------------------------------------
    # STANDARD PUBLICATION
    #
    # CrossRef is used as the primary external confirmation.
    #
    # If CrossRef provides a credible title, use it when:
    #   - local extraction failed, OR
    #   - local extraction differs substantially.
    #
    # This prevents PDF layout artifacts from becoming the
    # filename.
    # --------------------------------------------------------

    if crossref_valid:

        if not local_valid:
            return crossref_title_value

        local_similarity = similarity(
            local_title,
            crossref_title_value
        )

        if local_similarity >= CROSSREF_TITLE_SIMILARITY:

            # PDF layout often drops or spaces out marker signs
            # ('CD8+GZMK+' -> 'CD8 GZMK', or 'K+CD8+' -> 'K + CD8 +').
            # normalize_text() strips '+' and spaces, so such a
            # local title scores ~1.0 and would beat the canonical
            # record. When the two differ only by that
            # punctuation, CrossRef is the better filename.
            if differs_only_by_marker_punctuation(
                local_title,
                crossref_title_value
            ):

                return crossref_title_value

            return local_title

        # Local title differs substantially. The local extraction
        # lost to CrossRef's canonical record.
        return crossref_title_value

    # --------------------------------------------------------
    # CrossRef unavailable.
    # --------------------------------------------------------

    if local_valid:
        return local_title

    if metadata_valid:
        return metadata_title

    return None


# ============================================================
# YEAR SELECTION
# ============================================================

def page_years(text):

    strong = []
    medium = []
    weak = []

    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip()
    ]

    for line in lines[:35]:

        years = years_in_text(
            line
        )

        if not years:
            continue

        lower = line.lower()

        if re.search(
            r'published|publication|copyright|©',
            lower
        ):

            strong.extend(
                years
            )

        elif re.search(
            r'journal|volume|issue|doi|'
            r'accepted|received',
            lower
        ):

            medium.extend(
                years
            )

        else:

            weak.extend(
                years
            )

    return strong, medium, weak


def choose_year(
    crossref_year_value,
    page_year_data,
    metadata_year
):

    strong, medium, weak = page_year_data

    candidates = []

    if crossref_year_value:

        year = valid_year(
            crossref_year_value
        )

        if year:

            candidates.append(
                (year, 100)
            )

    for year in strong:

        candidates.append(
            (year, 95)
        )

    for year in medium:

        candidates.append(
            (year, 70)
        )

    if metadata_year:

        year = valid_year(
            metadata_year
        )

        if year:

            candidates.append(
                (year, 40)
            )

    for year in weak:

        candidates.append(
            (year, 20)
        )

    candidates = [
        item
        for item in candidates
        if item[0] <= CURRENT_YEAR
    ]

    if not candidates:
        return None

    highest_score = max(
        score
        for _, score in candidates
    )

    best_years = [
        year
        for year, score in candidates
        if score == highest_score
    ]

    return max(
        best_years
    )

# ============================================================
# DIAGNOSTICS
# ============================================================

def print_author_diagnostics(
    candidates,
    selected_author,
    confidence,
    crossref_list,
    agreement
):

    print(
        "\n  AUTHOR ANALYSIS"
    )

    if not candidates:

        print(
            "    No local author candidates found."
        )

    else:

        for candidate in sorted(
            candidates,
            key=lambda x: x['score'],
            reverse=True
        ):

            print(
                f"    Candidate: "
                f"'{candidate['text']}' "
                f"[confidence {candidate['score']:.2f}]"
            )

    if selected_author:

        print(
            f"    Selected: "
            f"{selected_author}"
        )

        print(
            f"    Local confidence: "
            f"{confidence:.2f}"
        )

    else:

        print(
            "    Selected: NONE"
        )

    if crossref_list:

        print(
            "    CrossRef authors: "
            + ", ".join(
                crossref_list[:10]
            )
        )

        print(
            f"    Author agreement: "
            f"{agreement:.2f}"
        )


# ============================================================
# MAIN PROCESSING
# ============================================================

def collect_pdf_files(args):
    """Resolve command-line arguments into a list of PDF files.

    Accepts explicit filenames (typically shell-expanded globs such
    as `pdf_renamer *.pdf`), unexpanded glob patterns, or nothing at
    all (in which case every PDF in the current folder is selected).
    """

    files = []

    for arg in args:

        if any(c in arg for c in '*?['):
            files.extend(glob.glob(arg))
        else:
            files.append(arg)

    if not files:
        files = glob.glob('*.pdf')

    # Deduplicate, keep order, keep PDFs only.
    seen = set()
    pdf_files = []

    for f in files:

        normalized = os.path.normpath(
            f
        )

        if normalized not in seen and os.path.splitext(normalized)[1].lower() in PDF_EXTENSIONS:
            seen.add(normalized)
            pdf_files.append(normalized)

    return pdf_files


def process_pdfs(pdf_files):

    if not pdf_files:

        print(
            "No PDF files found in this folder."
        )

        return

    for filename in pdf_files:

        print(
            "\n"
            + "=" * 80
        )

        print(
            f"Processing: {filename}"
        )

        print(
            "=" * 80
        )

        # ----------------------------------------------------
        # PDF METADATA
        # ----------------------------------------------------

        (
            metadata_title,
            metadata_author,
            metadata_year
        ) = extract_metadata(
            filename
        )

        # ----------------------------------------------------
        # FIRST PAGE
        # ----------------------------------------------------

        words, _ = extract_first_page(
            filename
        )

        if not words:

            print(
                "  ❌ Could not extract first page."
            )

            continue

        lines = group_words_into_lines(
            words
        )

        first_page_text = '\n'.join(
            line['text']
            for line in lines
        )

        # ----------------------------------------------------
        # DOI
        # ----------------------------------------------------

        doi = extract_doi(
            first_page_text
        )

        if doi:

            print(
                f"\n  DOI: {doi}"
            )

        else:

            print(
                "\n  DOI: NONE"
            )

        # ----------------------------------------------------
        # BIO RXIV DETECTION
        # ----------------------------------------------------

        biorxiv = is_biorxiv_preprint(
            first_page_text,
            doi
        )

        if biorxiv:

            print(
                "  DOCUMENT TYPE: bioRxiv PREPRINT"
            )

        else:

            print(
                "  DOCUMENT TYPE: Standard publication"
            )

        # ----------------------------------------------------
        # LOCAL TITLE
        # ----------------------------------------------------

        title_candidate = extract_title(
            lines
        )

        if title_candidate:

            local_title = (
                title_candidate['text']
            )

            print(
                "\n  LOCAL TITLE:"
            )

            print(
                f"    {local_title}"
            )

            if title_candidate.get(
                'font_size'
            ):

                print(
                    f"    Font size: "
                    f"{title_candidate['font_size']:.2f}"
                )

        else:

            local_title = None

            print(
                "\n  LOCAL TITLE: NONE"
            )

        # ----------------------------------------------------
        # CROSSREF
        # ----------------------------------------------------

        crossref_data = None

        if doi:

            crossref_data = fetch_crossref(
                doi
            )

            if crossref_data:

                print(
                    "  DOI VALIDATION: PASSED"
                )

            else:

                print(
                    "  DOI VALIDATION: FAILED"
                )

        # ----------------------------------------------------
        # CROSSREF TITLE SEARCH
        #
        # Some publishers interleave the DOI footer with body
        # text, so DOI lookup fails. A bibliographic search on
        # the local title recovers the record. Skipped for
        # bioRxiv preprints, because the search can return the
        # published paper and overwrite preprint information.
        # ----------------------------------------------------

        if (
            crossref_data is None
            and local_title
            and not biorxiv
        ):

            crossref_data = fetch_crossref_by_title(
                local_title
            )

            if crossref_data:

                print(
                    "  CROSSREF TITLE SEARCH: MATCHED"
                )

            else:

                print(
                    "  CROSSREF TITLE SEARCH: NO MATCH"
                )

        # ----------------------------------------------------
        # CROSSREF DATA
        # ----------------------------------------------------

        cr_title = crossref_title(
            crossref_data
        )

        cr_authors = crossref_authors(
            crossref_data
        )

        cr_year = crossref_year(
            crossref_data
        )

        if cr_title:

            print(
                "\n  CROSSREF TITLE:"
            )

            print(
                f"    {cr_title}"
            )

        # ----------------------------------------------------
        # TITLE DECISION
        # ----------------------------------------------------

        final_title = choose_title(
            local_title,
            metadata_title,
            cr_title,
            is_biorxiv=biorxiv
        )

        if not final_title:

            print(
                "  ❌ No credible title could be determined."
            )

            continue

        if local_title and cr_title:

            title_similarity = similarity(
                local_title,
                cr_title
            )

            print(
                f"\n  LOCAL/CROSSREF TITLE "
                f"SIMILARITY: "
                f"{title_similarity:.2f}"
            )

            if biorxiv:

                print(
                    "  ℹ️ CrossRef title ignored "
                    "because document is bioRxiv."
                )

            else:

                if title_similarity < CROSSREF_TITLE_SIMILARITY:

                    print(
                        "  ℹ️ Local title differed "
                        "substantially from CrossRef."
                    )

                    print(
                        "  ℹ️ CrossRef title selected "
                        "as the canonical title."
                    )

                else:

                    print(
                        "  ℹ️ CrossRef confirms "
                        "the local title."
                    )

        # ----------------------------------------------------
        # AUTHOR BLOCK
        # ----------------------------------------------------

        (
            local_author,
            local_confidence,
            author_candidates
        ) = extract_author_block(
            lines,
            title_candidate
        )

        # ----------------------------------------------------
        # AUTHOR DECISION
        # ----------------------------------------------------

        final_author, agreement = choose_authors(
            local_author,
            local_confidence,
            cr_authors,
            metadata_author
        )

        print_author_diagnostics(
            author_candidates,
            final_author,
            local_confidence,
            cr_authors,
            agreement
        )

        formatted_author = format_author(
            final_author
        )

        # ----------------------------------------------------
        # AUTHOR FALLBACK
        #
        # A scientific paper always has authors. 'Unknown'
        # means extraction failed, and the filename would be
        # built on bad data.
        # ----------------------------------------------------

        if formatted_author == "Unknown":

            print(
                "\n  ⚠️ No credible author found."
            )

            print(
                "  ⚠️ File will NOT be renamed."
            )

            continue

        # ----------------------------------------------------
        # YEAR
        # ----------------------------------------------------

        if biorxiv:

            final_year = "PREPRINT"

            print(
                "\n  YEAR: PREPRINT"
            )

            print(
                "  ℹ️ Publication year ignored "
                "because document is a bioRxiv preprint."
            )

        else:

            final_year = choose_year(
                cr_year,
                page_years(
                    first_page_text
                ),
                metadata_year
            )

        
        # ----------------------------------------------------
        # CLEAN TITLE
        # ----------------------------------------------------

        final_title = clean_filename(
            final_title
        )

        final_title = truncate_title(
            final_title
        )

       
        # ----------------------------------------------------
        # YEAR FALLBACK
        # ----------------------------------------------------

        if final_year is None:

            print(
                "\n  ⚠️ No reliable publication "
                "year found."
            )

            print(
                "  ⚠️ File will NOT be renamed."
            )

            continue

        # ----------------------------------------------------
        # FINAL FILENAME
        # ----------------------------------------------------

        new_name = (
            f"({formatted_author} "
            f"{final_year}) "
            f"{final_title}.pdf"
        )

        new_name = clean_filename(
            new_name
        )

        # Keep renamed files next to their source. A bare name
        # would land in the terminal's current folder when the
        # script runs from somewhere else.
        new_name = os.path.join(
            os.path.dirname(filename),
            new_name
        )

        source_path = os.path.normpath(
            filename
        )
        target_path = os.path.normpath(
            new_name
        )

        # ----------------------------------------------------
        # FINAL DIAGNOSTICS
        # ----------------------------------------------------

        print(
            "\n  FINAL RESULT"
        )

        print(
            f"    Document type: "
            f"{'bioRxiv PREPRINT' if biorxiv else 'Publication'}"
        )

        print(
            f"    Author: {formatted_author}"
        )

        print(
            f"    Year: {final_year}"
        )

        print(
            f"    Title: {final_title}"
        )

        print(
            "    New filename:"
        )

        print(
            f"      {new_name}"
        )

        # ----------------------------------------------------
        # RENAME
        # ----------------------------------------------------

        if source_path == target_path:

            print(
                "  ℹ️ Filename already matches."
            )

            continue

        try:

            new_name = unique_target_path(
                filename,
                new_name
            )

            if new_name is None:

                print(
                    "  ⚠️ Could not find a free "
                    "filename. Skipped."
                )

                continue

            if source_path != os.path.normpath(new_name):
                os.replace(
                    filename,
                    new_name
                )

            print(
                "  ✅ Renamed successfully."
            )

        except Exception as e:

            print(
                f"  ⚠️ Rename failed: {e}"
            )


def unique_target_path(source, target):

    if os.path.normpath(source) == os.path.normpath(target):
        return target

    base, ext = os.path.splitext(target)

    candidates = [target]
    candidates.extend(
        f"{base} ({suffix}){ext}"
        for suffix in range(
            1,
            MAX_NAME_SUFFIX + 1
        )
    )

    for candidate in candidates:

        # Reserve the name atomically so concurrent runs cannot
        # both claim the same free slot.
        try:

            handle = os.open(
                candidate,
                os.O_CREAT
                | os.O_EXCL
                | os.O_WRONLY
            )

            os.close(handle)
            os.unlink(candidate)

        except FileExistsError:
            continue

        except OSError:
            continue

        return candidate

    return None


# ============================================================
# RUN
# ============================================================

def main():

    process_pdfs(
        collect_pdf_files(
            sys.argv[1:]
        )
    )


if __name__ == "__main__":

    main()

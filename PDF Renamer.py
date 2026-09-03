# v260903
#Fixed spatial line-number cropping issue that interfered with metadata extraction from some preprints.
#Added isolated line-number detection and filtering during PDF text processing.
#Improved title extraction with better scoring, multiline title assembly, and abstract detection.
#Improved author extraction and parsing of complex author lists.
#Added filename-based metadata recovery when PDF metadata is incomplete.
#Added title cleanup/normalization (ligatures, special characters, hyphenation fixes, footnote markers).
#Enhanced CrossRef integration with retries, timeouts, DOI extraction, and title-based lookups.
#Improved title matching confidence and metadata validation logic.
#Added more robust publication year selection/ranking.
#Updated author formatting to produce "Author", "Author and Coauthor", or "Author et al" output.
#Expanded configuration options for search depth, filename limits, confidence thresholds, and API behavior.
import os
import re
import difflib
import time
import requests
import pdfplumber

from datetime import datetime
from PyPDF2 import PdfReader


# ============================================================
# CONFIGURATION
# ============================================================

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(SCRIPT_DIR)

CURRENT_YEAR = datetime.now().year

MIN_YEAR = 1900
MAX_YEAR = CURRENT_YEAR

AUTHOR_CONFIDENCE_THRESHOLD = 0.68

TITLE_SEARCH_LINES = 20
EXTENDED_TITLE_SEARCH_LINES = 40

MAX_TITLE_TOP = 220.0

AUTHOR_SEARCH_LINES = 25

MIN_TITLE_LENGTH = 10
MAX_TITLE_LENGTH = 250
MAX_TITLE_WORDS = 35

MAX_FILENAME_LENGTH = 150

DRY_RUN = False
SKIP_ON_TITLE_MISMATCH = False

CROSSREF_TIMEOUT = 10
CROSSREF_RETRY_COUNT = 2
CROSSREF_RETRY_DELAY = 1.0

USE_CROSSREF_TITLE_SEARCH = True
CROSSREF_TITLE_SEARCH_MIN_SIMILARITY = 0.85

MIN_FINAL_CONFIDENCE = 0.60

CROSSREF_TITLE_SIMILARITY = 0.65

TITLE_LINE_DISTANCE = 32
TITLE_FONT_SIZE_TOLERANCE = 2.0


# ============================================================
# TEXT CLEANING & NORMALIZATION
# ============================================================

def clean_filename(text):
    if not text:
        return ""
    text = re.sub(r'[<>:"/\\|?*]', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip().rstrip('.')


def limit_filename_length(filename, max_length=MAX_FILENAME_LENGTH):
    if max_length is None or len(filename) <= max_length:
        return filename

    extension = ".pdf"
    base = filename[:-len(extension)] if filename.lower().endswith(extension) else filename
    available = max_length - len(extension)

    if available <= 0:
        return filename[:max_length]

    base = base[:available].rstrip()
    if " " in base:
        base = base.rsplit(" ", 1)[0].rstrip()

    return base + extension


def clean_title_text(text):
    if not text:
        return ""
    replacements = {
        'ﬁ': 'fi', 'ﬂ': 'fl', 'ﬀ': 'ff', 'ﬃ': 'ffi', 'ﬄ': 'ffl',
        '’': "'", '‘': "'", '“': '"', '”': '"', '–': '-', '—': '-'
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = re.sub(r'[\\*†‡§¶#⁰¹²³⁴⁵⁶⁷⁸⁹]+', '', text)
    text = re.sub(r'(?<=\w)\d+(?=\s|$)', '', text) 
    text = re.sub(r'(?<=\w)-\s+(?=\w)', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def normalize_text(text):
    if not text:
        return ""
    text = clean_title_text(text).lower()
    text = re.sub(r'[^\w\s]', '', text)
    return text.strip()


def similarity(a, b):
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, normalize_text(a), normalize_text(b)).ratio()


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
    for match in re.findall(r'\b(19\d{2}|20\d{2})\b', text):
        year = valid_year(match)
        if year:
            results.append(year)
    return results


# ============================================================
# PDF METADATA & FILENAME PARSING
# ============================================================

def extract_metadata_from_filename(filename):
    base = os.path.basename(filename)
    if base.lower().endswith('.pdf'):
        base = base[:-4].strip()
        
    pattern = r"^\((.*?)\s+(\d{4})\)\s+(.*)"
    match = re.search(pattern, base)
    
    if match:
        author = match.group(1).strip()
        year = match.group(2).strip()
        title = match.group(3).strip()
        return title, author, year
    return None, None, None


def extract_metadata(pdf_path):
    try:
        reader = PdfReader(pdf_path)
        meta = reader.metadata or {}
        title = meta.get('/Title')
        author = meta.get('/Author')
        year = None
        creation_date = meta.get('/CreationDate')

        if creation_date:
            for match in re.findall(r'(19\d{2}|20\d{2})', str(creation_date)):
                candidate = valid_year(match)
                if candidate:
                    year = candidate
                    break

        return title, author, year
    except Exception:
        return None, None, None


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
                extra_attrs=['size', 'fontname']
            )
            return words, page
    except Exception:
        return [], None


# ============================================================
# RECONSTRUCT LINES FROM PDF WORDS
# ============================================================

def is_isolated_number(text):
    text = text.strip().lower()
    return bool(re.fullmatch(r'\[?\(?(?:l|line)?\s*\d+\)?\]?\.?', text))

def group_words_into_lines(words):
    if not words:
        return []

    words = sorted(
        words,
        key=lambda w: (float(w.get('top', 0)), float(w.get('x0', 0)))
    )

    lines = []
    for word in words:
        top = float(word.get('top', 0))
        placed = False

        for line in lines:
            if abs(top - line['top']) <= 3.5:
                line['words'].append(word)
                line['top'] = sum(
                    float(w.get('top', 0)) for w in line['words']
                ) / len(line['words'])
                placed = True
                break

        if not placed:
            lines.append({'top': top, 'words': [word]})

    valid_lines = []
    for line in lines:
        line['words'].sort(key=lambda w: float(w.get('x0', 0)))
        
        while len(line['words']) > 1:
            first_word = line['words'][0]
            if is_isolated_number(first_word.get('text', '')):
                gap = float(line['words'][1].get('x0', 0)) - float(first_word.get('x1', 0))
                if gap > 12.0:
                    line['words'] = line['words'][1:]
                else:
                    break
            else:
                break

        while len(line['words']) > 1:
            last_word = line['words'][-1]
            if is_isolated_number(last_word.get('text', '')):
                gap = float(last_word.get('x0', 0)) - float(line['words'][-2].get('x1', 0))
                if gap > 12.0:
                    line['words'] = line['words'][:-1]
                else:
                    break
            else:
                break

        if not line['words']:
            continue

        raw_text = ' '.join(w.get('text', '') for w in line['words']).strip()
        line['text'] = clean_title_text(raw_text)
        line['bottom'] = max(float(w.get('bottom', line['top'])) for w in line['words'])
        line['x0'] = min(float(w.get('x0', 0)) for w in line['words'])
        line['x1'] = max(float(w.get('x1', 0)) for w in line['words'])

        sizes = []
        for word in line['words']:
            try:
                size = float(word.get('size', 0))
                if size > 0:
                    sizes.append(size)
            except (ValueError, TypeError):
                pass

        if sizes:
            line['font_size'] = sum(sizes) / len(sizes)
            line['max_font_size'] = max(sizes)
            line['min_font_size'] = min(sizes)
        else:
            line['font_size'] = None
            line['max_font_size'] = None
            line['min_font_size'] = None

        valid_lines.append(line)

    valid_lines.sort(key=lambda x: x['top'])
    return valid_lines


# ============================================================
# STRUCTURAL BOUNDARIES & DETECTION
# ============================================================

JOURNAL_TERMS = [
    'nature', 'science', 'cell', 'plos', 'elsevier', 'springer', 'wiley',
    'frontiers', 'mdpi', 'oxford', 'cambridge', 'taylor & francis', 'sage',
    'acs', 'american chemical society', 'royal society', 'academic press',
    'bmc', 'biomed central', 'bentham', 'bmj', 'journal of', 'proceedings of',
    'annals of', 'letters in', 'reviews in', 'current opinion in',
    'scientific reports', 'communications biology', 'communications chemistry',
    'communications medicine', 'nature communications', 'international journal of',
    'european journal of', 'american journal of', 'british journal of', 
    'transactions of', 'advances in'
]

def is_article_boundary(text):
    if not text:
        return False
    lower = text.lower().strip()
    boundary_patterns = [
        r'^abstract\b', r'^keywords?\b', r'^key words?\b', r'^introduction\b',
        r'^background\b', r'^materials and methods\b', r'^methods\b',
        r'^results\b', r'^discussion\b', r'^references\b', r'^bibliography\b',
        r'^supplementary\b'
    ]
    return any(re.search(pattern, lower) for pattern in boundary_patterns)

def is_keyword_line(text):
    if not text:
        return False
    return bool(re.match(r'^(keywords?|key\s+words?)\b', text.strip().lower()))

def looks_like_journal_header(text):
    if not text:
        return False
    lower = text.lower().strip()
    if len(text.split()) > 10 and (':' in text or ' of ' in lower or ' in ' in lower):
        return False
    for term in JOURNAL_TERMS:
        if lower == term or lower.startswith(term + ' ') or lower.endswith(' ' + term):
            return True
    if re.search(r'^\s*journal\s+of\b', lower) and len(text.split()) <= 6:
        return True
    if re.search(r'\bvol(?:ume)?\.?\s*\d+', lower):
        return True
    if re.search(r'\b\d{1,4}\s*\(\d{1,4}\)\s*:\s*\d+', lower):
        return True
    return False

def looks_like_publisher_line(text):
    if not text:
        return False
    lower = text.lower()
    terms = [
        'published by', 'copyright', 'open access', 'creative commons',
        'available online', 'online version', 'publisher', 'editorial note'
    ]
    return any(term in lower for term in terms)


# ============================================================
# SCIENTIFIC TITLE LANGUAGE
# ============================================================

SCIENTIFIC_TERMS = {
    'analysis', 'analyses', 'characterization', 'characterisation', 'engineering',
    'engineered', 'design', 'designed', 'development', 'developed', 'identification',
    'expression', 'production', 'synthesis', 'genome', 'genomic', 'protein',
    'proteins', 'enzyme', 'enzymes', 'cell', 'cells', 'yeast', 'bacterial',
    'bacteria', 'plant', 'plants', 'microbial', 'microbe', 'microbes', 'metabolic',
    'metabolism', 'pathway', 'pathways', 'cloning', 'assembly', 'system',
    'systems', 'method', 'methods', 'technology', 'technologies', 'biosynthesis',
    'fermentation', 'biological', 'biotechnology', 'biomanufacturing', 'sequence',
    'sequences', 'sequencing', 'mutation', 'mutations', 'variant', 'variants',
    'genetic', 'genetics', 'cultivation', 'activity', 'binding', 'structure',
    'structures', 'evolution', 'evolutionary', 'screening', 'high-throughput',
    'highthroughput', 'synthetic', 'metabolite', 'metabolites', 'molecular',
    'cellular', 'organism', 'organisms', 'recombinant', 'crystallization',
    'spectrometry', 'chromatography', 'assay', 'assays', 'phenotype',
    'phenotypic', 'genotype', 'genotypic', 'benchmark', 'linking', 'entity', 'synel',
    'learning', 'network', 'networks', 'neural', 'model', 'models', 'data',
    'algorithm', 'algorithms', 'training', 'performance', 'framework', 
    'architecture', 'dataset', 'datasets', 'evaluation', 'machine', 'artificial', 'intelligence'
}

def scientific_term_count(text):
    if not text:
        return 0
    words = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ'-]+", text.lower())
    return sum(1 for word in words if word in SCIENTIFIC_TERMS)

def has_title_structure(text):
    if not text:
        return False
    if ':' in text:
        return True
    structural_indicators = [' of ', ' in ', ' for ', ' using ', ' with ', ' via ', ' by ', ' through ']
    lower = text.lower()
    return any(ind in lower for ind in structural_indicators)


# ============================================================
# AUTHOR DETECTION
# ============================================================

NAME_PARTICLES = {'de', 'da', 'del', 'van', 'von', 'der', 'den', 'la', 'le'}

def is_initial(token):
    token = token.strip('.,;:()[]{}')
    return bool(re.fullmatch(r'[A-Z]\.?', token))

def is_name_word(token):
    token = token.strip('.,;:()[]{}')
    if not token:
        return False
    if is_initial(token):
        return True
    if token.lower() in NAME_PARTICLES:
        return True
    return bool(re.fullmatch(r"[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’\-]+", token))

def name_word_count(text):
    if not text:
        return 0
    return sum(1 for token in text.split() if is_name_word(token))

def clean_author_text(text):
    if not text:
        return ""
    text = clean_title_text(text)
    text = re.sub(r'\S+@\S+', '', text)
    text = re.sub(r'https?://\S+|www\.\S+', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\bdoi:\S+', '', text, flags=re.IGNORECASE)
    text = re.sub(r'(?<=[A-Za-z])\d+(?=[,\s;]|$)', '', text)
    text = re.sub(r'(?<=\w)\s+[a-z](?=\s*[,;])', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def looks_like_single_person(text):
    text = clean_author_text(text)
    if not text:
        return False
    if looks_like_journal_header(text) or looks_like_publisher_line(text):
        return False
    if is_article_boundary(text) or is_keyword_line(text):
        return False
    if '@' in text:
        return False
        
    lower = text.lower()
    title_preps = {'of', 'for', 'in', 'the', 'a', 'an', 'with', 'on'}
    if set(lower.split()).intersection(title_preps):
        return False
        
    words = text.split()
    if len(words) < 2 or len(words) > 10:
        return False
    return name_word_count(text) >= 2

def has_author_separator(text):
    if not text:
        return False
    lower = text.lower()
    if ',' in text or '&' in text or re.search(r'\band\b', lower):
        return True
    return len(re.findall(r'\b[A-Z]\.?\b', text)) >= 2

def parse_author_line(text):
    text = clean_author_text(text)
    if not text:
        return []
    text = re.sub(r'\s+(?:and|&)\s+', ',', text, flags=re.IGNORECASE).replace(';', ',')
    comma_parts = [p.strip() for p in text.split(',') if p.strip()]

    if len(comma_parts) >= 2:
        valid_parts = [p for p in comma_parts if looks_like_single_person(p)]
        if len(valid_parts) >= 2:
            return valid_parts

    if looks_like_single_person(text):
        return [text]
    return []

def contains_author_list(text):
    if not text:
        return False
    if len(re.findall(r'\b[A-Z]\.', text)) >= 2:
        return True
    if ',' in text:
        parts = [p.strip() for p in text.split(',')]
        person_parts = 0
        for p in parts:
            if not p: continue
            w = re.findall(r'[A-Za-z]+', p)
            if 1 <= len(w) <= 4 and all(word[0].isupper() for word in w):
                person_parts += 1
        if person_parts >= 2 and (person_parts / max(len(parts), 1)) >= 0.5:
            return True
            
    lower = text.lower()
    prepositions = {'of', 'for', 'in', 'on', 'the', 'a', 'an', 'with', 'to', 'by', 'from', 'using', 'via'}
    words = set(re.findall(r'\b[a-z]+\b', lower))
    has_prep = bool(words.intersection(prepositions))
    
    if not has_prep and scientific_term_count(text) == 0:
        if ',' in text or ' and ' in lower:
            return True
            
    if re.search(r'\b[A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+\s*,\s*[A-Z][a-z]+', text):
        return True
    if re.search(r'\b[A-Z]\.\s*[A-Z]\.\s+[A-Z][a-z]+\b', text):
        return True
    return False

def find_structural_boundaries(lines):
    author_idx = None
    abstract_idx = None
    
    for i, line in enumerate(lines[:EXTENDED_TITLE_SEARCH_LINES]):
        text = line['text']
        lower = text.lower()
        
        if is_article_boundary(text) and ('abstract' in lower or 'introduction' in lower or 'background' in lower):
            if abstract_idx is None:
                abstract_idx = i
                
        if author_idx is None:
            if contains_author_list(text):
                author_idx = i
            else:
                authors = parse_author_line(text)
                if len(authors) >= 2:
                    author_idx = i
                    
    return author_idx, abstract_idx


# ============================================================
# TITLE DETECTION
# ============================================================

def looks_like_abstract_sentence(text):
    if not text:
        return False
    normalized = re.sub(r'\s+', ' ', text).strip()
    lower = normalized.lower()
    if 'abstract' in lower or 'citation:' in lower:
        return True
    sentence_starts = (
        'we ', 'this study ', 'this work ', 'in this study ', 'in this work ',
        'here, ', 'here we ', 'the aim ', 'the objective ', 'our study ',
        'our results ', 'these results ', 'results show ', 'we demonstrate ',
        'we show ', 'we investigated ', 'we developed ', 'we present ',
        'we report ', 'we describe ', 'we found ', 'entity linking is',
        'recent advances', 'despite '
    )
    return lower.startswith(sentence_starts)

def looks_like_title(text):
    if not text:
        return False
    text = clean_title_text(text)
    if len(text) < MIN_TITLE_LENGTH or len(text) > MAX_TITLE_LENGTH:
        return False
    lower = text.lower()
    
    if is_keyword_line(text) or is_article_boundary(text):
        return False

    rejected_prefixes = [
        'doi:', 'doi ', 'http://', 'https://', 'www.', 'received',
        'accepted', 'published', 'copyright', 'preprint', 'abstract',
        'keywords', 'key words', 'acknowledg', 'citation:'
    ]
    if any(lower.startswith(prefix) for prefix in rejected_prefixes):
        return False
    if looks_like_journal_header(text) or looks_like_publisher_line(text):
        return False
    if contains_author_list(text):
        return False
    if '@' in text or re.search(r'10\.\d{4,9}/', text, re.IGNORECASE):
        return False

    digits = sum(c.isdigit() for c in text)
    if digits > len(text) * 0.15:
        return False
    if looks_like_single_person(text) or looks_like_abstract_sentence(text):
        return False

    words = text.split()
    if len(words) < 2 or len(words) > MAX_TITLE_WORDS:
        return False
        
    if scientific_term_count(text) == 0 and not has_title_structure(text):
        if not text[0].isupper():
            return False
        if len(words) > 8:
            return False
            
    return True

def title_score(text, index, font_size=None, max_font_size=None):
    if not looks_like_title(text):
        return -999

    words = text.split()
    score = 0.0

    if index <= 2:
        score += 15
    elif index <= 5:
        score += 8
    elif index <= 10:
        score += 2
    elif index > 15:
        score -= 10

    if 3 <= len(words) <= 20:
        score += 5

    score += min(scientific_term_count(text) * 1.0, 4)
    if ':' in text: score += 3
    if has_title_structure(text): score += 2

    if font_size and max_font_size:
        if font_size >= max_font_size - 1.0:
            score += 10
        elif font_size >= max_font_size - 3.0:
            score += 4
        else:
            score -= 8
    return score

def compatible_title_lines(first, second):
    if not first or not second:
        return False
    distance = second['top'] - first['bottom']
    if distance < -2 or distance > TITLE_LINE_DISTANCE:
        return False
    size1 = first.get('font_size')
    size2 = second.get('font_size')
    if size1 and size2 and abs(size1 - size2) > TITLE_FONT_SIZE_TOLERANCE:
        return False
    return True

def extract_title_from_range(lines, start_idx, end_idx):
    candidates = []
    valid_lines = [l for l in lines[start_idx:end_idx] if l.get('top', 999) <= MAX_TITLE_TOP]
    if not valid_lines:
        valid_lines = lines[start_idx:end_idx]
        
    if not valid_lines:
        return None

    max_page_font = max((l.get('font_size') or 0 for l in valid_lines), default=0)

    for i, line in enumerate(valid_lines):
        real_idx = lines.index(line)
        text = line['text']
        score = title_score(text, i, line.get('font_size'), max_page_font)
        if score > -999:
            candidates.append({
                'text': text, 'score': score, 'index': real_idx,
                'top': line['top'], 'bottom': line['bottom'],
                'font_size': line.get('font_size')
            })

    limit_pair = len(valid_lines) - 1
    for i in range(limit_pair):
        first = valid_lines[i]
        second = valid_lines[i + 1]
        real_idx = lines.index(first)

        if not compatible_title_lines(first, second):
            continue

        first_text, second_text = first['text'], second['text']
        if not looks_like_title(first_text) or not looks_like_title(second_text):
            continue

        combined = clean_title_text(first_text + ' ' + second_text)
        if not looks_like_title(combined):
            continue

        score1 = title_score(first_text, i, first.get('font_size'), max_page_font)
        score2 = title_score(second_text, i + 1, second.get('font_size'), max_page_font)

        if score1 <= -999 or score2 <= -999:
            continue

        combined_score = score1 + score2 + 5
        candidates.append({
            'text': combined, 'score': combined_score, 'index': real_idx,
            'top': first['top'], 'bottom': second['bottom'],
            'font_size': first.get('font_size')
        })

    if not candidates:
        return None

    candidates.sort(key=lambda x: (x['score'], -x['index']), reverse=True)
    return candidates[0]

def extract_title(lines):
    if not lines:
        return None
        
    author_idx, abstract_idx = find_structural_boundaries(lines)
    
    if author_idx is not None and author_idx > 0:
        result = extract_title_from_range(lines, 0, author_idx)
        if result:
            return result
            
    start_idx = (author_idx + 1) if author_idx is not None else 0
    end_idx = abstract_idx if abstract_idx is not None else EXTENDED_TITLE_SEARCH_LINES
    
    if start_idx < end_idx:
        result = extract_title_from_range(lines, start_idx, end_idx)
        if result:
            return result
            
    result = extract_title_from_range(lines, 0, TITLE_SEARCH_LINES)
    if result:
        return result
        
    return extract_title_from_range(lines, 0, EXTENDED_TITLE_SEARCH_LINES)


# ============================================================
# AUTHOR EXTRACTION & SCORING
# ============================================================

def author_line_strength(text, line, title_bottom=None):
    text = clean_author_text(text)
    if not text:
        return 0

    if (looks_like_journal_header(text) or looks_like_publisher_line(text) or
            is_article_boundary(text) or is_keyword_line(text)):
        return 0

    authors = parse_author_line(text)
    if not authors:
        return 0

    score = 0.0
    if title_bottom is not None:
        distance = line['top'] - title_bottom
        if -80 <= distance < 10: score += 0.35
        elif 10 <= distance < 25: score += 0.40
        elif 25 <= distance < 45: score += 0.32
        elif 45 <= distance < 70: score += 0.18
        elif 70 <= distance < 100: score += 0.05
        else:
            if distance < -80: score += 0.10
            else: return 0
    else:
        score += 0.20

    if len(authors) == 1: score += 0.18
    elif len(authors) == 2: score += 0.28
    elif len(authors) >= 3: score += 0.25

    if has_author_separator(text): score += 0.15
    if re.search(r'\b[A-Z]\.', text): score += 0.12
    if len(text.split()) > 12: score -= 0.25
    if re.search(r'\d', text): score -= 0.15

    return max(0, min(score, 1))

def extract_author_block(lines, title):
    candidates = []
    title_index = title['index'] if title else -1
    title_bottom = title['bottom'] if title else None
    
    search_start = 0
    search_end = min((title_index + AUTHOR_SEARCH_LINES + 1) if title else EXTENDED_TITLE_SEARCH_LINES, len(lines))

    for i in range(search_start, search_end):
        if i == title_index: continue
        line = lines[i]
        text = clean_author_text(line['text'])
        if not text: continue

        lower = text.lower()
        if is_article_boundary(text) or is_keyword_line(text):
            if i > title_index: break
            else: continue

        if 'doi:' in lower or re.search(r'https?://|www\.', lower): continue

        authors = parse_author_line(text)
        if not authors: continue

        strength = author_line_strength(text, line, title_bottom)
        if strength <= 0: continue

        candidates.append({'text': text, 'authors': authors, 'score': strength, 'index': i})

    if not candidates:
        return None, 0, []

    best_score = max(c['score'] for c in candidates)
    combined = []

    for candidate in candidates:
        if candidate['score'] >= max(best_score - 0.20, AUTHOR_CONFIDENCE_THRESHOLD - 0.20):
            combined.extend(candidate['authors'])

    unique, seen = [], set()
    for author in combined:
        key = normalize_text(author)
        if key and key not in seen:
            seen.add(key)
            unique.append(author)

    if not unique:
        unique = candidates[0]['authors']

    confidence = min(1.0, best_score + (0.10 if len(unique) > 1 else 0))
    return ', '.join(unique), confidence, candidates


# ============================================================
# API & IDENTIFIER ROUTINES (DOI / CROSSREF / DECISION LOGIC)
# ============================================================

def normalize_doi(doi):
    if not doi: return None
    doi = doi.strip()
    doi = re.sub(r'^(?:https?://)?(?:dx\.)?doi\.org/', '', doi, flags=re.I)
    doi = re.sub(r'^doi:\s*', '', doi, flags=re.I)
    return re.sub(r'\s+', '', doi).strip('.,;:)]}')

def extract_doi_from_filename(filename):
    if not filename: return None
    name = os.path.splitext(os.path.basename(filename))[0]
    patterns = [r'(journal\.pone\.\d+)', r'(s\d{4,}-\d{2,4}-\d{4,}-\d+)', r'(10\.\d{4,9}/[-._;()/:A-Z0-9]+)']
    for pattern in patterns:
        match = re.search(pattern, name, re.I)
        if match:
            value = match.group(1)
            if value.lower().startswith('journal.pone.'): value = '10.1371/' + value
            elif value.lower().startswith('s') and value.count('-') >= 2: value = '10.1007/' + value
            return normalize_doi(value)
    return None

def extract_doi(text, filename=None):
    filename_doi = extract_doi_from_filename(filename)
    if not text: return filename_doi
    candidates = []
    explicit_patterns = [r'(?:https?://(?:dx\.)?doi\.org/|doi:\s*)(10\.\d{4,9}/[^\s<>"\']+)', r'\b(10\.\d{4,9}/[^\s<>"\']+)']
    for pattern in explicit_patterns:
        for match in re.finditer(pattern, text, re.I):
            candidates.append(normalize_doi(match.group(1)))
    cleaned = []
    stop_patterns = [r'(?i)(?=received\d)', r'(?i)(?=accepted\d)', r'(?i)(?=availableonline)', r'(?i)(?=originalpaper)', r'(?i)(?=researcharticle)', r'(?i)(?=articleinfo)']
    for doi in candidates:
        value = doi
        for stop in stop_patterns: value = re.split(stop, value, maxsplit=1)[0]
        value = normalize_doi(value)
        if value: cleaned.append(value)
    return filename_doi if filename_doi else (cleaned[0] if cleaned else None)

def fetch_crossref(doi):
    if not doi: return None
    doi = doi.strip()
    url = 'https://api.crossref.org/works/' + doi
    for attempt in range(CROSSREF_RETRY_COUNT + 1):
        try:
            response = requests.get(url, headers={'Accept': 'application/json', 'User-Agent': 'PDF-Renamer/2.0'}, timeout=CROSSREF_TIMEOUT)
            if response.status_code == 200:
                data = response.json().get('message')
                return data
            if response.status_code == 404: return None
        except requests.RequestException:
            if attempt < CROSSREF_RETRY_COUNT: time.sleep(CROSSREF_RETRY_DELAY)
    return None

def fetch_crossref_by_title(title):
    if not title or not USE_CROSSREF_TITLE_SEARCH: return None
    params = {'query.bibliographic': title, 'rows': 5}
    try:
        response = requests.get('https://api.crossref.org/works', params=params, headers={'Accept': 'application/json', 'User-Agent': 'PDF-Renamer/2.0'}, timeout=CROSSREF_TIMEOUT)
        if response.status_code != 200: return None
        items = response.json().get('message', {}).get('items', [])
        best, best_score = None, 0.0
        for item in items:
            titles = item.get('title', [])
            candidate = titles[0] if titles else None
            if not candidate: continue
            score = similarity(title, candidate)
            if score > best_score: best_score, best = score, item
        if best is not None and best_score >= CROSSREF_TITLE_SEARCH_MIN_SIMILARITY: return best
    except requests.RequestException: pass
    return None

def crossref_title(data):
    if not data: return None
    titles = data.get('title', [])
    if titles:
        title = titles[0]
        if title and looks_like_title(title): return clean_title_text(title.strip())
    return None

def crossref_authors(data):
    if not data: return []
    results = []
    for author in data.get('author', []):
        given, family = author.get('given', '').strip(), author.get('family', '').strip()
        if not family: continue
        results.append(f"{given} {family}" if given else family)
    return results

def crossref_year(data):
    if not data: return None
    fields = ['published-print', 'published-online', 'published', 'issued']
    years = []
    for field in fields:
        info = data.get(field)
        if not info: continue
        parts = info.get('date-parts', [])
        if not parts or not parts[0]: continue
        year = valid_year(parts[0][0])
        if year: years.append(year)
    return max(years) if years else None

def split_local_authors(text):
    return parse_author_line(text)

def last_name(author):
    if not author: return None
    author = author.strip()
    if ',' in author:
        first = author.split(',')[0].strip()
        if first: return first
    parts = author.split()
    if not parts: return None
    if len(parts) >= 2 and parts[-2].lower() in NAME_PARTICLES: return parts[-2] + ' ' + parts[-1]
    return parts[-1]

def author_agreement(local_author_text, crossref_list):
    if not local_author_text or not crossref_list: return 0
    local_authors = split_local_authors(local_author_text)
    if not local_authors: return 0
    local_names = [last_name(a).lower() for a in local_authors if last_name(a)]
    cross_names = [last_name(a).lower() for a in crossref_list if last_name(a)]
    if not local_names: return 0
    matches = sum(1 for name in local_names if any(similarity(name, cn) >= 0.80 for cn in cross_names))
    return matches / len(local_names)

def choose_authors(local_author, local_confidence, crossref_list, metadata_author):
    if crossref_list:
        agreement = author_agreement(local_author, crossref_list)
        return crossref_list, 1.0, agreement, 'CrossRef'
    if local_author and local_confidence >= AUTHOR_CONFIDENCE_THRESHOLD:
        local_list = split_local_authors(local_author)
        if local_list: return local_list, local_confidence, 0.0, 'PDF'
    if metadata_author:
        metadata_list = split_local_authors(metadata_author)
        if metadata_list: return metadata_list, 0.50, 0.0, 'PDF/Filename metadata'
    return [], 0.0, 0.0, 'None'

def format_author_list(authors):
    if not authors: return 'Unknown'
    surnames = [last_name(author) for author in authors if last_name(author)]
    if not surnames: return 'Unknown'
    if len(surnames) == 1: return surnames[0]
    if len(surnames) == 2: return f'{surnames[0]} and {surnames[1]}'
    return f'{surnames[0]} et al'

def title_is_credible(title):
    return title and looks_like_title(title) and not is_keyword_line(title)

def title_similarity_score(a, b):
    if not a or not b: return 0.0
    a_norm, b_norm = normalize_text(a), normalize_text(b)
    if not a_norm or not b_norm: return 0.0
    score = similarity(a, b)
    if a_norm in b_norm or b_norm in a_norm:
        score = max(score, min(len(a_norm), len(b_norm)) / max(len(a_norm), len(b_norm)))
    return score

def choose_title(local_title, metadata_title, crossref_title_value, is_biorxiv=False):
    local_valid, metadata_valid, crossref_valid = title_is_credible(local_title), title_is_credible(metadata_title), title_is_credible(crossref_title_value)
    if is_biorxiv:
        if local_valid: return clean_title_text(local_title), 1.0, 'PDF', None
        if metadata_valid: return clean_title_text(metadata_title), 0.75, 'PDF/Filename metadata', None
        return None, 0.0, 'None', None
    if crossref_valid:
        if not local_valid: return clean_title_text(crossref_title_value), 1.0, 'CrossRef', None
        title_similarity = title_similarity_score(local_title, crossref_title_value)
        if title_similarity >= CROSSREF_TITLE_SIMILARITY: return clean_title_text(crossref_title_value), 1.0, 'CrossRef', title_similarity
        if SKIP_ON_TITLE_MISMATCH: return None, 0.0, 'Mismatch', title_similarity
        return clean_title_text(crossref_title_value), 0.85, 'CrossRef (mismatch)', title_similarity
    if local_valid: return clean_title_text(local_title), 0.85, 'PDF', None
    if metadata_valid: return clean_title_text(metadata_title), 0.50, 'PDF/Filename metadata', None
    return None, 0.0, 'None', None

def page_years(text):
    strong, medium, weak = [], [], []
    for line in [line.strip() for line in text.splitlines() if line.strip()][:35]:
        years = years_in_text(line)
        if not years: continue
        lower = line.lower()
        if re.search(r'published|publication|copyright|©', lower): strong.extend(years)
        elif re.search(r'journal|volume|issue|doi|accepted|received', lower): medium.extend(years)
        else: weak.extend(years)
    return strong, medium, weak

def choose_year(crossref_year_value, page_year_data, metadata_year):
    strong, medium, weak = page_year_data
    candidates = []
    if crossref_year_value:
        if year := valid_year(crossref_year_value): candidates.append((year, 100))
    for year in strong: candidates.append((year, 95))
    for year in medium: candidates.append((year, 70))
    if metadata_year:
        if year := valid_year(metadata_year): candidates.append((year, 40))
    for year in weak: candidates.append((year, 20))
    candidates = [item for item in candidates if item[0] <= CURRENT_YEAR]
    if not candidates: return None
    highest_score = max(score for _, score in candidates)
    return max([year for year, score in candidates if score == highest_score])

def print_author_diagnostics(candidates, selected_authors, confidence, crossref_list, agreement, source='Unknown'):
    print("\n  AUTHOR ANALYSIS")
    if candidates:
        for candidate in sorted(candidates, key=lambda x: x['score'], reverse=True):
            print(f"    Candidate: '{candidate['text']}' [confidence {candidate['score']:.2f}]")
    else: print('    No local author candidates found.')
    if selected_authors:
        print(f"    Selected: {', '.join(selected_authors)}\n    Source: {source}\n    Confidence: {confidence:.2f}")
    else: print('    Selected: NONE')
    if crossref_list: print(f"    CrossRef authors: {', '.join(crossref_list[:10])}\n    Author agreement: {agreement:.2f}")

def process_pdfs():
    pdf_files = sorted(f for f in os.listdir('.') if f.lower().endswith('.pdf'))
    if not pdf_files:
        print('No PDF files found in this folder.')
        return
    print(f'\nDRY RUN: {DRY_RUN}\nMAX FILENAME LENGTH: {MAX_FILENAME_LENGTH}')

    for filename in pdf_files:
        print("\n" + "=" * 80 + f'\nProcessing: {filename}\n' + "=" * 80)

        try:
            fn_title, fn_author, fn_year = extract_metadata_from_filename(filename)
            metadata_title, metadata_author, metadata_year = extract_metadata(filename)
            metadata_title = fn_title or metadata_title
            metadata_author = fn_author or metadata_author
            metadata_year = fn_year or metadata_year
            
            words, page = extract_first_page(filename)
            if not words:
                print('  ❌ Could not extract first page.')
                continue

            lines = group_words_into_lines(words)
            first_page_text = '\n'.join(line['text'] for line in lines)
            doi = extract_doi(first_page_text, filename)
            
            biorxiv = 'biorxiv' in (doi or '').lower() or re.search(r'\bbiorxiv\b', first_page_text, re.I)
            print(f"  DOCUMENT TYPE: {'bioRxiv PREPRINT' if biorxiv else 'Publication'}")

            crossref_data = fetch_crossref(doi) if doi else None
            if not crossref_data and USE_CROSSREF_TITLE_SEARCH:
                provisional_title = extract_title(lines)
                provisional_text = (provisional_title['text'] if provisional_title else None) or metadata_title
                if provisional_text:
                    title_search_data = fetch_crossref_by_title(provisional_text)
                    if title_search_data:
                        candidate_title = (title_search_data.get('title') or [''])[0]
                        match_score = title_similarity_score(provisional_text, candidate_title)
                        if match_score >= CROSSREF_TITLE_SEARCH_MIN_SIMILARITY:
                            doi = normalize_doi(title_search_data.get('DOI'))
                            if doi:
                                crossref_data = title_search_data
                                print(f'  CROSSREF TITLE SEARCH: MATCHED ({match_score:.2f})\n  RECOVERED DOI: {doi}')

            cr_title, cr_authors, cr_year = crossref_title(crossref_data), crossref_authors(crossref_data), crossref_year(crossref_data)
            title_candidate = extract_title(lines)
            local_title = title_candidate['text'] if title_candidate else None

            final_title, title_confidence, title_source, title_similarity = choose_title(local_title, metadata_title, cr_title, is_biorxiv=biorxiv)
            if not final_title:
                print('  ⚠️ No reliable title could be determined. File will NOT be renamed.')
                continue

            local_author, local_confidence, author_candidates = extract_author_block(lines, title_candidate)
            final_authors, author_confidence, agreement, author_source = choose_authors(local_author, local_confidence, cr_authors, metadata_author)

            if not final_authors:
                print('  ⚠️ No reliable author could be determined. File will NOT be renamed.')
                continue

            print_author_diagnostics(author_candidates, final_authors, author_confidence, cr_authors, agreement, author_source)
            formatted_author = format_author_list(final_authors)

            if biorxiv: final_year, year_confidence = 'PREPRINT', 1.0
            else:
                final_year = choose_year(cr_year, page_years(first_page_text), metadata_year)
                year_confidence = 1.0 if cr_year else (0.75 if final_year else 0.0)

            if final_year is None:
                print('  ⚠️ No reliable publication year found. File will NOT be renamed.')
                continue

            final_title = clean_filename(final_title)
            prefix = f'({formatted_author} {final_year}) '
            if MAX_FILENAME_LENGTH is not None and len(final_title) > MAX_FILENAME_LENGTH - len(prefix) - 4:
                final_title = final_title[:MAX_FILENAME_LENGTH - len(prefix) - 4].rstrip()
                if ' ' in final_title: final_title = final_title.rsplit(' ', 1)[0].rstrip()

            new_name = limit_filename_length(clean_filename(prefix + final_title + '.pdf'))
            final_confidence = min(title_confidence, author_confidence, year_confidence)
            print(f'\n  FINAL RESULT\n    New filename: {new_name}\n    Confidence: {final_confidence:.2f}')

            if final_confidence < MIN_FINAL_CONFIDENCE:
                print(f'  ⚠️ Confidence below {MIN_FINAL_CONFIDENCE:.2f}. File will NOT be renamed.')
            elif new_name == filename:
                print('  ℹ️ Filename already matches.')
            elif DRY_RUN:
                print('  ℹ️ DRY RUN: file was NOT renamed.')
            elif os.path.exists(new_name):
                print('  ⚠️ Target file already exists. File was NOT renamed.')
            else:
                os.rename(filename, new_name)
                print('  ✅ Renamed successfully.')
        except Exception as e:
            print(f'  ❌ Unexpected error: {e}')

if __name__ == "__main__": process_pdfs()

<img width="3026" height="1277" alt="Picture1" src="https://github.com/user-attachments/assets/3608233d-45d0-4984-b099-82b867348af6" />

# PDF Renamer

This Python script automatically renames scientific PDF files using bibliographic information extracted directly from the document.

It identifies article titles, authors, publication years, and DOIs from PDF content and metadata, then validates information against CrossRef when available. The title detection system uses PDF layout reconstruction, font-size analysis, scientific language recognition, journal-header filtering, publisher detection, affiliation rejection, and author-block detection to improve accuracy across a wide range of publishers and journal formats.

The script specifically detects and handles bioRxiv preprints, preventing licensing text, copyright notices, journal headers, and other publisher metadata from being mistaken for article titles. For bioRxiv documents, filenames are labeled with **PREPRINT** instead of a publication year.

PDFs are processed automatically from the folder containing the script, with detailed diagnostic output showing how titles, authors, years, and DOI metadata were selected.

## Filename Format

Standard publications:

```text
(Author Year) Title.pdf
```

Examples:

```text
(Smith 2023) Engineering Yeast for Sustainable Production.pdf
(Smith and Jones 2021) Genome Analysis of Marine Bacteria.pdf
(Smith et al 2024) High-Throughput Screening of Novel Enzymes.pdf
```

bioRxiv preprints:

```text
(Smith et al PREPRINT) Discovery of Novel Plastic-Degrading Enzymes.pdf
```

More than two authors are automatically shortened to **et al**.

## Example

This scientific article:

https://doi.org/10.1177/2472555218803064

may download as:

```text
PIIS2472555222126262.pdf
```

The script renames it to:

```text
(Colin et al 2019) High-Throughput DNA Plasmid Transfection Using Acoustic Droplet Ejection Technology.pdf
```

## Installation

Install the required Python packages:

```bash
pip install requests pdfplumber PyPDF2
```

## How to Use

1. Place the script in a folder containing the PDF files you want to rename.
2. Open a terminal in that folder.
3. Run:

```bash
python pdf_renamer.py
```

The script will:

- Extract article metadata from each PDF
- Detect and validate DOIs
- Query CrossRef when DOI metadata is available
- Identify the most likely title, author(s), and publication year
- Detect bioRxiv preprints
- Automatically rename files
- Print detailed diagnostics for every processed document

## Requirements

- Python 3.x
- requests
- pdfplumber
- PyPDF2

## Notes

### Standard Publications

When a DOI and CrossRef metadata are available, the script uses them to validate and improve extracted information. CrossRef titles are preferred when they provide a more reliable canonical title than the PDF layout extraction.

### bioRxiv Preprints

bioRxiv preprints are detected using DOI patterns and preprint-specific text. Publication years are intentionally replaced with **PREPRINT** to distinguish manuscripts from peer-reviewed publications.

### Limitations

- Requires text-based PDFs (image-only scans are not supported).\

## Versions
v260903
- Fixed spatial line-number cropping issue that interfered with metadata extraction from some preprints.
- Added isolated line-number detection and filtering during PDF text processing.
- Improved title extraction with better scoring, multiline title assembly, and abstract detection.
- Improved author extraction and parsing of complex author lists.
- Added filename-based metadata recovery when PDF metadata is incomplete.
- Added title cleanup/normalization (ligatures, special characters, hyphenation fixes, footnote markers).
- Enhanced CrossRef integration with retries, timeouts, DOI extraction, and title-based lookups.
- Improved title matching confidence and metadata validation logic.
- Added more robust publication year selection/ranking.
- Updated author formatting to produce "Author", "Author and Coauthor", or "Author et al" output.
- Expanded configuration options for search depth, filename limits, confidence thresholds, and API behavior.

v260830
- Removed review-article detection and filename suffixing.
- Expanded journal and publisher detection terms.
- Improved title-selection reliability.
- Retained CrossRef-assisted metadata validation.
- Retained specialized bioRxiv preprint handling.
- CrossRef validation requires an internet connection.
- PDFs without a detectable title, author, or year may be skipped.
- Some highly unusual publisher layouts may still require manual review.

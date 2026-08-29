# PDF Renamer

This Python script automatically renames scientific PDF files using bibliographic information extracted from the document.
It identifies titles, authors, publication years, and DOIs from PDF content and metadata, then validates information against CrossRef when available.

The script specifically detects and handles bioRxiv preprints, avoiding misleading header or licensing text.
It also identifies review articles and applies confidence-based author selection.
Diagnostic output is provided during processing to show extracted and selected metadata.

PDFs are processed automatically from the folder containing the script. Requires Python with `requests`, `pdfplumber`, and `PyPDF2` installed.

"(Name, Year) Title" is the standard format. More than two authors will result in "et al". A preprint from bioRxiv results in "PREPRINT" instead of a year.


###EXAMPLE###

This scientific article https://doi.org/10.1177/2472555218803064 will download a pdf "PIIS2472555222126262.pdf"

The script renames it to "(Colin et al 2019) High-Throughput DNA Plasmid Transfection Using Acoustic Droplet Ejection Technology"

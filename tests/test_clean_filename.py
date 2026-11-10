"""Tests for filename cleaning in PDF Renamer (slot 811)."""

import importlib.util
import os
import re

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULE_PATH = os.path.join(REPO_DIR, "PDF Renamer.py")


def load_module():
    spec = importlib.util.spec_from_file_location("pdf_renamer", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pdf_renamer = load_module()


def test_clean_filename_strips_control_characters():
    # Control characters (0x00-0x1F) are rejected by Windows filesystems;
    # keeping them makes the later os.rename() crash.
    assert pdf_renamer.clean_filename("a\x01b\x7fc") == "ab\x7fc"


def test_clean_filename_strips_reserved_characters():
    assert pdf_renamer.clean_filename('a<b>c:d"e/f\\g|h?i*j') == "abcdefghij"


def test_clean_filename_trims_whitespace_and_dots():
    assert pdf_renamer.clean_filename("  report. ") == "report"


def test_clean_filename_empty_stays_empty():
    assert pdf_renamer.clean_filename("") == ""
    assert pdf_renamer.clean_filename(None) == ""


def test_cleaned_name_has_no_reserved_or_control_chars():
    name = pdf_renamer.clean_filename("Paper (2020)\x00: final?.pdf")
    assert re.search(r'[<>:"/\\|?*\x00-\x1f]', name) is None

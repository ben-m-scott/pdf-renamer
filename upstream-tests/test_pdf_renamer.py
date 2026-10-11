"""Tests for ben-m-scott/pdf-renamer ("PDF Renamer.py", MIT).

Scope: 纯函数 —— 文件名清理、标题归一化、作者解析、DOI 提取、
年份选择。不碰真实 PDF 文件解析、不碰网络（CrossRef）、
不执行 process_pdfs()。
"""
import os


# ---------------------------------------------------------------- clean_filename
class TestCleanFilename:
    def test_removes_illegal_chars(self, pr):
        assert pr.clean_filename('a<b>:c"d/e\\f|g?h*i') == "abcdefghi"

    def test_collapses_whitespace(self, pr):
        assert pr.clean_filename("a  b\tc") == "a b c"

    def test_strips_trailing_dots(self, pr):
        assert pr.clean_filename("report...") == "report"

    def test_empty_and_none(self, pr):
        assert pr.clean_filename("") == ""
        assert pr.clean_filename(None) == ""

    def test_normal_unchanged(self, pr):
        assert pr.clean_filename("My Paper 2020") == "My Paper 2020"


# ---------------------------------------------------------------- limit_filename_length
class TestLimitFilenameLength:
    def test_short_unchanged(self, pr):
        name = "short.pdf"
        assert pr.limit_filename_length(name) == name

    def test_long_truncated_with_pdf_suffix(self, pr):
        name = "a" * 200 + ".pdf"
        out = pr.limit_filename_length(name)
        assert len(out) == pr.MAX_FILENAME_LENGTH
        assert out.endswith(".pdf")

    def test_truncates_at_word_boundary(self, pr):
        name = "word " * 60 + ".pdf"
        out = pr.limit_filename_length(name)
        assert out.endswith(".pdf")
        assert len(out) <= pr.MAX_FILENAME_LENGTH
        assert not out[: -len(".pdf")].endswith(" ")

    def test_no_extension(self, pr):
        name = "b" * 200
        out = pr.limit_filename_length(name)
        assert len(out) <= pr.MAX_FILENAME_LENGTH

    def test_falsy_max_length_returns_input(self, pr):
        name = "x" * 200 + ".pdf"
        assert pr.limit_filename_length(name, max_length=0) == name


# ---------------------------------------------------------------- clean_title_text / normalize_text
class TestCleanTitleText:
    def test_ligatures(self, pr):
        assert pr.clean_title_text("ﬁle ﬂow") == "file flow"

    def test_smart_quotes_and_dashes(self, pr):
        assert pr.clean_title_text("\u201cHello\u201d \u2013 world") == '"Hello" - world'

    def test_html_tags_stripped(self, pr):
        assert pr.clean_title_text("<i>Quantum</i> Effects") == "Quantum Effects"

    def test_footnote_symbols_removed(self, pr):
        assert pr.clean_title_text("Theory\u2020") == "Theory"

    def test_hyphenated_linebreak_joined(self, pr):
        assert pr.clean_title_text("electro- magnetic") == "electromagnetic"

    def test_empty(self, pr):
        assert pr.clean_title_text("") == ""


class TestNormalizeText:
    def test_lowercase_and_punctuation(self, pr):
        assert pr.normalize_text("Hello, World!") == "hello world"

    def test_empty(self, pr):
        assert pr.normalize_text("") == ""

    def test_applies_title_cleaning_first(self, pr):
        assert pr.normalize_text("\ufb01") == "fi"


class TestSimilarity:
    def test_identical_is_one(self, pr):
        assert pr.similarity("deep learning", "deep learning") == 1.0

    def test_empty_is_zero(self, pr):
        assert pr.similarity("", "abc") == 0.0
        assert pr.similarity("abc", "") == 0.0

    def test_unrelated_is_low(self, pr):
        assert pr.similarity("abcdef", "xyz") < 0.5

    def test_case_insensitive(self, pr):
        assert pr.similarity("Deep Learning", "deep learning") == 1.0


# ---------------------------------------------------------------- year handling
class TestValidYear:
    def test_valid_string(self, pr):
        assert pr.valid_year("2020") == 2020

    def test_valid_int(self, pr):
        assert pr.valid_year(1999) == 1999

    def test_too_old(self, pr):
        assert pr.valid_year("1800") is None

    def test_future_rejected(self, pr):
        assert pr.valid_year(str(pr.CURRENT_YEAR + 5)) is None

    def test_non_numeric(self, pr):
        assert pr.valid_year("abc") is None
        assert pr.valid_year(None) is None


class TestYearsInText:
    def test_extracts_years(self, pr):
        assert pr.years_in_text("Published in 2020, revised 2021") == [2020, 2021]

    def test_none_found(self, pr):
        assert pr.years_in_text("no years here") == []

    def test_empty(self, pr):
        assert pr.years_in_text("") == []


class TestPageYears:
    def test_strong_copyright(self, pr):
        strong, medium, weak = pr.page_years("\u00a9 2020 Elsevier\nSome text")
        assert strong == [2020]
        assert medium == [] and weak == []

    def test_medium_journal(self, pr):
        strong, medium, weak = pr.page_years("Journal of Testing, vol 3, 2019")
        assert medium == [2019]

    def test_weak_plain_mention(self, pr):
        strong, medium, weak = pr.page_years("We did this in 2018.")
        assert weak == [2018]


class TestChooseYear:
    def test_crossref_beats_weak(self, pr):
        assert pr.choose_year(2020, ([], [], [2019]), None) == 2020

    def test_strong_beats_weak_without_crossref(self, pr):
        assert pr.choose_year(None, ([2018], [], [2019]), None) == 2018

    def test_metadata_used_as_fallback(self, pr):
        assert pr.choose_year(None, ([], [], []), "2017") == 2017

    def test_nothing_returns_none(self, pr):
        assert pr.choose_year(None, ([], [], []), None) is None

    def test_future_years_filtered(self, pr):
        future = pr.CURRENT_YEAR + 3
        assert pr.choose_year(future, ([], [], []), None) is None


# ---------------------------------------------------------------- filename metadata
class TestExtractMetadataFromFilename:
    def test_parenthesized_pattern(self, pr):
        title, author, year = pr.extract_metadata_from_filename(
            "(Smith 2020) Deep Learning.pdf"
        )
        assert (title, author, year) == ("Deep Learning", "Smith", "2020")

    def test_no_match_returns_nones(self, pr):
        assert pr.extract_metadata_from_filename("random_paper.pdf") == (
            None,
            None,
            None,
        )

    def test_directory_prefix_ignored(self, pr):
        title, author, year = pr.extract_metadata_from_filename(
            os.path.join("tmp", "(Smith 2020) Deep Learning.pdf")
        )
        assert (title, author, year) == ("Deep Learning", "Smith", "2020")


# ---------------------------------------------------------------- DOI
class TestNormalizeDoi:
    def test_none(self, pr):
        assert pr.normalize_doi(None) is None

    def test_url_prefix_stripped(self, pr):
        assert pr.normalize_doi("https://doi.org/10.1000/xyz.123") == "10.1000/xyz.123"

    def test_doi_prefix_stripped(self, pr):
        assert pr.normalize_doi("doi:10.1000/xyz") == "10.1000/xyz"

    def test_trailing_punctuation_stripped(self, pr):
        assert pr.normalize_doi("10.1000/xyz.") == "10.1000/xyz"

    def test_whitespace_removed(self, pr):
        assert pr.normalize_doi("  10.1000/xyz  ") == "10.1000/xyz"


class TestExtractDoiFromFilename:
    def test_plos_pattern(self, pr):
        assert (
            pr.extract_doi_from_filename("journal.pone.1234567.pdf")
            == "10.1371/journal.pone.1234567"
        )

    def test_springer_pattern(self, pr):
        assert (
            pr.extract_doi_from_filename("s41586-021-03412-5.pdf")
            == "10.1007/s41586-021-03412-5"
        )

    def test_elsevier_pii(self, pr):
        assert (
            pr.extract_doi_from_filename("PIIS2472555222126262.pdf")
            == "10.1016/S2472-5552(22)12626-2"
        )

    def test_none_input(self, pr):
        assert pr.extract_doi_from_filename(None) is None

    def test_no_doi(self, pr):
        assert pr.extract_doi_from_filename("my_notes.pdf") is None


class TestExtractDoi:
    def test_explicit_doi_in_text(self, pr):
        assert pr.extract_doi("Contact: doi:10.1000/abc-def", None) == "10.1000/abc-def"

    def test_doi_org_url_in_text(self, pr):
        assert (
            pr.extract_doi("See https://doi.org/10.1000/abc for details", None)
            == "10.1000/abc"
        )

    def test_falls_back_to_filename(self, pr):
        assert (
            pr.extract_doi("no doi here", "journal.pone.1234567.pdf")
            == "10.1371/journal.pone.1234567"
        )

    def test_nothing_returns_none(self, pr):
        assert pr.extract_doi("plain text", "notes.pdf") is None


# ---------------------------------------------------------------- authors
class TestLastName:
    def test_plain(self, pr):
        assert pr.last_name("John Smith") == "Smith"

    def test_comma_form(self, pr):
        assert pr.last_name("Smith, John") == "Smith"

    def test_particle_kept(self, pr):
        assert pr.last_name("Ludwig van Beethoven") == "van Beethoven"

    def test_empty_and_none(self, pr):
        assert pr.last_name("") is None
        assert pr.last_name(None) is None


class TestFormatAuthorList:
    def test_empty(self, pr):
        assert pr.format_author_list([]) == "Unknown"

    def test_single(self, pr):
        assert pr.format_author_list(["John Smith"]) == "Smith"

    def test_two(self, pr):
        assert pr.format_author_list(["John Smith", "Jane Doe"]) == "Smith and Doe"

    def test_three_plus(self, pr):
        assert (
            pr.format_author_list(["John Smith", "Jane Doe", "Bob Lee"])
            == "Smith et al"
        )


class TestParseAuthorLine:
    def test_comma_separated(self, pr):
        assert pr.parse_author_line("John Smith, Jane Doe") == [
            "John Smith",
            "Jane Doe",
        ]

    def test_and_separated(self, pr):
        assert pr.parse_author_line("John Smith and Jane Doe") == [
            "John Smith",
            "Jane Doe",
        ]

    def test_single(self, pr):
        assert pr.parse_author_line("John Smith") == ["John Smith"]

    def test_empty(self, pr):
        assert pr.parse_author_line("") == []


class TestCleanAuthorText:
    def test_removes_email(self, pr):
        assert pr.clean_author_text("John Smith john@uni.edu") == "John Smith"

    def test_removes_url(self, pr):
        out = pr.clean_author_text("Jane Doe https://uni.edu/~doe")
        assert "http" not in out and "Jane Doe" in out

    def test_plain_preserved(self, pr):
        assert pr.clean_author_text("John Smith") == "John Smith"


class TestAuthorAgreement:
    def test_full_match(self, pr):
        assert pr.author_agreement("John Smith", ["John Smith"]) == 1.0

    def test_no_overlap(self, pr):
        assert pr.author_agreement("John Smith", ["Jane Doe"]) == 0.0

    def test_empty_inputs(self, pr):
        assert pr.author_agreement("", ["John Smith"]) == 0
        assert pr.author_agreement("John Smith", []) == 0


class TestChooseAuthors:
    def test_crossref_wins(self, pr):
        authors, conf, agree, src = pr.choose_authors(
            "John Smith", 0.9, ["John Smith"], None
        )
        assert authors == ["John Smith"] and src == "CrossRef"

    def test_local_when_confident(self, pr):
        authors, conf, agree, src = pr.choose_authors("John Smith", 0.9, [], None)
        assert authors == ["John Smith"] and src == "PDF"

    def test_metadata_fallback(self, pr):
        authors, conf, agree, src = pr.choose_authors("", 0.0, [], "Jane Doe")
        assert authors == ["Jane Doe"]

    def test_nothing(self, pr):
        assert pr.choose_authors("", 0.0, [], None) == ([], 0.0, 0.0, "None")


# ---------------------------------------------------------------- title heuristics
class TestIsArticleTypeHeader:
    def test_research_article(self, pr):
        assert pr.is_article_type_header("Research Article") is True

    def test_person_is_not(self, pr):
        assert pr.is_article_type_header("John Smith") is False

    def test_empty(self, pr):
        assert pr.is_article_type_header("") is False


class TestIsKeywordLine:
    def test_keywords(self, pr):
        assert pr.is_keyword_line("Keywords: foo bar") is True

    def test_normal_text(self, pr):
        assert pr.is_keyword_line("Deep learning") is False


class TestIsArticleBoundary:
    def test_abstract(self, pr):
        assert pr.is_article_boundary("Abstract") is True

    def test_introduction(self, pr):
        assert pr.is_article_boundary("Introduction") is True

    def test_title_like(self, pr):
        assert pr.is_article_boundary("Deep Learning Methods") is False


class TestLooksLikeTitle:
    def test_good_title(self, pr):
        assert (
            pr.looks_like_title(
                "Deep Learning Approaches for Medical Image Segmentation"
            )
            is True
        )

    def test_abstract_rejected(self, pr):
        assert pr.looks_like_title("Abstract") is False

    def test_person_rejected(self, pr):
        assert pr.looks_like_title("John Smith") is False

    def test_too_short(self, pr):
        assert pr.looks_like_title("a") is False

    def test_keywords_rejected(self, pr):
        assert pr.looks_like_title("Keywords: deep learning, vision") is False


class TestTitleSimilarityScore:
    def test_identical(self, pr):
        assert pr.title_similarity_score("Deep Learning", "Deep Learning") == 1.0

    def test_empty(self, pr):
        assert pr.title_similarity_score("", "Deep Learning") == 0.0

    def test_substring_boost(self, pr):
        score = pr.title_similarity_score("Deep Learning", "Deep Learning Methods")
        assert 0.7 < score < 1.0


class TestHasAuthorSeparator:
    def test_comma(self, pr):
        assert pr.has_author_separator("Smith, Doe") is True

    def test_single_name(self, pr):
        assert pr.has_author_separator("John Smith") is False

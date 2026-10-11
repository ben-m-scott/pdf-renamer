"""Shared fixtures for ben-m-scott/pdf-renamer tests.

Scope: 纯函数（文件名清理/标题归一化/作者解析/DOI/年份选择）。
不碰真实 PDF 解析（extract_metadata/extract_first_page）、不碰网络
（fetch_crossref*/CrossRef）、不跑 process_pdfs。

被测模块文件名含空格（"PDF Renamer.py"），用 importlib 按路径加载。
模块顶层有 os.chdir(SCRIPT_DIR) 副作用，fixture 负责恢复 cwd。
"""
import importlib.util
import os

import pytest

MODULE_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "PDF Renamer.py")
)
_CWD_BEFORE_IMPORT = os.getcwd()


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "pdf_renamer_under_test", MODULE_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def pr():
    """The loaded 'PDF Renamer.py' module (import side effects isolated)."""
    os.chdir(_CWD_BEFORE_IMPORT)
    module = _load_module()
    os.chdir(_CWD_BEFORE_IMPORT)  # 抵消模块顶层的 os.chdir(SCRIPT_DIR)
    return module


@pytest.fixture(autouse=True)
def _restore_cwd():
    yield
    os.chdir(_CWD_BEFORE_IMPORT)

import hashlib
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"

MINI_PDF = (
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R 6 0 R]/Count 2>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 100]/Contents 4 0 R"
    b"/Resources<</Font<</F1 5 0 R>>>>>>endobj\n"
    b"4 0 obj<</Length 52>>stream\nBT /F1 12 Tf 10 50 Td (first page sample) Tj ET\nendstream endobj\n"
    b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
    b"6 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 100]/Contents 7 0 R"
    b"/Resources<</Font<</F1 5 0 R>>>>>>endobj\n"
    b"7 0 obj<</Length 52>>stream\nBT /F1 12 Tf 10 50 Td (second page keyword) Tj ET\nendstream endobj\n"
    b"trailer<</Root 1 0 R/Size 8>>\n%%EOF\n"
)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FakeNet:
    def __init__(self, answers):
        self.answers = answers
        self.requests_made = []

    def __call__(self, url):
        from sourceshelf.net import NetError

        self.requests_made.append(url)
        answer = self.answers.get(url)
        if answer is None:
            raise NetError(f"no connection: {url}")
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture
def arxiv_xml():
    return (FIXTURES / "arxiv-2501.00001.xml").read_bytes()


@pytest.fixture
def mini_pdf():
    return MINI_PDF

import pytest
from app.verify import verify_quote, normalize_text, number_in_quote

PAGES = [
    "Title page\nAbstract: We report the champion PCE of 21.3 % for the device.",
    "The efficient ﬁlm showed a high ﬂux and a Voc of 1.12 V (reverse scan).\nthe perov-\nskite layer was annealed at 100 °C.",
    "Jsc of 24.5 mA/cm2 and FF of 78 % were obtained;\nVoc = −1.05 V and the layer­thickness was 450 nm.",
]


def test_exact_on_given_page():
    assert verify_quote(PAGES, 1, "champion PCE of 21.3 %") == (True, 1)


def test_case_and_whitespace_collapsed():
    assert verify_quote(PAGES, 1, "CHAMPION   pce\nof 21.3 %") == (True, 1)


def test_ligatures():
    assert verify_quote(PAGES, 2, "efficient film showed a high flux") == (True, 2)
    assert verify_quote(PAGES, 2, "efficient ﬁlm showed a high ﬂux") == (True, 2)


def test_hyphen_linebreak_joined_and_kept():
    assert verify_quote(PAGES, 2, "the perovskite layer was annealed") == (True, 2)
    assert verify_quote(PAGES, 2, "the perov-skite layer was annealed")[0] is True
    assert verify_quote(PAGES, 2, "perov-\nskite layer") == (True, 2)


def test_unicode_minus_and_soft_hyphen():
    assert verify_quote(PAGES, 3, "Voc = -1.05 V") == (True, 3)
    assert verify_quote(PAGES, 3, "the layer thickness was 450 nm") == (True, 3)


def test_number_on_other_page_is_found_there():
    ok, page = verify_quote(PAGES, 1, "Jsc of 24.5 mA/cm2 and FF of 78 %")
    assert ok and page == 3


def test_fabricated_quote_rejected():
    assert verify_quote(PAGES, 1, "champion PCE of 25.9 % for the device") == (False, None)
    assert verify_quote(PAGES, 3, "the cell reached a record efficiency of 30 %") == (False, None)


def test_substituted_number_rejected():
    assert verify_quote(PAGES, 3, "Jsc of 24.9 mA/cm2 and FF of 78 %")[0] is False
    assert verify_quote(PAGES, 3, "Jsc of 24.5 mA/cm2 and FF of 87 %")[0] is False


def test_empty_quote_not_verified():
    assert verify_quote(PAGES, 1, "") == (False, None)
    assert verify_quote(PAGES, 1, "   ") == (False, None)
    assert verify_quote(PAGES, 1, None) == (False, None)


def test_bad_page_number_searches_all():
    assert verify_quote(PAGES, 99, "FF of 78 %") == (True, 3)
    assert verify_quote(PAGES, None, "FF of 78 %") == (True, 3)
    assert verify_quote(PAGES, "2", "FF of 78 %") == (True, 3)


def test_ellipsis_parts():
    assert verify_quote(PAGES, 3, "Jsc of 24.5 mA/cm2 ... thickness was 450 nm") == (True, 3)
    assert verify_quote(PAGES, 3, "Jsc of 24.5 mA/cm2 … thickness was 999 nm")[0] is False


def test_glued_words_lenient():
    pages = ["Thechampion PCE wasreported as 20.1% inTable 1 for the device"]
    assert verify_quote(pages, 1, "The champion PCE was reported as 20.1% in Table 1")[0] is True
    assert verify_quote(pages, 1, "The champion PCE was reported as 21.1% in Table 1")[0] is False


def test_table_pipes_ignored():
    pages = ["[TABLE]\nDevice | PCE (%) | Voc (V)\nControl | 18.2 | 1.05\n[/TABLE]"]
    assert verify_quote(pages, 1, "Control 18.2 1.05")[0] is True


def test_apostrophes_and_dashes():
    pages = ["The device’s fill factor – measured under AM1.5G — was 80%"]
    assert verify_quote(pages, 1, "The device's fill factor - measured under AM1.5G - was 80%")[0] is True


def test_empty_pages():
    assert verify_quote([], 1, "anything") == (False, None)
    assert verify_quote(["", ""], 1, "anything") == (False, None)


def test_normalize_text_modes():
    assert normalize_text("a-\nb") == "ab"
    assert normalize_text("a-\nb", join_breaks=False) == "a-b"
    assert normalize_text("  Ёж  ТЕСТ ") == "еж тест"


@pytest.mark.parametrize("val,frag,ok", [("21.3", "PCE of 21.3 %", True), ("21,3", "PCE of 21.3 %", True), ("1.12", "Voc 1.12 V", True),
                                         ("−1.05", "Voc = -1.05 V", True), ("21.3", "PCE of 12.3 %", False), ("0.85", "FF 85 %", False),
                                         ("text", "whatever", True)])
def test_number_in_quote(val, frag, ok):
    assert number_in_quote(val, frag) is ok


def test_multiline_table_quote_lines_must_all_exist():
    from app import verify as v
    page = "Intro text.\n[TABLE]\nMetric | BM25 baseline | Hybrid + rerank\nRecall@5 | 0.70 | 0.81\nnDCG@10 | 0.55 | 0.64\n[/TABLE]\nEnd."
    assert v.verify_quote([page], 1, "Metric | BM25 baseline | Hybrid + rerank\nnDCG@10 | 0.55 | 0.64") == (True, 1)
    assert v.verify_quote([page], 1, "Metric | BM25 baseline | Hybrid + rerank\nnDCG@10 | 0.99 | 0.64")[0] is False

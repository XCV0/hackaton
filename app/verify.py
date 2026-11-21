"""Проверка дословных цитат: нормализованный поиск фрагмента в тексте страницы, затем в остальных."""
import re, unicodedata
from decimal import Decimal, InvalidOperation

_LIG = {"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st", "Ĳ": "IJ", "ĳ": "ij"}
_DASHES = "‐‑‒–—―−﹘﹣－⁃˗"
_DASH_RE = re.compile("[%s]" % re.escape(_DASHES))
_APOS_RE = re.compile("[’‘ʼ`´′]")
_QUOT_RE = re.compile("[“”„«»]")
_INVIS_RE = re.compile("[­​‌‍⁠﻿]")
_BREAK_RE = re.compile(r"(?<=\w)-[ \t]*\r?\n[ \t]*(?=\w)")


def _base(s):
    s = s or ""
    for k, v in _LIG.items():
        s = s.replace(k, v)
    s = unicodedata.normalize("NFKC", s)
    s = _INVIS_RE.sub("", s)
    s = _DASH_RE.sub("-", s)
    s = _APOS_RE.sub("'", s)
    s = _QUOT_RE.sub('"', s)
    return s.lower().replace("ё", "е")


def normalize_text(s, join_breaks=True):
    """Регистр, лигатуры, юникод-минусы/тире/апострофы, мягкие переносы, переносы слов, схлопнутые пробелы.
    join_breaks=True: «слово-\\nпродолжение» -> «словопродолжение»; False: -> «слово-продолжение»."""
    s = _base(s)
    s = _BREAK_RE.sub("" if join_breaks else "-", s)
    return re.sub(r"\s+", " ", s).strip()


def _lenient(s):
    """Ключ без пробелов, дефисов и табличных разделителей — на случай склеенных/разорванных извлечением слов."""
    return re.sub(r"[\s\-|]+", "", _BREAK_RE.sub("", _base(s)))


def _parts(fragment, lines=False):
    """Цитата с многоточием («…», «...») проверяется по частям; lines=True — ещё и по строкам (шапка + строка таблицы)."""
    parts = re.split(r"\.\.\.|…|\n" if lines else r"\.\.\.|…", fragment or "")
    return [p for p in parts if re.search(r"\w", p)]


def _found_in(parts, page_text):
    if not page_text:
        return False
    strict = [normalize_text(page_text, True), normalize_text(page_text, False)]
    qs = [normalize_text(p) for p in parts]
    if all(any(q in t for t in strict) for q in qs):
        return True
    lt = _lenient(page_text)
    lq = [_lenient(p) for p in parts]
    return all(q and q in lt for q in lq)


def verify_quote(pages_text, page, fragment):
    """pages_text — тексты страниц (страница 1 = индекс 0). Возвращает (ok, found_page); found_page 1-based.
    Пустая цитата = не верифицирована. Сначала указанная страница, затем остальные от ближайшей."""
    parts = _parts(fragment)
    if not parts:
        return False, None
    line_parts = _parts(fragment, lines=True)
    n = len(pages_text)
    try:
        page = int(page)
    except (TypeError, ValueError):
        page = None
    order = []
    if page and 1 <= page <= n:
        order.append(page)
    rest = sorted((p for p in range(1, n + 1) if p != page), key=lambda p: abs(p - (page or 1)))
    for p in order + rest:
        if _found_in(parts, pages_text[p - 1]):
            return True, p
    if len(line_parts) > len(parts):      # многострочная цитата: каждая строка дословно на одной и той же странице
        for p in order + rest:
            if _found_in(line_parts, pages_text[p - 1]):
                return True, p
    return False, None


_NUMBER = re.compile(r"(?<![\w@.,])([+-]?(?:\d+[.,]\d+|[.,]\d+|\d+)(?:e[+-]?\d+)?)(?![\d]|[.,]\d)")


def _numbers(text):
    values = []
    for token in _NUMBER.findall(_base(text)):
        try:
            values.append(Decimal(token.replace(",", ".")))
        except InvalidOperation:
            continue
    return values


def number_in_quote(value, fragment):
    """Проверяет числа исходного значения, включая знак, экспоненту и погрешность."""
    expected = _numbers(str(value or ""))
    actual = _numbers(fragment)
    return all(actual.count(n) >= expected.count(n) for n in set(expected))

#!/usr/bin/env python3
"""Add a page-list (page numbers) to an EPUB so that Kindle and other readers show pages.

Kindle (Send to Kindle) shows real page numbers only when the EPUB has a
page-list: <nav epub:type="page-list"> in the EPUB 3 nav document and/or
<pageList> in the NCX. This module builds one. It has no dependencies outside
the Python standard library, so it works both inside calibre and as a
command-line script.

Modes:
  existing   The book already contains print-page anchors
             (epub:type="pagebreak", role="doc-pagebreak" or ids like
             "page12"); just build the page-list from them.
  synthetic  Estimate pages like a typesetter would: every spine document
             (and every CSS page-break-before/after, or --break-on tag)
             starts a new page; otherwise a page holds --chars-per-page
             characters, paragraphs are rounded up to whole lines of
             --chars-per-line characters.
  auto       existing if anchors are found, otherwise synthetic (default).

--check reports how likely Kindle is to show page numbers for a book.
--compare runs the synthetic estimator on a book that has real page anchors
and reports how far the estimate drifts from the printed pages; --fit also
searches for the --chars-per-page value that matches the printed pages best.
"""
import argparse
import bisect
import math
import os
import posixpath
import re
import sys
import zipfile
from types import SimpleNamespace
from urllib.parse import quote, unquote
from xml.sax.saxutils import escape

TOKEN_RE = re.compile(
    r'<!--.*?-->|<!\[CDATA\[.*?\]\]>|<\?.*?\?>|<![^>]*>'
    r'|<(/?)([A-Za-z][\w:.-]*)((?:[^>"\']|"[^"]*"|\'[^\']*\')*?)(/?)>',
    re.S)
ATTR_RE = re.compile(r'([\w:.-]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\')')
TEXT_RE = re.compile(r'(\s+)|(&#?\w+;|[^\s&]+|&)')
ENTITY_RE = re.compile(r'&#?\w+;')

BLOCKS = {
    'p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'blockquote', 'pre',
    'dt', 'dd', 'figcaption', 'td', 'th', 'caption', 'section', 'article',
    'aside', 'header', 'footer', 'figure', 'table', 'tr', 'ul', 'ol', 'dl',
    'hr', 'address', 'nav', 'br', 'body',
}
# Extra lines a heading takes on top of its text (spacing above and below).
HEADING_EXTRA = {'h1': 3, 'h2': 2, 'h3': 1.5, 'h4': 1, 'h5': 1, 'h6': 1}
SKIP = {'head', 'script', 'style', 'title', 'svg', 'math'}
BREAK_VALUES = r'(?:always|page|left|right|recto|verso)'
PAGE_ID_RE = re.compile(
    r'^(?:page|pg|pagebreak|page_?n|lpn|pn|p)[-_]?(\d+)$|^(?:page|pg)[-_]?([ivxlcdm]+)$', re.I)
HREF_SAFE = "/-_.~!$&'()*+,;=:@"
BODY_START_RE = re.compile(
    r'\s*(?:(?:prologue|пролог)\b'
    r'|(?:chapter|part|book|глава|часть|книга)\s*(?:1|one|i|первая)\b)', re.I)
FONT_OBFUSCATION = ('http://www.idpf.org/2008/embedding', 'http://ns.adobe.com/pdf/enc#RC')

DEFAULTS = {
    'mode': 'auto',             # auto | existing | synthetic
    'chars_per_page': 2000,     # characters on a full page, spaces included
    'chars_per_line': 60,       # 0 = count plain characters, no paragraph rounding
    'image_lines': 0.5,         # share of a page an inline image takes
    'break_on': '',             # comma-separated tags that always start a new page
    'body_start': None,         # None | 'auto' | substring of the spine file of page 1
    'front': 'roman',           # numbering before body_start: roman | skip | number
    'first_page': 1,
    'recto': False,             # every spine document starts on an odd page
    'min_last_page': 0.0,       # merge a shorter last page of a section into the previous
    'chapter_sink': 0.0,        # share of a page taken by the heading that opens a page
    'include_nonlinear': False,
    'force': False,             # replace an existing page-list
}


def options(**kw):
    """Return processing options: DEFAULTS updated with kw."""
    unknown = set(kw) - set(DEFAULTS)
    if unknown:
        raise TypeError('unknown options: %s' % ', '.join(sorted(unknown)))
    return SimpleNamespace(**dict(DEFAULTS, **kw))


class PagesError(Exception):
    """A book cannot be processed; code is one of: invalid, drm, has_page_list,
    no_anchors, no_toc, body_start, no_pages."""

    def __init__(self, code, detail=''):
        super().__init__('%s: %s' % (code, detail) if detail else code)
        self.code = code
        self.detail = detail


# ---------------------------------------------------------------- utilities

def local(name):
    return name.split(':')[-1].lower()


def attrs_of(raw):
    return {m.group(1): m.group(2) if m.group(2) is not None else m.group(3)
            for m in ATTR_RE.finditer(raw)}


def events(text):
    """Yield ('start'|'end', name, attrs, selfclosing, start, end) and
    ('text', raw, start) events for an (X)HTML document."""
    pos = 0
    for m in TOKEN_RE.finditer(text):
        if m.start() > pos:
            yield ('text', text[pos:m.start()], pos)
        pos = m.end()
        if m.group(2) is None:
            continue  # comment, doctype, PI, CDATA
        name = local(m.group(2))
        if m.group(1):
            yield ('end', name, None, False, m.start(), m.end())
        else:
            yield ('start', name, attrs_of(m.group(3)), bool(m.group(4)),
                   m.start(), m.end())
    if pos < len(text):
        yield ('text', text[pos:], pos)


def decode(data):
    m = re.match(rb'(?:\xef\xbb\xbf)?<\?xml[^>]*encoding=["\']([\w.-]+)["\']', data)
    enc = m.group(1).decode() if m else 'utf-8'
    try:
        return data.decode(enc), enc
    except (UnicodeDecodeError, LookupError):
        return data.decode('utf-8', 'replace'), 'utf-8'


def to_roman(n):
    out = ''
    for v, s in ((1000, 'm'), (900, 'cm'), (500, 'd'), (400, 'cd'), (100, 'c'),
                 (90, 'xc'), (50, 'l'), (40, 'xl'), (10, 'x'), (9, 'ix'),
                 (5, 'v'), (4, 'iv'), (1, 'i')):
        while n >= v:
            out += s
            n -= v
    return out


def rel_href(target, from_dir, frag=None):
    href = quote(posixpath.relpath(target, from_dir or '.'), safe=HREF_SAFE)
    return href + ('#' + frag if frag else '')


# -------------------------------------------------------------------- book

class Book:
    """An EPUB opened for reading; spine documents are kept as text."""

    def __init__(self, path):
        self.path = path
        try:
            self.zip = zipfile.ZipFile(path)
        except (zipfile.BadZipFile, OSError) as e:
            raise PagesError('invalid', str(e))
        try:
            self._load()
        except PagesError:
            self.close()
            raise
        except (KeyError, AttributeError, zipfile.BadZipFile) as e:
            self.close()
            raise PagesError('invalid', str(e))

    def _load(self):
        names = set(self.zip.namelist())
        container = self.zip.read('META-INF/container.xml').decode('utf-8', 'replace')
        self.opf_path = re.search(r'full-path="([^"]+)"', container).group(1)
        self.opf_dir = posixpath.dirname(self.opf_path)
        self.opf, _ = decode(self.zip.read(self.opf_path))
        self.version = re.search(r'<(?:\w+:)?package\b[^>]*\bversion="([^"]+)"', self.opf).group(1)

        self.manifest = {}  # id -> (path, media-type, properties)
        for m in re.finditer(r'<(?:\w+:)?item\b[^>]*>', self.opf):
            a = attrs_of(m.group(0))
            if 'id' in a and 'href' in a:
                path = posixpath.normpath(posixpath.join(self.opf_dir, unquote(a['href'])))
                self.manifest[a['id']] = (path, a.get('media-type', ''), a.get('properties', ''))

        self.spine = []  # (path, linear)
        for m in re.finditer(r'<(?:\w+:)?itemref\b[^>]*>', self.opf):
            a = attrs_of(m.group(0))
            item = self.manifest.get(a.get('idref'))
            if item and item[0] in names:
                self.spine.append((item[0], a.get('linear', 'yes') != 'no'))
        if not self.spine:
            raise PagesError('invalid', 'empty spine')

        if 'META-INF/encryption.xml' in names:
            enc = self.zip.read('META-INF/encryption.xml').decode('utf-8', 'replace')
            algorithms = re.findall(r'Algorithm="([^"]+)"', enc)
            if any(a not in FONT_OBFUSCATION for a in algorithms):
                raise PagesError('drm')

        self.nav_path = next((p for p, _, props in self.manifest.values()
                              if 'nav' in props.split() and p in names), None)
        spine_tag = re.search(r'<(?:\w+:)?spine\b[^>]*>', self.opf)
        toc_id = attrs_of(spine_tag.group(0)).get('toc') if spine_tag else None
        ncx = self.manifest[toc_id][0] if toc_id in self.manifest else next(
            (p for p, mt, _ in self.manifest.values() if mt == 'application/x-dtbncx+xml'), None)
        self.ncx_path = ncx if ncx in names else None

        self.texts, self.encodings = {}, {}
        for path, _ in self.spine:
            self.texts[path], self.encodings[path] = decode(self.zip.read(path))
        self.modified = {}

        css = ''.join(decode(self.zip.read(p))[0] for p, mt, _ in self.manifest.values()
                      if mt == 'text/css' and p in names)
        self.break_before, self.break_after = parse_css_breaks(css)

    def close(self):
        self.zip.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def has_page_list(self):
        return page_list_entries(self) > 0

    def spine_index(self, href, base_dir):
        path = posixpath.normpath(posixpath.join(base_dir, unquote(href.partition('#')[0])))
        for i, (p, _) in enumerate(self.spine):
            if p == path:
                return i
        return None


def page_list_entries(book):
    entries = 0
    for p in (book.nav_path, book.ncx_path):
        if p:
            text = decode(book.zip.read(p))[0]
            nav = re.search(r'<nav\b[^>]*page-list.*?</nav>', text, re.S)
            n = len(re.findall(r'<a\b', nav.group(0))) if nav else 0
            n = max(n, len(re.findall(r'<(?:\w+:)?pageTarget\b', text)))
            entries = max(entries, n)
    return entries


def detect_body_start(book):
    """Spine index where the main text starts according to the EPUB 3 landmarks
    or the EPUB 2 guide, or None."""
    if book.nav_path:
        text = decode(book.zip.read(book.nav_path))[0]
        nav = re.search(r'<nav\b[^>]*landmarks.*?</nav>', text, re.S)
        if nav:
            for m in re.finditer(r'<a\b[^>]*>', nav.group(0)):
                a = attrs_of(m.group(0))
                if 'bodymatter' in a.get('epub:type', '').split() and a.get('href'):
                    idx = book.spine_index(a['href'], posixpath.dirname(book.nav_path))
                    if idx is not None:
                        return idx
    for m in re.finditer(r'<(?:\w+:)?reference\b[^>]*>', book.opf):
        a = attrs_of(m.group(0))
        if a.get('type', '').lower() in ('text', 'start', 'bodymatter') and a.get('href'):
            idx = book.spine_index(a['href'], book.opf_dir)
            if idx is not None:
                return idx
    # No semantics: the first early document whose text starts like a chapter.
    for idx, (path, linear) in enumerate(book.spine[:max(3, len(book.spine) // 3)]):
        text = book.texts[path]
        body = text[text.find('<body'):] if '<body' in text else text
        visible = re.sub(r'\s+', ' ', ENTITY_RE.sub(' ', re.sub(r'<[^>]+>', ' ', body[:4000])))
        if linear and BODY_START_RE.match(visible):
            return idx
    return None


# ---------------------------------------------------------------------- css

def parse_selector(sel):
    sel = re.split(r'[\s>+~]+', sel.strip())[-1]
    sel = re.sub(r'::?[\w-]+(\([^)]*\))?', '', sel)
    m = re.match(r'^([\w-]+|\*)?((?:[.#][\w-]+)*)$', sel)
    if not m or not sel:
        return None
    tag = m.group(1).lower() if m.group(1) and m.group(1) != '*' else None
    classes = frozenset(re.findall(r'\.([\w-]+)', m.group(2)))
    ids = re.findall(r'#([\w-]+)', m.group(2))
    return tag, classes, ids[0] if ids else None


def parse_css_breaks(css):
    css = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
    before, after = [], []
    for sel, body in re.findall(r'([^{}@;]+)\{([^{}]*)\}', css):
        b = re.search(r'(?:page-break-before|break-before)\s*:\s*' + BREAK_VALUES, body, re.I)
        a = re.search(r'(?:page-break-after|break-after)\s*:\s*' + BREAK_VALUES, body, re.I)
        for s in sel.split(','):
            parsed = parse_selector(s)
            if parsed:
                if b:
                    before.append(parsed)
                if a:
                    after.append(parsed)
    return before, after


def css_match(rules, name, attrs):
    classes = set(attrs.get('class', '').split())
    for tag, cls, id_ in rules:
        if (tag is None or tag == name) and cls <= classes and (id_ is None or id_ == attrs.get('id')):
            return True
    return False


def style_break(attrs, side):
    return bool(re.search(r'(?:page-break-|break-)%s\s*:\s*%s' % (side, BREAK_VALUES),
                          attrs.get('style', ''), re.I))


# ------------------------------------------------------- existing anchors

def find_existing(book):
    """Return [(spine_idx, offset, id, label, insert_id_at)] for print-page
    anchors already in the book; insert_id_at is set when the element has no id."""
    semantic, by_id = [], []
    for idx, (path, linear) in enumerate(book.spine):
        text = book.texts[path]
        collecting = None  # [entry, text parts]
        depth = 0
        for ev in events(text):
            kind = ev[0]
            if kind == 'start':
                _, name, attrs, selfclose, start, _ = ev
                if collecting and not selfclose:
                    depth += 1
                etype = attrs.get('epub:type', '').split() + attrs.get('role', '').split()
                if 'pagebreak' in etype or 'doc-pagebreak' in etype:
                    label = attrs.get('title') or attrs.get('aria-label')
                    entry = [idx, start, attrs.get('id'), label,
                             None if attrs.get('id') else start + 1 + len(ev[1])]
                    semantic.append(entry)
                    if not label and not selfclose:
                        collecting, depth = [entry, []], 0
                    continue
                m = PAGE_ID_RE.match(attrs.get('id', ''))
                if m:
                    by_id.append([idx, start, attrs['id'], m.group(1) or m.group(2).lower(), None])
            elif kind == 'end' and collecting:
                if depth == 0:
                    collecting[0][3] = ENTITY_RE.sub('', ''.join(collecting[1])).strip()
                    collecting = None
                else:
                    depth -= 1
            elif kind == 'text' and collecting:
                collecting[1].append(ev[1])
    if semantic:
        for e in semantic:
            if not e[3]:
                m = re.search(r'(\d+|[ivxlcdm]+)$', e[2] or '', re.I)
                e[3] = m.group(1) if m else ''
        return [e for e in semantic if e[3]]
    if len(by_id) >= 10:
        nums = [int(e[3]) for e in by_id if e[3].isdigit()]
        increasing = sum(b > a for a, b in zip(nums, nums[1:]))
        if nums and increasing >= 0.8 * (len(nums) - 1):
            return by_id
    return []


# ---------------------------------------------------- synthetic pagination

class Paginator:
    """Fills pages with lines; a paragraph occupies whole lines."""

    def __init__(self, chars_per_page, chars_per_line, image_lines):
        self.cpl = chars_per_line if chars_per_line > 0 else 1
        self.lpp = max(1.0, chars_per_page / self.cpl)
        self.image_lines = self.lpp * image_lines
        self.used = 0.0       # lines used on the current page
        self.chars = 0        # chars of the current block on the current page
        self.space = False
        self.pending = True   # the next content starts a new page
        self.pages = []       # (spine_idx, offset)
        self.blank = False
        self.first_number = 1  # number of the first page, for recto parity
        self.min_last = 0.0    # shorter last page of a section joins the previous one
        self.section_first = 0  # index of the first page of the current section

    def lines(self, chars):
        return math.ceil(chars / self.cpl) if chars else 0

    def new_page(self, idx, offset):
        if self.blank:
            self.pages.append((idx, offset))  # blank page, shares the position
            self.blank = False
        self.pages.append((idx, offset))
        if self.pending:
            self.section_first = len(self.pages) - 1
        self.used, self.chars, self.space, self.pending = 0.0, 0, False, False

    def word(self, idx, offset, n):
        n += 1 if self.space and self.chars else 0
        self.space = False
        if self.pending:
            self.new_page(idx, offset)
        elif self.used + self.lines(self.chars + n) > self.lpp and (self.used or self.chars):
            self.new_page(idx, offset)
            n = n - 1 if n > 1 else n
        self.chars += n

    def end_block(self, extra=0.0):
        if self.chars or (extra and not self.pending and self.pages):
            self.used += self.lines(self.chars) + extra
        self.chars, self.space = 0, False

    def image(self, idx, offset):
        self.end_block()
        if self.pending or (self.used and self.used + self.image_lines > self.lpp):
            self.new_page(idx, offset)
        self.used += self.image_lines

    def sink(self, lines):
        """The heading area at the top of a page occupies at least this many lines."""
        if not self.pending:
            self.used = max(self.used, lines)

    def force_break(self, recto=False):
        self.end_block()
        if (not self.pending and len(self.pages) - 1 > self.section_first
                and self.used < self.min_last * self.lpp):
            self.pages.pop()  # a short tail is squeezed onto the previous page
        self.pending = True
        if recto and (self.first_number + len(self.pages)) % 2 == 0:
            self.blank = True  # next page must be odd: add a blank page first


def paginate(book, opts, docs=None, first_number=None):
    """Return [(spine_idx, offset)] of synthetic page starts."""
    pg = Paginator(opts.chars_per_page, opts.chars_per_line, opts.image_lines)
    pg.first_number = opts.first_page if first_number is None else first_number
    pg.min_last = opts.min_last_page
    break_on = {t.strip().lower() for t in opts.break_on.split(',') if t.strip()}
    sink_lines = opts.chapter_sink * pg.lpp
    opening = False     # the headings that open the current page are being read
    headings = 0        # depth of open heading elements
    for idx, (path, linear) in enumerate(book.spine):
        if docs is not None and idx not in docs:
            continue
        if not linear and not opts.include_nonlinear:
            continue
        pg.force_break(opts.recto and pg.pages)
        opening, headings = False, 0
        stack = []      # names of open elements that force a break when closed
        skip = None     # name of the skipped element we are inside
        skip_depth = 0
        in_body = False
        for ev in events(book.texts[path]):
            kind = ev[0]
            if kind == 'start':
                _, name, attrs, selfclose, start, _ = ev
                if skip:
                    if name == skip and not selfclose:
                        skip_depth += 1
                    continue
                if name == 'body':
                    in_body = True
                    continue
                if not in_body:
                    continue
                if name in SKIP:
                    if name == 'svg':
                        pg.image(idx, start)
                    if not selfclose:
                        skip, skip_depth = name, 0
                    continue
                if (name in break_on or css_match(book.break_before, name, attrs)
                        or style_break(attrs, 'before')):
                    pg.force_break()
                elif name in BLOCKS:
                    pg.end_block()
                if name in HEADING_EXTRA and not selfclose:
                    if pg.pending and sink_lines:
                        opening = True
                    headings += 1
                if name in ('img', 'image'):
                    pg.image(idx, start)
                after = css_match(book.break_after, name, attrs) or style_break(attrs, 'after')
                if selfclose:
                    if after:
                        pg.force_break()
                else:
                    stack.append((name, after))
            elif kind == 'end':
                _, name, _, _, start, _ = ev
                if skip:
                    if name == skip:
                        if skip_depth == 0:
                            skip = None
                        else:
                            skip_depth -= 1
                    continue
                if not in_body:
                    continue
                if name == 'body':
                    break
                after = False
                while stack:
                    open_name, open_after = stack.pop()
                    if open_name == name:
                        after = open_after
                        break
                if name in BLOCKS:
                    pg.end_block(HEADING_EXTRA.get(name, 0))
                if name in HEADING_EXTRA and headings:
                    headings -= 1
                    if opening:
                        pg.sink(sink_lines)
                if after:
                    pg.force_break()
            elif kind == 'text' and in_body and not skip:
                raw, start = ev[1], ev[2]
                for m in TEXT_RE.finditer(raw):
                    if m.group(1):
                        pg.space = True
                    else:
                        tok = m.group(2)
                        n = len(ENTITY_RE.sub('x', tok))
                        if tok in ('&#160;', '&nbsp;') and not pg.chars:
                            continue  # empty-paragraph spacer
                        if opening and not headings:
                            opening = False  # body text: the page opening is over
                        pg.word(idx, start + m.start(2), n)
    pg.force_break()
    return pg.pages


# ------------------------------------------------------------ output

def resolve_body_start(book, body_start):
    """Spine index of page 1: body_start is None, 'auto' or a file name substring."""
    if not body_start:
        return 0
    if body_start == 'auto':
        idx = detect_body_start(book)
        return idx or 0
    for i, (path, _) in enumerate(book.spine):
        if body_start in path:
            return i
    raise PagesError('body_start', body_start)


def page_labels(book, pages, opts):
    """pages: [(spine_idx, offset)] -> [(spine_idx, offset, label, is_front)]."""
    body_idx = resolve_body_start(book, opts.body_start)
    out, front_n, body_n = [], 0, opts.first_page - 1
    for idx, off in pages:
        if idx < body_idx:
            if opts.front == 'skip':
                continue
            if opts.front == 'roman':
                front_n += 1
                out.append((idx, off, to_roman(front_n), True))
                continue
        body_n += 1
        out.append((idx, off, str(body_n), False))
    return out


def insert_markers(book, labelled):
    """Insert empty <span> page markers; return [(path, id, label, is_front)]."""
    epub3 = book.version.startswith('3')
    per_doc = {}
    for idx, off, label, front in labelled:
        per_doc.setdefault(idx, []).append((off, label, front))
    targets = []
    for idx, items in sorted(per_doc.items()):
        path = book.spine[idx][0]
        text = book.texts[path]
        prefix = 'pg'
        while re.search(r'\bid=["\']%s-' % prefix, text):
            prefix += 'x'
        ns_epub = epub3 and 'xmlns:epub=' in text
        inserts = []
        for off, label, front in items:
            id_ = '%s-%s' % (prefix, label)
            attrs = 'id="%s"' % id_
            if ns_epub:
                attrs += ' epub:type="pagebreak" role="doc-pagebreak"'
            attrs += ' title="%s"' % escape(label)
            inserts.append((off, '<span %s></span>' % attrs))
            targets.append((path, id_, label, front))
        for off, html in sorted(inserts, reverse=True):
            text = text[:off] + html + text[off:]
        book.texts[path] = text
        book.modified[path] = True
    return targets


def add_missing_ids(book, anchors):
    """Existing anchors without id get one; return [(path, id, label, is_front)]."""
    per_doc = {}
    for idx, off, id_, label, insert_at in anchors:
        per_doc.setdefault(idx, []).append([off, id_, label, insert_at])
    targets = []
    for idx, items in sorted(per_doc.items()):
        path = book.spine[idx][0]
        text = book.texts[path]
        inserts = []
        for n, item in enumerate(items):
            if not item[1]:
                item[1] = 'pgx-%d-%d' % (idx, n)
                inserts.append((item[3], ' id="%s"' % item[1]))
            targets.append((path, item[1], item[2], not item[2].isdigit()))
        for off, s in sorted(inserts, reverse=True):
            text = text[:off] + s + text[off:]
        if inserts:
            book.texts[path] = text
            book.modified[path] = True
    return targets


def target_key(book, path, frag):
    order = {p: i for i, (p, _) in enumerate(book.spine)}
    idx = order.get(path, len(order))
    if not frag or path not in book.texts:
        return idx, 0
    m = re.search(r'\bid=["\']%s["\']' % re.escape(frag), book.texts[path])
    return idx, m.start() if m else 0


def build_nav(book, targets, other):
    text = other[book.nav_path]
    text = re.sub(r'<nav\b[^>]*page-list[^>]*>.*?</nav>\s*', '', text, flags=re.S)
    nav_dir = posixpath.dirname(book.nav_path)
    items = '\n'.join('      <li><a href="%s">%s</a></li>'
                      % (escape(rel_href(p, nav_dir, i)), escape(l)) for p, i, l, _ in targets)
    nav = ('<nav epub:type="page-list" hidden="hidden">\n    <h2>Pages</h2>\n'
           '    <ol>\n%s\n    </ol>\n  </nav>\n' % items)
    pos = text.rfind('</body>')
    other[book.nav_path] = text[:pos] + nav + text[pos:]


def build_ncx(book, targets, other):
    text = other[book.ncx_path]
    text = re.sub(r'\s*<(?:\w+:)?pageList\b.*?</(?:\w+:)?pageList>', '', text, flags=re.S)
    ncx_dir = posixpath.dirname(book.ncx_path)

    # Renumber playOrder over navMap and pageList together, in reading order.
    content_re = re.compile(r'<content\b[^>]*\bsrc=["\']([^"\']+)["\']')
    nav_points = []
    for m in re.finditer(r'<navPoint\b[^>]*>', text):
        c = content_re.search(text, m.end())
        src = unquote(c.group(1)) if c else ''
        path, _, frag = src.partition('#')
        path = posixpath.normpath(posixpath.join(ncx_dir, path))
        nav_points.append((m, target_key(book, path, frag)))
    page_keys = [target_key(book, p, i) for p, i, _, _ in targets]
    order = {k: n for n, k in enumerate(sorted(set([k for _, k in nav_points] + page_keys)), 1)}

    out, pos = [], 0
    for m, key in nav_points:
        tag = m.group(0)
        if 'playOrder=' in tag:
            tag = re.sub(r'playOrder=["\']\d*["\']', 'playOrder="%d"' % order[key], tag)
        else:
            tag = tag[:-1] + ' playOrder="%d">' % order[key]
        out += [text[pos:m.start()], tag]
        pos = m.end()
    out.append(text[pos:])
    text = ''.join(out)

    rows = []
    for (path, id_, label, front), key in zip(targets, page_keys):
        value = '' if front or not label.isdigit() else ' value="%s"' % label
        rows.append('    <pageTarget id="pt-%s" type="%s"%s playOrder="%d">\n'
                    '      <navLabel><text>%s</text></navLabel>\n'
                    '      <content src="%s"/>\n    </pageTarget>'
                    % (escape(id_), 'front' if front else 'normal', value, order[key],
                       escape(label), escape(rel_href(path, ncx_dir, id_))))
    page_list = ('\n  <pageList>\n    <navLabel><text>Pages</text></navLabel>\n%s\n'
                 '  </pageList>\n' % '\n'.join(rows))
    pos = text.rfind('</ncx>')
    other[book.ncx_path] = text[:pos].rstrip() + page_list + text[pos:]


def write_book(book, targets, output):
    other = {}
    for p in (book.nav_path, book.ncx_path):
        if p:
            other[p] = decode(book.zip.read(p))[0]
    if book.nav_path:
        if book.nav_path in book.texts:  # nav doc is also in the spine
            other[book.nav_path] = book.texts[book.nav_path]
        build_nav(book, targets, other)
    if book.ncx_path:
        build_ncx(book, targets, other)

    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as out:
        out.writestr(zipfile.ZipInfo('mimetype'), book.zip.read('mimetype'), zipfile.ZIP_STORED)
        for info in book.zip.infolist():
            name = info.filename
            if name == 'mimetype':
                continue
            if name in other:
                data = other[name].encode('utf-8')
            elif name in book.modified:
                data = book.texts[name].encode(book.encodings[name])
            else:
                data = book.zip.read(name)
            out.writestr(name, data, zipfile.ZIP_DEFLATED)


def process(book, opts, output, anchors=None):
    """Add a page-list to book and write it to output.

    Returns dict(mode='existing'|'synthetic', pages=count, first=label, last=label).
    Raises PagesError."""
    if not book.nav_path and not book.ncx_path:
        raise PagesError('no_toc')
    if book.has_page_list() and not opts.force:
        raise PagesError('has_page_list')
    if anchors is None:
        anchors = find_existing(book)

    mode = opts.mode
    if mode == 'auto':
        mode = 'existing' if anchors else 'synthetic'
    if mode == 'existing':
        if not anchors:
            raise PagesError('no_anchors')
        targets = add_missing_ids(book, anchors)
    else:
        targets = insert_markers(book, page_labels(book, paginate(book, opts), opts))
    if not targets:
        raise PagesError('no_pages')

    write_book(book, targets, output)
    return {'mode': mode, 'pages': len(targets), 'first': targets[0][2], 'last': targets[-1][2]}


# ------------------------------------------------------------ compare

def compare(book, anchors, opts, verbose=True):
    """Compare synthetic pagination with the book's real page anchors."""
    real = [(a[0], a[1], int(a[3])) for a in anchors if a[3].isdigit()]
    docs = {a[0] for a in real}  # only documents that are paginated in print
    synth = paginate(book, opts, docs, real[0][2] if real else None)
    if not real or not synth:
        return None
    doc_first = {}
    for idx, off in synth:
        doc_first.setdefault(idx, off)

    def page_index(idx, off):
        # an anchor before the first word of a document belongs to its first page
        if idx in doc_first and off < doc_first[idx]:
            off = doc_first[idx]
        return bisect.bisect_right(synth, (idx, off)) - 1

    first = page_index(*real[0][:2])
    shift = real[0][2] - first  # align synthetic numbering with the first real page
    diffs, starts, seen = [], [], set()
    for idx, off, num in real:
        est = page_index(idx, off) + shift
        diffs.append(est - num)
        if idx not in seen:
            seen.add(idx)
            starts.append((book.spine[idx][0], num, est))
    real_span = real[-1][2] - real[0][2] + 1
    synth_span = len(synth) - max(first, 0)
    drift = sorted(map(abs, diffs))
    if verbose:
        print('chapter starts (real -> estimated):')
        for path, num, est in starts:
            print('  %-50s %5d -> %5d  (%+d)' % (posixpath.basename(path), num, est, est - num))
        print('pages: real %d, estimated %d' % (real_span, synth_span))
        print('drift: median %d, 90%% within %d, max %d pages'
              % (drift[len(drift) // 2], drift[len(drift) * 9 // 10], drift[-1]))
    return synth_span - real_span, sum(drift[:len(drift) * 9 // 10]) / len(drift)


def fit(book, anchors, opts):
    """Scan chars_per_page for the smallest page drift (ignoring the worst 10%)."""
    best = None
    for cpp in range(800, 4001, 20):
        opts.chars_per_page = cpp
        r = compare(book, anchors, opts, verbose=False)
        if r is not None and (best is None or r[1] < best[1]):
            best = (cpp, r[1])
    if best:
        opts.chars_per_page = best[0]
    return opts.chars_per_page


# ------------------------------------------------------------------- check

def check(book, anchors=None):
    """Estimate whether Send to Kindle will show page numbers for the book.

    Based on observed books: a page-list always gave pages; ids like
    "page12" without a page-list gave pages (mechanism unconfirmed: maybe the
    converter picks them up, maybe Amazon matched the ISBN); ids like
    "lpn_12" without a page-list gave no pages.

    Returns dict(verdict='high'|'likely'|'low', advice='none'|'existing'|'synthetic',
    page_list=entries, pagebreaks=count, anchors=count, prefixes={prefix: count},
    first=label, last=label, isbn=str|None)."""
    if anchors is None:
        anchors = find_existing(book)
    entries = page_list_entries(book)
    pagebreaks = sum(len(re.findall(r'epub:type="[^"]*\bpagebreak\b|role="doc-pagebreak"', t))
                     for t in book.texts.values())
    prefixes = {}
    if not pagebreaks:
        for a in anchors:
            if a[2]:
                prefix = re.match(r'(.*?)[-_]?(?:\d+|[ivxlcdm]+)$', a[2], re.I).group(1)
                prefixes[prefix] = prefixes.get(prefix, 0) + 1
    isbn = re.search(r'(97[89]\d{10})', book.opf)

    if entries:
        verdict, advice = 'high', 'none'
    elif any(p.lower() == 'page' for p in prefixes):
        verdict, advice = 'likely', 'existing'
    elif anchors:
        verdict, advice = 'low', 'existing'
    else:
        verdict, advice = 'low', 'synthetic'
    return {
        'verdict': verdict, 'advice': advice, 'page_list': entries,
        'pagebreaks': pagebreaks, 'anchors': len(anchors), 'prefixes': prefixes,
        'first': anchors[0][3] if anchors else None,
        'last': anchors[-1][3] if anchors else None,
        'isbn': isbn.group(1) if isbn else None,
    }


# ------------------------------------------------------------------- main

ERRORS = {
    'invalid': 'not a valid EPUB',
    'drm': 'the book is DRM-protected',
    'has_page_list': 'the book already has a page-list; use --force to replace it',
    'no_anchors': 'no page anchors found; use --mode synthetic',
    'no_toc': 'the book has neither an NCX nor an EPUB 3 nav document',
    'body_start': 'no spine document matches --body-start',
    'no_pages': 'no text found to paginate',
}

VERDICTS = {
    'high': 'HIGH - the book has a page-list; Kindle shows these pages',
    'likely': 'LIKELY - no page-list, but anchors like "page12" (seen to work, not '
              'guaranteed); run without --check to add a page-list from them',
    'low-existing': 'LOW - no page-list; real print-page anchors exist, run without '
                    '--check to build a page-list from them (real page numbers)',
    'low-synthetic': 'LOW - no page-list and no page anchors; run without --check to '
                     'add estimated pages',
}


def print_check(info):
    print('  page-list entries:      %d' % info['page_list'])
    print('  pagebreak markers:      %d' % info['pagebreaks'])
    print('  page anchors by id:     %s' % (', '.join('%s* x%d' % kv for kv in info['prefixes'].items())
                                         or 'none'))
    print('  ISBN:                   %s' % (info['isbn'] or 'none'))
    key = info['verdict'] if info['verdict'] != 'low' else 'low-' + info['advice']
    print('  Kindle pages:           %s' % VERDICTS[key])


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__.split('\n\n', 1)[1])
    ap.add_argument('input', nargs='+', help='EPUB file(s); several only with --check')
    ap.add_argument('-o', '--output', help='output file (default: <input> (pages).epub)')
    ap.add_argument('--mode', choices=('auto', 'existing', 'synthetic'), default='auto')
    ap.add_argument('--chars-per-page', type=float, default=DEFAULTS['chars_per_page'],
                    help='characters on a full page incl. spaces (default 2000)')
    ap.add_argument('--chars-per-line', type=int, default=DEFAULTS['chars_per_line'],
                    help='characters per printed line; paragraph ends are rounded up '
                         'to whole lines; 0 = count plain characters (default 60)')
    ap.add_argument('--image-lines', type=float, default=DEFAULTS['image_lines'],
                    help='share of a page an inline image takes (default 0.5)')
    ap.add_argument('--break-on', default='',
                    help='comma-separated tags that always start a new page, e.g. h1,h2')
    ap.add_argument('--body-start',
                    help='spine file (substring of its path) where page 1 begins, or "auto" '
                         'to use the book\'s landmarks/guide; earlier documents are front matter')
    ap.add_argument('--front', choices=('roman', 'skip', 'number'), default='roman',
                    help='front matter numbering when --body-start is given (default roman)')
    ap.add_argument('--first-page', type=int, default=1, help='number of the first body page')
    ap.add_argument('--recto', action='store_true',
                    help='every spine document starts on an odd page (blank page before)')
    ap.add_argument('--min-last-page', type=float, default=0,
                    help='a section\'s last page filled less than this share (e.g. 0.25) '
                         'is merged into the previous page, like a typesetter avoids '
                         'stray lines (default 0 = off)')
    ap.add_argument('--chapter-sink', type=float, default=0,
                    help='share of a page (e.g. 0.3) taken by a heading that opens a page, '
                         'with its spacing: the first page of a chapter holds less text '
                         '(default 0 = only the heading text and a few lines)')
    ap.add_argument('--include-nonlinear', action='store_true',
                    help='also paginate spine items marked linear="no"')
    ap.add_argument('--force', action='store_true', help='replace an existing page-list')
    ap.add_argument('--check', action='store_true',
                    help='only report how likely Kindle is to show page numbers')
    ap.add_argument('--compare', action='store_true',
                    help='compare synthetic pages with the real page anchors, write nothing')
    ap.add_argument('--fit', action='store_true',
                    help='with --compare: find --chars-per-page matching the real pages best')
    args = ap.parse_args()
    if len(args.input) > 1 and not args.check:
        ap.error('several input files are only allowed with --check')
    opts = options(**{k: getattr(args, k) for k in DEFAULTS})

    status = 0
    for path in args.input:
        try:
            with Book(path) as book:
                anchors = find_existing(book)
                print('%s: EPUB %s, %d spine documents, existing page anchors: %d'
                      % (os.path.basename(path), book.version, len(book.spine), len(anchors)))
                if args.check:
                    print_check(check(book, anchors))
                    print()
                elif args.compare:
                    if not anchors:
                        raise PagesError('no_anchors')
                    if args.fit:
                        print('best --chars-per-page: %d (with --chars-per-line %d)\n'
                              % (fit(book, anchors, opts), opts.chars_per_line))
                    compare(book, anchors, opts)
                else:
                    output = args.output or os.path.splitext(path)[0] + ' (pages).epub'
                    r = process(book, opts, output, anchors)
                    where = ', '.join(n for n, p in (('nav page-list', book.nav_path),
                                                     ('NCX pageList', book.ncx_path)) if p)
                    print('mode %s: %d pages (%s ... %s) -> %s [%s]'
                          % (r['mode'], r['pages'], r['first'], r['last'], output, where))
        except PagesError as e:
            print('%s: %s%s' % (os.path.basename(path), ERRORS.get(e.code, e.code),
                                ' (%s)' % e.detail if e.detail else ''), file=sys.stderr)
            status = 1
    return status


if __name__ == '__main__':
    sys.exit(main())

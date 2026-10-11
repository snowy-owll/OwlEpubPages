"""Unit tests for plugin/core.py on small generated EPUBs (standard library only).

    python -m unittest discover -s tests
"""
import os
import re
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'plugin'))
import core  # noqa: E402

WORDS = ('lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod tempor '
         'incididunt ut labore et dolore magna aliqua ').split()


def paragraphs(n, words=60, seed=0):
    out = []
    for i in range(n):
        text = ' '.join(WORDS[(seed + i + j) % len(WORDS)] for j in range(words))
        out.append('<p>%s.</p>' % text)
    return '\n'.join(out)


def xhtml(body, epub3=False):
    ns = ' xmlns:epub="http://www.idpf.org/2007/ops"' if epub3 else ''
    return ('<?xml version="1.0" encoding="utf-8"?>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml"%s><head><title>t</title></head>'
            '<body>\n%s\n</body></html>' % (ns, body))


def make_epub(path, chapters, epub3=False, page_list=False, encryption=None, guide_text=None,
              css=''):
    """chapters: [(file name, body html)]."""
    items, spine, nav_points = [], [], []
    for n, (name, _) in enumerate(chapters, 1):
        items.append('<item id="c%d" href="%s" media-type="application/xhtml+xml"/>' % (n, name))
        spine.append('<itemref idref="c%d"/>' % n)
        nav_points.append('<navPoint id="n%d" playOrder="%d"><navLabel><text>%s</text></navLabel>'
                          '<content src="%s"/></navPoint>' % (n, n, name, name))
    items.append('<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>')
    items.append('<item id="css" href="style.css" media-type="text/css"/>')
    if epub3:
        items.append('<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" '
                     'properties="nav"/>')
    guide = ('<guide><reference type="text" href="%s" title="Start"/></guide>' % guide_text
             if guide_text else '')
    opf = ('<?xml version="1.0" encoding="utf-8"?>\n'
           '<package xmlns="http://www.idpf.org/2007/opf" version="%s" unique-identifier="id">'
           '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Test</dc:title>'
           '<dc:identifier id="id">urn:isbn:9780000000002</dc:identifier></metadata>'
           '<manifest>%s</manifest><spine toc="ncx">%s</spine>%s</package>'
           % ('3.0' if epub3 else '2.0', ''.join(items), ''.join(spine), guide))
    ncx_pages = ('<pageList><pageTarget id="p1" type="normal" value="1" playOrder="1">'
                 '<navLabel><text>1</text></navLabel><content src="%s"/></pageTarget></pageList>'
                 % chapters[0][0] if page_list else '')
    ncx = ('<?xml version="1.0" encoding="utf-8"?>\n'
           '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><head/>'
           '<docTitle><text>Test</text></docTitle><navMap>%s</navMap>%s</ncx>'
           % (''.join(nav_points), ncx_pages))
    nav = xhtml('<nav epub:type="toc"><ol>%s</ol></nav>'
                % ''.join('<li><a href="%s">%s</a></li>' % (c, c) for c, _ in chapters), True)
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr(zipfile.ZipInfo('mimetype'), 'application/epub+zip', zipfile.ZIP_STORED)
        z.writestr('META-INF/container.xml',
                   '<?xml version="1.0"?><container version="1.0" '
                   'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                   '<rootfile full-path="OEBPS/content.opf" '
                   'media-type="application/oebps-package+xml"/></rootfiles></container>')
        if encryption:
            z.writestr('META-INF/encryption.xml',
                       '<encryption><EncryptedData><EncryptionMethod Algorithm="%s"/>'
                       '</EncryptedData></encryption>' % encryption)
        z.writestr('OEBPS/content.opf', opf)
        z.writestr('OEBPS/toc.ncx', ncx)
        z.writestr('OEBPS/style.css', css)
        if epub3:
            z.writestr('OEBPS/nav.xhtml', nav)
        for name, body in chapters:
            z.writestr('OEBPS/' + name, xhtml(body, epub3))


class CoreTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def path(self, name):
        return os.path.join(self.tmp.name, name)

    def build(self, chapters, **kw):
        src = self.path('in.epub')
        make_epub(src, chapters, **kw)
        return src

    def run_process(self, src, **opts):
        out = self.path('out.epub')
        with core.Book(src) as book:
            result = core.process(book, core.options(**opts), out)
        return result, zipfile.ZipFile(out)

    def labels(self, z, ncx=True):
        if ncx:
            return re.findall(r'<pageTarget\b.*?<text>([^<]+)</text>', z.read('OEBPS/toc.ncx').decode(), re.S)
        nav = z.read('OEBPS/nav.xhtml').decode()
        page_list = re.search(r'<nav epub:type="page-list".*?</nav>', nav, re.S).group(0)
        return re.findall(r'<a [^>]*>([^<]+)</a>', page_list)

    # ----------------------------------------------------------- existing

    def test_existing_anchors_build_page_list(self):
        body = ''.join('<p><a id="page%d"/>%s</p>' % (n, ' '.join(WORDS)) for n in range(1, 13))
        result, z = self.run_process(self.build([('c1.xhtml', body)]))
        self.assertEqual(result['mode'], 'existing')
        self.assertEqual(self.labels(z), [str(n) for n in range(1, 13)])
        self.assertIn('<content src="c1.xhtml#page12"/>', z.read('OEBPS/toc.ncx').decode())

    def test_pagebreak_elements_with_text_labels(self):
        body = ''.join('<span epub:type="pagebreak" id="pb%d">%d</span>%s' % (n, n, paragraphs(1))
                       for n in range(1, 4))
        result, z = self.run_process(self.build([('c1.xhtml', body)], epub3=True))
        self.assertEqual(result['mode'], 'existing')
        self.assertEqual(self.labels(z, ncx=False), ['1', '2', '3'])

    def test_few_page_like_ids_are_ignored(self):
        body = '<p id="p1">%s</p>' % ' '.join(WORDS)
        with core.Book(self.build([('c1.xhtml', body)])) as book:
            self.assertEqual(core.find_existing(book), [])

    # ---------------------------------------------------------- synthetic

    def test_every_chapter_starts_a_new_page(self):
        chapters = [('c%d.xhtml' % n, '<h1>Chapter %d</h1>' % n + paragraphs(5, seed=n))
                    for n in range(1, 4)]
        result, z = self.run_process(self.build(chapters, epub3=True), chars_per_page=1000)
        self.assertEqual(result['mode'], 'synthetic')
        for n in range(1, 4):
            text = z.read('OEBPS/c%d.xhtml' % n).decode()
            first = re.search(r'<span id="pg-[^"]+"[^>]*>', text)
            self.assertIsNotNone(first)
            self.assertIn('epub:type="pagebreak"', first.group(0))
            self.assertLess(first.start(), text.index('Chapter'))  # page starts at the heading
        labels = self.labels(z, ncx=False)
        self.assertEqual(labels, [str(n) for n in range(1, len(labels) + 1)])
        self.assertEqual(self.labels(z), labels)

    def test_page_size_controls_page_count(self):
        src = self.build([('c1.xhtml', paragraphs(40))])
        small, _ = self.run_process(src, chars_per_page=1000)
        large, _ = self.run_process(src, chars_per_page=3000)
        self.assertGreater(small['pages'], 2.5 * large['pages'])

    def test_css_page_break_starts_a_new_page(self):
        body = paragraphs(1) + '<div class="brk">' + paragraphs(1) + '</div>'
        src = self.build([('c1.xhtml', body)], css='.brk { page-break-before: always }')
        result, _ = self.run_process(src, chars_per_page=5000)
        self.assertEqual(result['pages'], 2)

    def test_min_last_page_merges_short_tail(self):
        body = paragraphs(5) + '<p>tail</p>'
        src = self.build([('c1.xhtml', body), ('c2.xhtml', paragraphs(2))])
        cpp = 5 * 61 * 6  # five paragraphs fill about one page
        plain, _ = self.run_process(src, chars_per_page=cpp)
        merged, _ = self.run_process(src, chars_per_page=cpp, min_last_page=0.25)
        self.assertEqual(merged['pages'], plain['pages'] - 1)

    def test_chapter_sink_shortens_only_the_first_page(self):
        src = self.build([('c1.xhtml', '<h1>Chapter 1</h1>' + paragraphs(40))])

        def page_sizes(**opts):
            with core.Book(src) as book:
                text = book.texts[book.spine[0][0]]
                offs = [off for _, off in core.paginate(book, core.options(
                    chars_per_page=1800, chars_per_line=0, **opts))]
            return [len(re.sub(r'<[^>]+>', '', text[a:b])) for a, b in zip(offs, offs[1:])]

        plain, sunk = page_sizes(), page_sizes(chapter_sink=0.3)
        self.assertLess(sunk[0], 0.75 * plain[0])
        self.assertGreater(sunk[0], 0.65 * plain[0])
        self.assertAlmostEqual(sunk[1], plain[1], delta=40)

    def test_chapter_sink_ignores_headings_inside_a_page(self):
        # 7 + 35 lines of text fit one 50-line page; a sink of 25 lines at the heading would not
        body = paragraphs(1) + '<h2>Section</h2>' + paragraphs(5)
        src = self.build([('c1.xhtml', body)])
        sunk, _ = self.run_process(src, chars_per_page=3000, chapter_sink=0.5)
        self.assertEqual(sunk['pages'], 1)

    def test_recto_adds_blank_pages(self):
        chapters = [('c%d.xhtml' % n, paragraphs(1, seed=n)) for n in range(1, 4)]
        result, z = self.run_process(self.build(chapters), chars_per_page=5000, recto=True)
        self.assertEqual(self.labels(z), ['1', '2', '3', '4', '5'])  # 2 and 4 are blank

    def test_body_start_from_guide_with_roman_front_matter(self):
        chapters = [('title.xhtml', '<p>Title</p>'), ('intro.xhtml', paragraphs(1)),
                    ('c1.xhtml', paragraphs(2))]
        src = self.build(chapters, guide_text='c1.xhtml')
        result, z = self.run_process(src, chars_per_page=5000, body_start='auto')
        self.assertEqual(self.labels(z)[:3], ['i', 'ii', '1'])

    def test_body_start_by_chapter_heading(self):
        chapters = [('info.xhtml', '<p>About this book</p>'),
                    ('c1.xhtml', '<h1>Chapter 1. Start</h1>' + paragraphs(1))]
        with core.Book(self.build(chapters)) as book:
            self.assertEqual(core.detect_body_start(book), 1)

    # ------------------------------------------------------------- errors

    def test_existing_page_list_needs_force(self):
        src = self.build([('c1.xhtml', paragraphs(3))], page_list=True)
        with core.Book(src) as book:
            with self.assertRaises(core.PagesError) as cm:
                core.process(book, core.options(), self.path('out.epub'))
        self.assertEqual(cm.exception.code, 'has_page_list')
        result, z = self.run_process(src, force=True)
        self.assertEqual(z.read('OEBPS/toc.ncx').decode().count('<pageList>'), 1)

    def test_drm_is_refused_but_font_obfuscation_is_not(self):
        chapters = [('c1.xhtml', paragraphs(3))]
        with self.assertRaises(core.PagesError) as cm:
            core.Book(self.build(chapters, encryption='http://www.w3.org/2001/04/xmlenc#aes128-cbc'))
        self.assertEqual(cm.exception.code, 'drm')
        with core.Book(self.build(chapters, encryption='http://www.idpf.org/2008/embedding')):
            pass

    def test_not_an_epub(self):
        bad = self.path('bad.epub')
        with open(bad, 'wb') as f:
            f.write(b'not a zip')
        with self.assertRaises(core.PagesError) as cm:
            core.Book(bad)
        self.assertEqual(cm.exception.code, 'invalid')

    # -------------------------------------------------------------- check

    def test_check_verdicts(self):
        cases = [
            (dict(page_list=True), [('c1.xhtml', paragraphs(2))], 'high', 'none'),
            ({}, [('c1.xhtml', ''.join('<p id="page%d">x</p>' % n for n in range(1, 13)))],
             'likely', 'existing'),
            ({}, [('c1.xhtml', ''.join('<p id="lpn_%d">x</p>' % n for n in range(1, 13)))],
             'low', 'existing'),
            ({}, [('c1.xhtml', paragraphs(2))], 'low', 'synthetic'),
        ]
        for kw, chapters, verdict, advice in cases:
            with core.Book(self.build(chapters, **kw)) as book:
                info = core.check(book)
            self.assertEqual((info['verdict'], info['advice']), (verdict, advice))
            self.assertEqual(info['isbn'], '9780000000002')

    def test_output_is_valid_zip_with_stored_mimetype(self):
        _, z = self.run_process(self.build([('c1.xhtml', paragraphs(3))]))
        first = z.infolist()[0]
        self.assertEqual((first.filename, first.compress_type), ('mimetype', zipfile.ZIP_STORED))
        self.assertIsNone(z.testzip())


if __name__ == '__main__':
    unittest.main()

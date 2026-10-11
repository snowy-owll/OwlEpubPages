"""Plugin preferences and the settings widget."""
from qt.core import (QCheckBox, QComboBox, QFormLayout, QGroupBox, QLabel, QLineEdit,
                     QSpinBox, QVBoxLayout, QWidget)

from calibre.utils.config import JSONConfig

load_translations()

prefs = JSONConfig('plugins/Owl EPUB Pages')
prefs.defaults['mode'] = 'auto'
prefs.defaults['force'] = False
prefs.defaults['keep_original'] = True
prefs.defaults['chars_per_page'] = 2000
prefs.defaults['chars_per_line'] = 60
prefs.defaults['min_last_page'] = 0      # percent of a page
prefs.defaults['chapter_sink'] = 0       # percent of a page
prefs.defaults['recto'] = False
prefs.defaults['break_on'] = ''
prefs.defaults['page_one'] = 'auto'      # auto | book
prefs.defaults['front'] = 'roman'        # roman | skip | number


def core_options():
    """Processing options for core.process() from the saved preferences."""
    from calibre_plugins.owl_epub_pages import core
    return core.options(
        mode=prefs['mode'],
        force=prefs['force'],
        chars_per_page=prefs['chars_per_page'],
        chars_per_line=prefs['chars_per_line'],
        min_last_page=prefs['min_last_page'] / 100,
        chapter_sink=prefs['chapter_sink'] / 100,
        recto=prefs['recto'],
        break_on=prefs['break_on'],
        body_start='auto' if prefs['page_one'] == 'auto' else None,
        front=prefs['front'],
    )


def combo(items, current):
    box = QComboBox()
    for value, text in items:
        box.addItem(text, value)
    box.setCurrentIndex(max(0, box.findData(current)))
    return box


def spin(minimum, maximum, value, step=1, suffix=''):
    box = QSpinBox()
    box.setRange(minimum, maximum)
    box.setSingleStep(step)
    box.setValue(value)
    if suffix:
        box.setSuffix(suffix)
    return box


class ConfigWidget(QWidget):

    def __init__(self):
        QWidget.__init__(self)
        layout = QVBoxLayout(self)

        general = QGroupBox(_('All books'))
        form = QFormLayout(general)
        self.mode = combo([
            ('auto', _('Real print pages if the book has them, otherwise estimated pages')),
            ('existing', _('Only real print pages')),
            ('synthetic', _('Always estimated pages')),
        ], prefs['mode'])
        form.addRow(_('Page numbers:'), self.mode)
        self.force = QCheckBox(_('Replace page numbers the book already has'))
        self.force.setChecked(prefs['force'])
        form.addRow(self.force)
        self.keep_original = QCheckBox(_('Keep the original book as ORIGINAL_EPUB'))
        self.keep_original.setToolTip(_(
            'The unchanged EPUB is saved once as the ORIGINAL_EPUB format, so that it can be '
            'restored with "Restore the original EPUB".'))
        self.keep_original.setChecked(prefs['keep_original'])
        form.addRow(self.keep_original)
        layout.addWidget(general)

        estimated = QGroupBox(_('Estimated pages (books without print page numbers)'))
        form = QFormLayout(estimated)
        self.chars_per_page = spin(300, 6000, prefs['chars_per_page'], 50)
        self.chars_per_page.setToolTip(_('Characters on a full page, spaces included.'))
        form.addRow(_('Characters per page:'), self.chars_per_page)
        hint = QLabel(_('Paperback: about 1500, trade paperback: about 2000, hardcover: '
                        '2300-2600. Smaller pages change more often while reading.'))
        hint.setWordWrap(True)
        form.addRow(hint)
        self.chars_per_line = spin(0, 200, prefs['chars_per_line'])
        self.chars_per_line.setSpecialValueText(_('do not round paragraphs'))
        self.chars_per_line.setToolTip(_(
            'The end of a paragraph fills the rest of its line, as in a printed book. '
            '0 counts plain characters.'))
        form.addRow(_('Characters per line:'), self.chars_per_line)
        self.min_last_page = spin(0, 90, prefs['min_last_page'], 5, ' %')
        self.min_last_page.setSpecialValueText(_('never'))
        self.min_last_page.setToolTip(_(
            'A chapter whose last page would be filled less than this is squeezed onto the '
            'previous page, as typesetters do. Fewer page numbers are skipped when a chapter '
            'ends on Kindle.'))
        form.addRow(_('Merge a short last page of a chapter:'), self.min_last_page)
        self.chapter_sink = spin(0, 80, prefs['chapter_sink'], 5, ' %')
        self.chapter_sink.setSpecialValueText(_('heading text only'))
        self.chapter_sink.setToolTip(_(
            'Share of a page taken by a heading that opens a page, together with the space '
            'around it. The first page of a chapter then holds less text, as in printed '
            'books, where it is usually 20-30 %.'))
        form.addRow(_('A chapter heading takes at least:'), self.chapter_sink)
        self.recto = QCheckBox(_('Start every chapter on an odd page, adding a blank page if needed'))
        self.recto.setChecked(prefs['recto'])
        form.addRow(self.recto)
        self.break_on = QLineEdit(prefs['break_on'])
        self.break_on.setPlaceholderText('h1, h2')
        self.break_on.setToolTip(_(
            'HTML tags that always start a new page, separated by commas. Every chapter file '
            'and every CSS page break already starts a new page.'))
        form.addRow(_('Also start a new page at:'), self.break_on)
        self.page_one = combo([
            ('auto', _('Start of the main text (detected automatically)')),
            ('book', _('Start of the book')),
        ], prefs['page_one'])
        form.addRow(_('Page 1 is at:'), self.page_one)
        self.front = combo([
            ('roman', _('Roman numerals (i, ii, iii)')),
            ('skip', _('No page numbers')),
            ('number', _('Normal numbering')),
        ], prefs['front'])
        form.addRow(_('Pages before the main text:'), self.front)
        numbers = (self.chars_per_page, self.chars_per_line, self.min_last_page,
                   self.chapter_sink)
        width = max(box.sizeHint().width() for box in numbers)
        for box in numbers + (self.break_on,):
            box.setFixedWidth(width)
        layout.addWidget(estimated)
        layout.addStretch(1)

    def save_settings(self):
        prefs['mode'] = self.mode.currentData()
        prefs['force'] = self.force.isChecked()
        prefs['keep_original'] = self.keep_original.isChecked()
        prefs['chars_per_page'] = self.chars_per_page.value()
        prefs['chars_per_line'] = self.chars_per_line.value()
        prefs['min_last_page'] = self.min_last_page.value()
        prefs['chapter_sink'] = self.chapter_sink.value()
        prefs['recto'] = self.recto.isChecked()
        prefs['break_on'] = self.break_on.text().strip()
        prefs['page_one'] = self.page_one.currentData()
        prefs['front'] = self.front.currentData()

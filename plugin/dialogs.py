"""Dialogs that show the results of checking and adding page numbers."""
from qt.core import (QAbstractItemView, QDialog, QDialogButtonBox, QHeaderView, QLabel,
                     QTableWidget, QTableWidgetItem, QVBoxLayout)

load_translations()


def verdict_text(info):
    return {
        'high': _('High: the book has page numbers'),
        'likely': _('Likely: print page anchors without a page list'),
        'low': _('Low: the book has no page list'),
    }[info['verdict']]


def page_list_text(info):
    if info['page_list']:
        return ngettext('%d page', '%d pages', info['page_list']) % info['page_list']
    return _('none')


def anchors_text(info):
    if not info['anchors']:
        return _('none')
    return _('pages %(first)s-%(last)s') % info


def advice_text(info):
    if info['advice'] == 'none':
        return _('Nothing to do')
    if info['advice'] == 'existing':
        return _('Add page numbers: real print pages')
    return _('Add page numbers: estimated pages')


def error_text(code, detail):
    return {
        'invalid': _('Not a valid EPUB file'),
        'drm': _('The book is DRM-protected'),
        'has_page_list': _('Skipped: the book already has page numbers'),
        'no_anchors': _('Skipped: the book has no print page numbers'),
        'no_toc': _('The book has no table of contents file (NCX or nav)'),
        'body_start': _('The start of the main text was not found'),
        'no_pages': _('No text to number'),
        'no_epub': _('No EPUB format'),
    }.get(code) or _('Error: %s') % detail


def result_text(result):
    args = {'n': result['pages'], 'first': result['first'], 'last': result['last']}
    if result['mode'] == 'existing':
        return _('Real print pages: %(n)d (%(first)s-%(last)s)') % args
    return _('Estimated pages: %(n)d (%(first)s-%(last)s)') % args


class TableDialog(QDialog):

    def __init__(self, parent, title, summary, headers, rows):
        QDialog.__init__(self, parent)
        self.setWindowTitle(title)
        layout = QVBoxLayout(self)
        label = QLabel(summary)
        label.setWordWrap(True)
        layout.addWidget(label)

        table = QTableWidget(len(rows), len(headers), self)
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.verticalHeader().setVisible(False)
        table.setWordWrap(False)
        for r, row in enumerate(rows):
            for c, text in enumerate(row):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                table.setItem(r, c, item)
        table.resizeColumnsToContents()
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        if header.sectionSize(0) > 280:
            header.resizeSection(0, 280)  # long titles are elided, the full title is a tooltip
        header.setStretchLastSection(True)
        layout.addWidget(table)
        self.table = table

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        width = sum(header.sectionSize(c) for c in range(len(headers))) + 60
        screen = self.screen().availableGeometry() if self.screen() else None
        max_width = screen.width() - 80 if screen else 1200
        self.resize(max(600, min(width, max_width)), min(600, 170 + 30 * len(rows)))


def check_dialog(parent, results, without_epub):
    rows = []
    for r in results:
        if 'check' in r:
            info = r['check']
            rows.append([r['title'], verdict_text(info), page_list_text(info),
                         anchors_text(info), advice_text(info)])
        else:
            rows.append([r['title'], error_text(r['error'], r['detail']), '', '', ''])
    rows += [[title, error_text('no_epub', ''), '', '', ''] for title in without_epub]
    return TableDialog(
        parent, _('Page numbers check'),
        _('How likely Kindle is to show page numbers for each book. A page list in the book '
          'always works; for the other books use "Add page numbers".'),
        [_('Title'), _('Kindle page numbers'), _('Page list'), _('Print page anchors'),
         _('Recommendation')],
        rows)


def results_dialog(parent, results, without_epub):
    rows, done = [], 0
    for r in results:
        if 'result' in r and 'error' not in r:
            done += 1
            rows.append([r['title'], result_text(r['result'])])
        else:
            rows.append([r['title'], error_text(r['error'], r['detail'])])
    rows += [[title, error_text('no_epub', '')] for title in without_epub]
    total = len(results) + len(without_epub)
    return TableDialog(
        parent, _('Page numbers added'),
        _('Books with page numbers added: %(done)d of %(total)d.') % {'done': done, 'total': total},
        [_('Title'), _('Result')], rows)

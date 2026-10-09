"""The toolbar button and its menu."""
import os
from functools import partial

from qt.core import QMenu, QToolButton

from calibre.gui2 import Dispatcher, error_dialog, info_dialog, question_dialog
from calibre.gui2.actions import InterfaceAction
from calibre.gui2.threaded_jobs import ThreadedJob

from calibre_plugins.owl_epub_pages import jobs
from calibre_plugins.owl_epub_pages.config import core_options, prefs
from calibre_plugins.owl_epub_pages.dialogs import check_dialog, results_dialog

load_translations()


class OwlEpubPagesAction(InterfaceAction):
    name = 'Owl EPUB Pages'
    action_spec = (_('Page numbers'), None, _('Add page numbers to the selected EPUB books'), None)
    popup_type = QToolButton.ToolButtonPopupMode.MenuButtonPopup
    action_type = 'current'
    dont_add_to = frozenset({'context-menu-device', 'toolbar-device', 'menubar-device'})

    def genesis(self):
        self.qaction.setIcon(get_icons('images/icon.png', 'Owl EPUB Pages'))
        self.qaction.triggered.connect(self.add_pages)
        self.menu = QMenu(self.gui)
        self.create_menu_action(self.menu, 'owl-epub-pages-add', _('Add page numbers'),
                                triggered=self.add_pages)
        self.create_menu_action(self.menu, 'owl-epub-pages-check', _('Check page numbers'),
                                triggered=self.check_pages)
        self.create_menu_action(self.menu, 'owl-epub-pages-restore', _('Restore the original EPUB'),
                                triggered=self.restore_original)
        self.menu.addSeparator()
        self.create_menu_action(self.menu, 'owl-epub-pages-settings', _('Settings'),
                                triggered=self.show_settings)
        self.qaction.setMenu(self.menu)

    # ---------------------------------------------------------------- helpers

    def selected_ids(self):
        view = self.gui.library_view
        ids = list(map(view.model().id, view.selectionModel().selectedRows()))
        if not ids:
            error_dialog(self.gui, _('No books selected'),
                         _('Select one or more books in the library first.'), show=True)
        return ids

    def refresh(self, ids):
        view = self.gui.library_view
        view.model().refresh_ids(ids, current_row=view.currentIndex().row())

    def show_settings(self):
        self.interface_action_base_plugin.do_user_config(self.gui)

    # ---------------------------------------------------------------- actions

    def add_pages(self):
        self.start(check_only=False)

    def check_pages(self):
        self.start(check_only=True)

    def start(self, check_only):
        ids = self.selected_ids()
        if not ids:
            return
        db = self.gui.current_db.new_api
        books, without_epub = [], []
        for book_id in ids:
            title = db.field_for('title', book_id)
            if db.has_format(book_id, 'EPUB'):
                books.append((book_id, title, db.format(book_id, 'EPUB', as_path=True)))
            else:
                without_epub.append(title)
        if not books:
            error_dialog(self.gui, _('No EPUB books'),
                         _('None of the selected books has the EPUB format.'), show=True)
            return

        count = ngettext('%d book', '%d books', len(books)) % len(books)
        if check_only:
            description = _('Check page numbers: %s') % count
        else:
            description = _('Add page numbers: %s') % count
        job = ThreadedJob('owl_epub_pages', description, jobs.run,
                          (books, core_options(), check_only), {},
                          Dispatcher(partial(self.job_done, check_only, without_epub, db.library_id)))
        self.gui.job_manager.run_threaded_job(job)
        self.gui.status_bar.show_message(description, 3000)

    def job_done(self, check_only, without_epub, library_id, job):
        if job.failed:
            self.gui.job_exception(job, dialog_title=_('Page numbers failed'))
            return
        results = job.result
        if check_only:
            check_dialog(self.gui, results, without_epub).exec()
            return

        db = self.gui.current_db.new_api
        outputs = [r['output'] for r in results if r.get('output')]
        if db.library_id != library_id:
            for path in outputs:
                jobs.remove(path)
            error_dialog(self.gui, _('Library changed'),
                         _('The library was switched while page numbers were being added. '
                           'Nothing was changed.'), show=True)
            return

        changed = []
        for r in results:
            output = r.get('output')
            if not output:
                continue
            try:
                if prefs['keep_original'] and not db.has_format(r['id'], 'ORIGINAL_EPUB'):
                    db.save_original_format(r['id'], 'EPUB')
                db.add_format(r['id'], 'EPUB', output, run_hooks=False)
                changed.append(r['id'])
            except Exception as e:
                r['error'], r['detail'] = 'exception', '%s: %s' % (type(e).__name__, e)
            finally:
                jobs.remove(output)
        if changed:
            self.refresh(changed)
        results_dialog(self.gui, results, without_epub).exec()

    def restore_original(self):
        ids = self.selected_ids()
        if not ids:
            return
        db = self.gui.current_db.new_api
        with_original = [i for i in ids if db.has_format(i, 'ORIGINAL_EPUB')]
        if not with_original:
            error_dialog(self.gui, _('Nothing to restore'),
                         _('None of the selected books has an ORIGINAL_EPUB format.'), show=True)
            return
        count = len(with_original)
        if not question_dialog(self.gui, _('Restore the original EPUB'),
                               _('Books with ORIGINAL_EPUB: %d. Replace their EPUB with '
                                 'ORIGINAL_EPUB? Page numbers added by this plugin will be '
                                 'removed.') % count):
            return
        for book_id in with_original:
            db.restore_original_format(book_id, 'ORIGINAL_EPUB')
        self.refresh(with_original)
        info_dialog(self.gui, _('Restore the original EPUB'),
                    _('The original EPUB was restored. Books: %d.') % count, show=True)

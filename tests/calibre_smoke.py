"""End-to-end smoke test inside calibre, without the main window.

Install the plugin into a throwaway calibre configuration first, then run:

    set CALIBRE_CONFIG_DIRECTORY=<empty temp dir>
    calibre-customize -a dist/OwlEpubPages-<version>.zip
    calibre-debug -e tests/calibre_smoke.py <work dir> book1.epub [book2.epub ...]

A temporary library is created in <work dir>/library; the plugin's Check, Add and
Restore actions run on all books through a stand-in main window, and every dialog
and the settings widget are saved as PNG files into <work dir>. Set
CALIBRE_OVERRIDE_LANG=ru to test the Russian translation.
"""
import os
import shutil
import sys

work, paths = sys.argv[1], sys.argv[2:]
os.makedirs(work, exist_ok=True)
lang = os.environ.get('CALIBRE_OVERRIDE_LANG', 'en')

from qt.core import QMainWindow

from calibre.customize.ui import find_plugin
from calibre.ebooks.metadata.meta import get_metadata
from calibre.gui2 import Application
from calibre.library import db as open_library

app = Application([])
plugin = find_plugin('Owl EPUB Pages')
assert plugin is not None, 'plugin is not installed in CALIBRE_CONFIG_DIRECTORY'
print('plugin', plugin.name, plugin.version)

from calibre_plugins.owl_epub_pages import action as action_module
from calibre_plugins.owl_epub_pages import config, core, dialogs

# ------------------------------------------------------------- library
lib = os.path.join(work, 'library')
shutil.rmtree(lib, ignore_errors=True)
os.makedirs(lib)
legacy = open_library(lib)
db = legacy.new_api
ids = []
for path in paths:
    with open(path, 'rb') as f:
        mi = get_metadata(f, 'epub')
    ids += db.add_books([(mi, {'EPUB': path})], run_hooks=False)[0]
print('library books:', ids)

# ------------------------------------------------------- stand-in GUI
shots = []


def save_dialog(self):
    name = os.path.join(work, '%s-dialog-%d.png' % (lang, len(shots) + 1))
    self.grab().save(name)
    shots.append(name)
    print('DIALOG', self.windowTitle(), '->', os.path.basename(name))
    for r in range(self.table.rowCount()):
        print('   ', ' | '.join(self.table.item(r, c).text() if self.table.item(r, c) else ''
                                for c in range(self.table.columnCount())))
    return 0


dialogs.TableDialog.exec = save_dialog
action_module.info_dialog = lambda parent, title, msg, **kw: print('INFO', title, '-', msg)
action_module.error_dialog = lambda parent, title, msg, **kw: print('ERROR', title, '-', msg)
action_module.question_dialog = lambda parent, title, msg, **kw: print('QUESTION', title, '-', msg) or True


class Index:
    def row(self):
        return -1


class Selection:
    def selectedRows(self):
        return list(ids)


class Model:
    def id(self, row):
        return row

    def refresh_ids(self, changed, current_row=-1):
        print('refresh_ids', changed)


class View:
    def model(self):
        return Model()

    def selectionModel(self):
        return Selection()

    def currentIndex(self):
        return Index()


class Jobs:
    def run_threaded_job(self, job):
        job.start_work()  # synchronously; the callback is delivered by the event loop


class Status:
    def show_message(self, msg, timeout=0):
        print('STATUS', msg)


class Keyboard:
    def register_shortcut(self, *args, **kwargs):
        pass

    def finalize(self):
        pass


class Gui(QMainWindow):
    pass


gui = Gui()
gui.current_db = legacy
gui.library_view = View()
gui.job_manager = Jobs()
gui.status_bar = Status()
gui.keyboard = Keyboard()
gui.job_exception = lambda job, dialog_title='': print('JOB FAILED', dialog_title, job.details)

act = action_module.OwlEpubPagesAction(gui, None)
act.interface_action_base_plugin = plugin
act.do_genesis()
print('menu:', [a.text() for a in act.menu.actions() if a.text()])


def run(method):
    method()
    for _ in range(50):
        app.processEvents()


# ---------------------------------------------------------- scenario
config.prefs['chars_per_page'] = 1500
config.prefs['min_last_page'] = 25

run(act.check_pages)
run(act.add_pages)
failures = []
for book_id in ids:
    formats = db.formats(book_id)
    with core.Book(db.format_abspath(book_id, 'EPUB')) as book:
        info = core.check(book)
    print('book %d formats %s, page list %d' % (book_id, formats, info['page_list']))
    if not info['page_list']:
        failures.append('no page list in book %d' % book_id)
run(act.check_pages)
run(act.restore_original)
for book_id in ids:
    if 'ORIGINAL_EPUB' in db.formats(book_id):
        failures.append('ORIGINAL_EPUB still present in book %d' % book_id)

widget = config.ConfigWidget()
widget.resize(720, widget.sizeHint().height())
name = os.path.join(work, '%s-settings.png' % lang)
widget.grab().save(name)
print('SETTINGS ->', os.path.basename(name))

legacy.close()
print('FAILURES:', failures or 'none')

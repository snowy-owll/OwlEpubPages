"""Work done in a calibre background job thread: no GUI or database calls here."""
import os

from calibre_plugins.owl_epub_pages import core


def remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def run(books, opts, check_only, abort=None, log=None, notifications=None, **kwargs):
    """Check or add page numbers for books.

    books: [(book_id, title, path)], path is a temporary copy of the EPUB that is
    deleted here. Returns a list of dicts with id, title, check and either
    result + output (the new EPUB, a temporary file) or error + detail."""
    results = []
    for n, (book_id, title, path) in enumerate(books):
        if abort is not None and abort.is_set():
            remove(path)
            continue
        if notifications is not None:
            notifications.put((n / len(books), title))
        r = {'id': book_id, 'title': title}
        try:
            with core.Book(path) as book:
                anchors = core.find_existing(book)
                r['check'] = core.check(book, anchors)
                if not check_only:
                    output = os.path.splitext(path)[0] + '-pages.epub'
                    r['result'] = core.process(book, opts, output, anchors)
                    r['output'] = output
        except core.PagesError as e:
            r['error'], r['detail'] = e.code, e.detail
        except Exception as e:
            if log is not None:
                log.exception('Owl EPUB Pages failed on: %s' % title)
            r['error'], r['detail'] = 'exception', '%s: %s' % (type(e).__name__, e)
        remove(path)
        results.append(r)
    return results

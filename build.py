#!/usr/bin/env python3
"""Build the calibre plugin zip: compile translations and package plugin/.

  python build.py          build dist/OwlEpubPages-<version>.zip
  python build.py --pot    (re)write plugin/translations/messages.pot

Translations are plugin/translations/<language>.po files (e.g. ru.po); they are
compiled to .mo files inside the zip, no gettext tools needed. The build warns
about strings that are missing from a .po file.
"""
import argparse
import ast
import os
import re
import struct
import sys
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.join(ROOT, 'plugin')
TRANSLATIONS = os.path.join(PLUGIN, 'translations')
DIST = os.path.join(ROOT, 'dist')


def version():
    with open(os.path.join(PLUGIN, '__init__.py'), encoding='utf-8') as f:
        m = re.search(r'PLUGIN_VERSION\s*=\s*\((\d+),\s*(\d+),\s*(\d+)\)', f.read())
    return '.'.join(m.groups())


def sources():
    return sorted(n for n in os.listdir(PLUGIN) if n.endswith('.py'))


def extract():
    """Return {msgid or (singular, plural): [file:line, ...]} for _() and ngettext() calls."""
    found = {}
    for name in sources():
        with open(os.path.join(PLUGIN, name), encoding='utf-8') as f:
            tree = ast.parse(f.read(), name)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            args = [a.value for a in node.args if isinstance(a, ast.Constant)
                    and isinstance(a.value, str)]
            if node.func.id == '_' and len(args) == 1:
                key = args[0]
            elif node.func.id == 'ngettext' and len(args) == 2:
                key = tuple(args)
            else:
                continue
            found.setdefault(key, []).append((name, node.lineno))
    # in source order, references as file:line
    ordered = sorted(found.items(), key=lambda kv: min(kv[1]))
    return {k: ['%s:%d' % w for w in sorted(v)] for k, v in ordered}


def po_quote(s):
    s = s.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')
    return '"%s"' % s


def write_pot(found):
    lines = ['msgid ""', 'msgstr ""',
             '"Project-Id-Version: Owl EPUB Pages %s\\n"' % version(),
             '"MIME-Version: 1.0\\n"',
             '"Content-Type: text/plain; charset=UTF-8\\n"',
             '"Content-Transfer-Encoding: 8bit\\n"', '']
    for key, where in found.items():
        lines.append('#: ' + ' '.join(where))
        if isinstance(key, tuple):
            lines += ['msgid ' + po_quote(key[0]), 'msgid_plural ' + po_quote(key[1]),
                      'msgstr[0] ""', 'msgstr[1] ""']
        else:
            lines += ['msgid ' + po_quote(key), 'msgstr ""']
        lines.append('')
    path = os.path.join(TRANSLATIONS, 'messages.pot')
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(lines))
    print('wrote', os.path.relpath(path, ROOT), '(%d strings)' % len(found))


def po_unquote(s):
    return re.sub(r'\\(.)', lambda m: {'n': '\n', 't': '\t'}.get(m.group(1), m.group(1)),
                  s.strip()[1:-1])


def read_po(path):
    """Return {msgid or (singular, plural): msgstr or [forms]}; fuzzy entries are skipped."""
    entries, cur, field, fuzzy = {}, {}, None, False

    def flush():
        nonlocal cur, fuzzy
        if 'msgid' in cur and not fuzzy:
            if 'msgid_plural' in cur:
                forms = [cur[k] for k in sorted(k for k in cur if k.startswith('msgstr['))]
                entries[(cur['msgid'], cur['msgid_plural'])] = forms
            else:
                entries[cur['msgid']] = cur.get('msgstr', '')
        cur, fuzzy = {}, False

    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                flush()
            elif line.startswith('#,') and 'fuzzy' in line:
                fuzzy = True
            elif line.startswith('#'):
                continue
            elif line.startswith('"'):
                cur[field] += po_unquote(line)
            else:
                field, _, value = line.partition(' ')
                if field == 'msgid' and 'msgid' in cur:
                    flush()
                cur[field] = po_unquote(value)
    flush()
    return entries


def compile_mo(entries):
    """GNU .mo file contents for {msgid: msgstr} (plural keys are tuples)."""
    pairs = []
    for key, value in entries.items():
        if isinstance(key, tuple):
            if not all(value):
                continue
            pairs.append(('\0'.join(key), '\0'.join(value)))
        elif value:
            pairs.append((key, value))
    pairs.sort()
    ids = b''.join(k.encode('utf-8') + b'\0' for k, _ in pairs)
    strs = b''.join(v.encode('utf-8') + b'\0' for _, v in pairs)
    n = len(pairs)
    ids_start = 7 * 4 + 16 * n
    strs_start = ids_start + len(ids)
    table, offsets, pos = [], [], 0
    for k, _ in pairs:
        table += [len(k.encode('utf-8')), ids_start + pos]
        pos += len(k.encode('utf-8')) + 1
    pos = 0
    for _, v in pairs:
        offsets += [len(v.encode('utf-8')), strs_start + pos]
        pos += len(v.encode('utf-8')) + 1
    header = struct.pack('Iiiiiii', 0x950412de, 0, n, 7 * 4, 7 * 4 + 8 * n, 0, 0)
    return (header + struct.pack('%di' % (2 * n), *table)
            + struct.pack('%di' % (2 * n), *offsets) + ids + strs)


def build():
    found = extract()
    mo_files = {}
    for name in sorted(os.listdir(TRANSLATIONS)):
        if not name.endswith('.po'):
            continue
        lang = name[:-3]
        entries = read_po(os.path.join(TRANSLATIONS, name))
        missing = [k for k in found if not entries.get(k) or
                   (isinstance(k, tuple) and not all(entries[k]))]
        unused = [k for k in entries if k and k not in found]
        for k in missing:
            print('%s: missing translation: %r' % (name, k), file=sys.stderr)
        for k in unused:
            print('%s: unused translation: %r' % (name, k), file=sys.stderr)
        mo_files['translations/%s.mo' % lang] = compile_mo(entries)
        print('%s: %d of %d strings translated' % (lang, len(found) - len(missing), len(found)))

    os.makedirs(DIST, exist_ok=True)
    out = os.path.join(DIST, 'OwlEpubPages-%s.zip' % version())
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for dirpath, dirnames, filenames in os.walk(PLUGIN):
            dirnames[:] = [d for d in dirnames if d != '__pycache__']
            for name in sorted(filenames):
                if name.endswith(('.po', '.pot', '.pyc')):
                    continue
                full = os.path.join(dirpath, name)
                z.write(full, os.path.relpath(full, PLUGIN).replace(os.sep, '/'))
        for name, data in mo_files.items():
            z.writestr(name, data)
    print('built', os.path.relpath(out, ROOT))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--pot', action='store_true', help='write the translation template')
    args = ap.parse_args()
    if args.pot:
        write_pot(extract())
    else:
        build()


if __name__ == '__main__':
    main()

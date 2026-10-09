"""Owl EPUB Pages: a calibre plugin that adds page numbers (a page-list) to EPUB books."""
from calibre.customize import InterfaceActionBase

PLUGIN_NAME = 'Owl EPUB Pages'
PLUGIN_VERSION = (1, 0, 0)


class OwlEpubPagesPlugin(InterfaceActionBase):
    name = PLUGIN_NAME
    description = ('Adds page numbers (a page-list) to EPUB books so that Kindle and other '
                   'readers show pages: real print pages when the book contains them, '
                   'estimated pages otherwise.')
    supported_platforms = ['windows', 'osx', 'linux']
    author = 'Snowy Owl'
    version = PLUGIN_VERSION
    minimum_calibre_version = (6, 0, 0)
    actual_plugin = 'calibre_plugins.owl_epub_pages.action:OwlEpubPagesAction'

    def is_customizable(self):
        return True

    def config_widget(self):
        from calibre_plugins.owl_epub_pages.config import ConfigWidget
        return ConfigWidget()

    def save_settings(self, config_widget):
        config_widget.save_settings()

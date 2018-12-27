# -*- coding: utf-8 -*-

"""
/***************************************************************************
                              -------------------
        begin                : 2016
        copyright            : (C) 2016 by OPENGIS.ch
        email                : info@opengis.ch
 ***************************************************************************/

/***************************************************************************
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 2 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 ***************************************************************************/
"""

import os.path
import inspect
import functools
from qgis.PyQt.QtWidgets import (
    QFileDialog,
    QApplication
)
from qgis.PyQt.QtCore import (
    QCoreApplication,
    QObject,
    QFile,
    QIODevice,
    QEventLoop,
    QUrl
)
from qgis.PyQt.QtGui import QValidator
from qgis.PyQt.QtNetwork import QNetworkRequest
from qgis.core import QgsNetworkAccessManager
from functools import partial
import fnmatch


def selectFileName(line_edit_widget, title, file_filter, parent):
    filename, matched_filter = QFileDialog.getOpenFileName(
        parent, title, line_edit_widget.text(), file_filter)
    line_edit_widget.setText(filename)


def make_file_selector(widget, title=QCoreApplication.translate('QgisModelBaker', 'Open File'),
                       file_filter=QCoreApplication.translate('QgisModelBaker', 'Any file (*)'), parent=None):
    return partial(selectFileName, line_edit_widget=widget, title=title, file_filter=file_filter, parent=parent)


def selectFileNameToSave(line_edit_widget, title, file_filter, parent, extension, extensions):
    filename, matched_filter = QFileDialog.getSaveFileName(parent, title, line_edit_widget.text(), file_filter)
    extension_valid = False

    if not extensions:
        extensions = [extension]

    if extensions:
        extension_valid = any(filename.endswith(ext) for ext in extensions)

    if not extension_valid and filename:
        filename = filename + extension

    line_edit_widget.setText(filename)


def make_save_file_selector(widget, title=QCoreApplication.translate('QgisModelBaker', 'Open File'),
                            file_filter=QCoreApplication.translate('QgisModelBaker', 'Any file(*)'), parent=None,
                            extension='', extensions=None):
    return partial(selectFileNameToSave, line_edit_widget=widget, title=title, file_filter=file_filter, parent=parent,
                   extension=extension, extensions=extensions)


def selectFolder(line_edit_widget, title, parent):
    foldername = QFileDialog.getExistingDirectory(
        parent, title, line_edit_widget.text())
    line_edit_widget.setText(foldername)


def make_folder_selector(widget, title=QCoreApplication.translate('QgisModelBaker', 'Open Folder'), parent=None):
    return partial(selectFolder, line_edit_widget=widget, title=title, parent=parent)

class NetworkError(RuntimeError):

    def __init__(self, error_code, msg):
        self.msg = msg
        self.error_code = error_code


replies = list()


def download_file(url, filename, on_progress=None, on_finished=None, on_error=None, on_success=None):
    """
    Will download the file from url to a local filename.
    The method will only return once it's finished.

    While downloading it will repeatedly report progress by calling on_progress
    with two parameters bytes_received and bytes_total.

    If an error occurs, it raises a NetworkError exception.

    It will return the filename if everything was ok.
    """
    network_access_manager = QgsNetworkAccessManager.instance()

    req = QNetworkRequest(QUrl(url))
    req.setAttribute(QNetworkRequest.CacheSaveControlAttribute, False)
    req.setAttribute(QNetworkRequest.CacheLoadControlAttribute, QNetworkRequest.AlwaysNetwork)
    reply = network_access_manager.get(req)

    def on_download_progress(bytes_received, bytes_total):
        on_progress(bytes_received, bytes_total)

    def finished(filename, reply, on_error, on_success, on_finished):
        file = QFile(filename)
        file.open(QIODevice.WriteOnly)
        file.write(reply.readAll())
        file.close()
        if reply.error() and on_error:
            on_error(reply.error(), reply.errorString())
        elif not reply.error() and on_success:
            on_success()

        if on_finished:
            on_finished()
        reply.deleteLater()
        replies.remove(reply)

    if on_progress:
        reply.downloadProgress.connect(on_download_progress)

    on_reply_finished = functools.partial(finished, filename, reply, on_error, on_success, on_finished)

    reply.finished.connect(on_reply_finished)

    replies.append(reply)

    if not on_finished and not on_success:
        loop = QEventLoop()
        reply.finished.connect(loop.quit)
        loop.exec_()

        if reply.error():
            raise NetworkError(reply.error(), reply.errorString())
        else:
            return filename


class Validators(QObject):

    def validate_line_edits(self, *args, **kwargs):
        """
        Validate line edits and set their color to indicate validation state.
        """
        senderObj = self.sender()
        validator = senderObj.validator()
        if validator is None:
            color = '#fff'  # White
        else:
            state = validator.validate(senderObj.text().strip(), 0)[0]
            if state == QValidator.Acceptable:
                color = '#fff'  # White
            elif state == QValidator.Intermediate:
                color = '#ffd356'  # Light orange
            else:
                color = '#f6989d'  # Red
        senderObj.setStyleSheet(
            'QLineEdit {{ background-color: {} }}'.format(color))


class FileValidator(QValidator):

    def __init__(self, pattern='*', is_executable=False, parent=None, allow_empty=False, allow_non_existing=False):
        """
        Validates if a string is a valid filename, based on the provided parameters.

        :param pattern: A file glob pattern as recognized by ``fnmatch``, if a list if provided, the validator will try
                        to match every pattern in the list.
        :param is_executable: Only match executable files
        :param parent: The parent QObject
        :param allow_empty: Empty strings are valid
        :param allow_non_existing: Non existing files are valid
        """
        QValidator.__init__(self, parent)
        self.pattern = pattern
        self.is_executable = is_executable
        self.allow_empty = allow_empty
        self.allow_non_existing = allow_non_existing

    """
    Validator for file line edits
    """

    def validate(self, text, pos):
        if self.allow_empty and not text.strip():
            return QValidator.Acceptable, text, pos

        pattern_matches = False
        if type(self.pattern) is str:
            pattern_matches = fnmatch.fnmatch(text, self.pattern)
        elif type(self.pattern) is list:
            pattern_matches = True in (fnmatch.fnmatch(text, pattern) for pattern in self.pattern)
        else:
            raise TypeError('pattern must be str or list, not {}'.format(type(self.pattern)))

        if not text \
                or (not self.allow_non_existing and not os.path.isfile(text)) \
                or not pattern_matches \
                or (self.is_executable and not os.access(text, os.X_OK)):
            return QValidator.Intermediate, text, pos
        else:
            return QValidator.Acceptable, text, pos


class NonEmptyStringValidator(QValidator):

    def __init__(self, parent=None):
        QValidator.__init__(self, parent)

    def validate(self, text, pos):
        if not text.strip():
            return QValidator.Intermediate, text, pos

        return QValidator.Acceptable, text, pos


class OverrideCursor():

    def __init__(self, cursor):
        self.cursor = cursor

    def __enter__(self):
        QApplication.setOverrideCursor(self.cursor)

    def __exit__(self, exc_type, exc_val, exc_tb):
        QApplication.restoreOverrideCursor()

# -*- coding: utf-8 -*-
"""
/***************************************************************************
                              -------------------
        begin                : 29/03/17
        git sha              : :%H$
        copyright            : (C) 2017 by OPENGIS.ch
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

import os
import webbrowser

import re
from psycopg2 import OperationalError

from QgisModelBaker.gui.options import OptionsDialog, CompletionLineEdit
from QgisModelBaker.gui.ili2db_options import Ili2dbOptionsDialog
from QgisModelBaker.gui.multiple_models import MultipleModelsDialog
from QgisModelBaker.libili2db.globals import CRS_PATTERNS, displayDbIliMode, DbActionType
from QgisModelBaker.libili2db.ili2dbconfig import SchemaImportConfiguration
from QgisModelBaker.libili2db.ilicache import IliCache, ModelCompleterDelegate
from QgisModelBaker.libili2db.iliimporter import JavaNotFoundError
from QgisModelBaker.libili2db.ili2dbutils import color_log_text
from QgisModelBaker.utils.qt_utils import (
    make_file_selector,
    Validators,
    FileValidator,
    NonEmptyStringValidator,
    OverrideCursor
)
from qgis.PyQt.QtGui import (
    QColor,
    QDesktopServices,
    QValidator
)
from qgis.PyQt.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QCompleter,
    QSizePolicy,
    QGridLayout
)
from qgis.PyQt.QtCore import (
    QCoreApplication,
    QSettings,
    Qt,
    QLocale
)
from qgis.core import (
    QgsProject,
    QgsCoordinateReferenceSystem,
    Qgis
)
from qgis.gui import (
    QgsMessageBar,
    QgsGui
)
from ..utils import get_ui_class
from ..libili2db import iliimporter
from ..libili2db.globals import DbIliMode
from ..libqgsprojectgen.generator.generator import Generator
from ..libqgsprojectgen.dataobjects import Project
from ..libqgsprojectgen.db_factory.db_simple_factory import DbSimpleFactory
from ..libqgsprojectgen.dbconnector.db_connector import DBConnectorError

DIALOG_UI = get_ui_class('generate_project.ui')


class GenerateProjectDialog(QDialog, DIALOG_UI):

    def __init__(self, iface, base_config, parent=None):
        QDialog.__init__(self, parent)
        self.setupUi(self)
        self.iface = iface
        self.db_simple_factory = DbSimpleFactory()
        QgsGui.instance().enableAutoGeometryRestore(self)
        self.buttonBox.accepted.disconnect()
        self.buttonBox.accepted.connect(self.accepted)
        self.buttonBox.clear()
        self.buttonBox.addButton(QDialogButtonBox.Cancel)
        create_button = self.buttonBox.addButton(
            self.tr('Create'), QDialogButtonBox.AcceptRole)
        create_button.setDefault(True)
        self.ili_file_browse_button.clicked.connect(
            make_file_selector(self.ili_file_line_edit, title=self.tr('Open Interlis Model'),
                               file_filter=self.tr('Interlis Model File (*.ili)')))
        self.buttonBox.addButton(QDialogButtonBox.Help)
        self.buttonBox.helpRequested.connect(self.help_requested)
        self.crs = QgsCoordinateReferenceSystem()
        self.ili2db_options = Ili2dbOptionsDialog()
        self.ili2db_options_button.clicked.connect(self.ili2db_options.open)
        self.ili2db_options.finished.connect(self.fill_toml_file_info_label)
        self.multiple_models_dialog = MultipleModelsDialog(self)
        self.multiple_models_button.clicked.connect(
            self.multiple_models_dialog.open)
        self.multiple_models_dialog.accepted.connect(
            self.fill_models_line_edit)

        self.type_combo_box.clear()
        self._lst_panel = dict()

        for db_id in self.db_simple_factory.get_db_list(True):
            self.type_combo_box.addItem(displayDbIliMode[db_id], db_id)

        for db_id in self.db_simple_factory.get_db_list(False):
            db_factory = self.db_simple_factory.create_factory(db_id)
            item_panel = db_factory.get_config_panel(self, DbActionType.GENERATE)
            self._lst_panel[db_id] = item_panel
            self.db_layout.addWidget(item_panel)

        self.type_combo_box.currentIndexChanged.connect(self.type_changed)
        self.txtStdout.anchorClicked.connect(self.link_activated)
        self.crsSelector.crsChanged.connect(self.crs_changed)
        self.base_configuration = base_config

        self.bar = QgsMessageBar()
        self.bar.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        self.txtStdout.setLayout(QGridLayout())
        self.txtStdout.layout().setContentsMargins(0, 0, 0, 0)
        self.txtStdout.layout().addWidget(self.bar, 0, 0, Qt.AlignTop)

        self.validators = Validators()
        nonEmptyValidator = NonEmptyStringValidator()
        fileValidator = FileValidator(pattern='*.ili', allow_empty=True)

        self.restore_configuration()

        self.ili_models_line_edit.setValidator(nonEmptyValidator)
        self.ili_file_line_edit.setValidator(fileValidator)

        self.ili_models_line_edit.textChanged.connect(
            self.validators.validate_line_edits)
        self.ili_models_line_edit.textChanged.emit(
            self.ili_models_line_edit.text())
        self.ili_models_line_edit.textChanged.connect(self.on_model_changed)
        self.ili_models_line_edit.textChanged.connect(self.complete_models_completer)
        self.ili_models_line_edit.punched.connect(self.complete_models_completer)

        self.ilicache = IliCache(self.base_configuration)
        self.refresh_ili_cache()
        self.ili_models_line_edit.setPlaceholderText(self.tr('[Search model from repository]'))

        self.ili_file_line_edit.textChanged.connect(
            self.validators.validate_line_edits)
        self.ili_file_line_edit.textChanged.connect(self.ili_file_changed)
        self.ili_file_line_edit.textChanged.emit(
            self.ili_file_line_edit.text())

    def accepted(self):
        configuration = self.updated_configuration()

        ili_mode = self.type_combo_box.currentData()
        db_id = ili_mode & ~DbIliMode.ili
        interlis_mode = ili_mode & DbIliMode.ili

        if interlis_mode:
            if not self.ili_file_line_edit.text().strip():
                if not self.ili_models_line_edit.text().strip():
                    self.txtStdout.setText(
                        self.tr('Please set a valid INTERLIS model before creating the project.'))
                    self.ili_models_line_edit.setFocus()
                    return

            if self.ili_file_line_edit.text().strip() and \
                    self.ili_file_line_edit.validator().validate(configuration.ilifile, 0)[0] != QValidator.Acceptable:
                self.txtStdout.setText(
                    self.tr('Please set a valid INTERLIS file before creating the project.'))
                self.ili_file_line_edit.setFocus()
                return

        res, message = self._lst_panel[db_id].is_valid()

        if not res:
            self.txtStdout.setText(message)
            return

        configuration.dbschema = configuration.dbschema or configuration.database
        self.save_configuration(configuration)

        # create schema with superuser
        db_factory = self.db_simple_factory.create_factory(db_id)
        res, message = db_factory.pre_generate_project(configuration)

        if not res:
            self.txtStdout.setText(message)
            return

        with OverrideCursor(Qt.WaitCursor):
            self.progress_bar.show()
            self.progress_bar.setValue(0)

            self.disable()
            self.txtStdout.setTextColor(QColor('#000000'))
            self.txtStdout.clear()

            if interlis_mode:
                importer = iliimporter.Importer()

                importer.tool = self.type_combo_box.currentData()
                importer.configuration = configuration
                importer.stdout.connect(self.print_info)
                importer.stderr.connect(self.on_stderr)
                importer.process_started.connect(self.on_process_started)
                importer.process_finished.connect(self.on_process_finished)

                try:
                    if importer.run() != iliimporter.Importer.SUCCESS:
                        self.enable()
                        self.progress_bar.hide()
                        return
                except JavaNotFoundError as e:
                    self.txtStdout.setTextColor(QColor('#000000'))
                    self.txtStdout.clear()
                    self.txtStdout.setText(e.error_string)
                    self.enable()
                    self.progress_bar.hide()
                    return

            try:
                config_manager = db_factory.get_db_command_config_manager(configuration)
                uri = config_manager.get_uri()
                generator = Generator(configuration.tool, uri,
                                      configuration.inheritance, configuration.dbschema)
                generator.stdout.connect(self.print_info)
                generator.new_message.connect(self.show_message)
                self.progress_bar.setValue(50)
            except DBConnectorError:
                self.txtStdout.setText(
                    self.tr('There was an error connecting to the database. Check connection parameters.'))
                self.enable()
                self.progress_bar.hide()
                return

            if not interlis_mode:
                if not generator.db_or_schema_exists():
                    self.txtStdout.setText(
                        self.tr('Source {} does not exist. Check connection parameters.').format(
                            db_factory.get_specific_messages()['db_or_schema']
                        ))
                    self.enable()
                    self.progress_bar.hide()
                    return

            res, message = db_factory.post_generate_project_validations(configuration)

            if not res:
                self.txtStdout.setText(message)
                self.enable()
                self.progress_bar.hide()
                return

            self.print_info(
                self.tr('\nObtaining available layers from the database…'))
            available_layers = generator.layers()

            if not available_layers:
                text = self.tr('The {} has no layers to load into QGIS.').format(
                            db_factory.get_specific_messages()['layers_source'])

                self.txtStdout.setText(text)
                self.enable()
                self.progress_bar.hide()
                return

            self.progress_bar.setValue(70)
            self.print_info(
                self.tr('Obtaining relations from the database…'))
            relations, bags_of_enum = generator.relations(available_layers)
            self.progress_bar.setValue(75)
            self.print_info(self.tr('Arranging layers into groups…'))
            legend = generator.legend(available_layers)
            self.progress_bar.setValue(85)

            project = Project()
            project.layers = available_layers
            project.relations = relations
            project.bags_of_enum = bags_of_enum
            project.legend = legend
            self.print_info(self.tr('Configuring forms and widgets…'))
            project.post_generate()
            self.progress_bar.setValue(90)

            qgis_project = QgsProject.instance()

            self.print_info(self.tr('Generating QGIS project…'))
            project.create(None, qgis_project)

            # Set the extent of the mapCanvas from the first layer extent found
            for layer in project.layers:
                if layer.extent is not None:
                    self.iface.mapCanvas().setExtent(layer.extent)
                    self.iface.mapCanvas().refresh()
                    break

            self.buttonBox.clear()
            self.buttonBox.setEnabled(True)
            self.buttonBox.addButton(QDialogButtonBox.Close)
            self.progress_bar.setValue(100)
            self.print_info(self.tr('\nDone!'), '#004905')

    def print_info(self, text, text_color='#000000'):
        self.txtStdout.setTextColor(QColor(text_color))
        self.txtStdout.append(text)
        QCoreApplication.processEvents()

    def on_stderr(self, text):
        color_log_text(text, self.txtStdout)
        self.advance_progress_bar_by_text(text)
        QCoreApplication.processEvents()

    def on_process_started(self, command):
        self.txtStdout.setText(command)
        self.progress_bar.setValue(10)
        QCoreApplication.processEvents()

    def on_process_finished(self, exit_code, result):
        if exit_code == 0:
            color = '#004905'
            message = self.tr(
                'Interlis model(s) successfully imported into the database!')
        else:
            color = '#aa2222'
            message = self.tr('Finished with errors!')

        self.txtStdout.setTextColor(QColor(color))
        self.txtStdout.append(message)
        self.progress_bar.setValue(50)

    def updated_configuration(self):
        """
        Get the configuration that is updated with the user configuration changes on the dialog.
        :return: Configuration
        """
        configuration = SchemaImportConfiguration()

        mode = self.type_combo_box.currentData()
        db_id = mode & ~DbIliMode.ili

        self._lst_panel[db_id].get_fields(configuration)

        configuration.tool = mode
        configuration.epsg = self.epsg
        configuration.inheritance = self.ili2db_options.inheritance_type()
        configuration.tomlfile = self.ili2db_options.toml_file()
        configuration.create_basket_col = self.ili2db_options.create_basket_col()
        configuration.create_import_tid = self.ili2db_options.create_import_tid()
        configuration.stroke_arcs = self.ili2db_options.stroke_arcs()

        configuration.base_configuration = self.base_configuration
        if self.ili_file_line_edit.text().strip():
            configuration.ilifile = self.ili_file_line_edit.text().strip()

        if self.ili_models_line_edit.text().strip():
            configuration.ilimodels = self.ili_models_line_edit.text().strip()

        return configuration

    def save_configuration(self, configuration):
        settings = QSettings()
        settings.setValue('QgisModelBaker/ili2db/ilifile',
                          configuration.ilifile)
        settings.setValue('QgisModelBaker/ili2db/epsg', self.epsg)
        settings.setValue('QgisModelBaker/importtype',
                          self.type_combo_box.currentData().name)

        mode = self.type_combo_box.currentData()
        db_factory = self.db_simple_factory.create_factory(mode)
        config_manager = db_factory.get_db_command_config_manager(configuration)
        config_manager.save_config_in_qsettings()

    def restore_configuration(self):
        settings = QSettings()

        self.ili_file_line_edit.setText(
            settings.value('QgisModelBaker/ili2db/ilifile'))
        self.crs = QgsCoordinateReferenceSystem(
            settings.value('QgisModelBaker/ili2db/epsg', 21781, int))
        self.fill_toml_file_info_label()
        self.update_crs_info()

        for db_id in self.db_simple_factory.get_db_list(False):
            configuration = SchemaImportConfiguration()
            db_factory = self.db_simple_factory.create_factory(db_id)
            config_manager = db_factory.get_db_command_config_manager(configuration)
            config_manager.load_config_from_qsettings()
            self._lst_panel[db_id].set_fields(configuration)

        mode = settings.value('QgisModelBaker/importtype')
        mode = DbIliMode[mode] if mode else self.db_simple_factory.default_database

        self.type_combo_box.setCurrentIndex(self.type_combo_box.findData(mode))
        self.type_changed()
        self.crs_changed()

    def disable(self):
        self.type_combo_box.setEnabled(False)
        for key, value in self._lst_panel.items():
            value.setEnabled(False)
        self.ili_config.setEnabled(False)
        self.buttonBox.setEnabled(False)

    def enable(self):
        self.type_combo_box.setEnabled(True)
        for key, value in self._lst_panel.items():
            value.setEnabled(True)
        self.ili_config.setEnabled(True)
        self.buttonBox.setEnabled(True)

    def type_changed(self):
        self.txtStdout.clear()
        self.progress_bar.hide()

        ili_mode = self.type_combo_box.currentData()
        db_id = ili_mode & ~DbIliMode.ili
        interlis_mode = bool(ili_mode & DbIliMode.ili)

        self.ili_config.setVisible(interlis_mode)
        self.db_wrapper_group_box.setTitle(displayDbIliMode[db_id])

        # Refresh panels
        for key, value in self._lst_panel.items():
            value.interlis_mode = interlis_mode
            is_current_panel_selected = db_id == key
            value.setVisible(is_current_panel_selected)
            if is_current_panel_selected:
                value._show_panel()

    def on_model_changed(self, text):
        if not text:
            return
        for pattern, crs in CRS_PATTERNS.items():
            if re.search(pattern, text):
                self.crs = QgsCoordinateReferenceSystem(crs)
                self.update_crs_info()
                break
        self.ili2db_options.set_toml_file_key(text)
        self.fill_toml_file_info_label()

    def link_activated(self, link):
        if link.url() == '#configure':
            cfg = OptionsDialog(self.base_configuration)
            if cfg.exec_():
                settings = QSettings()
                settings.beginGroup('QgisModelBaker/ili2db')
                self.base_configuration.save(settings)
        else:
            QDesktopServices.openUrl(link)

    def update_crs_info(self):
        self.crsSelector.setCrs(self.crs)

    def crs_changed(self):
        if self.crsSelector.crs().authid()[:5] != 'EPSG:':
            self.crs_label.setStyleSheet('color: orange')
            self.crs_label.setToolTip(
                self.tr('Please select an EPSG Coordinate Reference System'))
            self.epsg = 21781
        else:
            self.crs_label.setStyleSheet('')
            self.crs_label.setToolTip(self.tr('Coordinate Reference System'))
            authid = self.crsSelector.crs().authid()
            self.epsg = int(authid[5:])

    def ili_file_changed(self):
        # If ili file is valid, models is optional
        if self.ili_file_line_edit.text().strip() and \
                self.ili_file_line_edit.validator().validate(self.ili_file_line_edit.text().strip(), 0)[0] == QValidator.Acceptable:
            self.ili_models_line_edit.setValidator(None)
            self.ili_models_line_edit.textChanged.emit(
                self.ili_models_line_edit.text())

            # Update completer to add models from given ili file
            self.ilicache = IliCache(None, self.ili_file_line_edit.text().strip())
            self.refresh_ili_cache()
            models = self.ilicache.process_ili_file(self.ili_file_line_edit.text().strip())
            self.ili_models_line_edit.setText(models[-1]['name'])
            self.ili_models_line_edit.setPlaceholderText(models[-1]['name'])
        else:
            nonEmptyValidator = NonEmptyStringValidator()
            self.ili_models_line_edit.setValidator(nonEmptyValidator)
            self.ili_models_line_edit.textChanged.emit(
                self.ili_models_line_edit.text())

            # Update completer to add models from given ili file
            self.ilicache = IliCache(self.base_configuration)
            self.refresh_ili_cache()
            self.ili_models_line_edit.setPlaceholderText(self.tr('[Search model from repository]'))

    def refresh_ili_cache(self):
        self.ilicache.new_message.connect(self.show_message)
        self.ilicache.refresh()
        self.update_models_completer()

    def complete_models_completer(self):
        if not self.ili_models_line_edit.text():
            self.ili_models_line_edit.completer().setCompletionMode(QCompleter.UnfilteredPopupCompletion)
            self.ili_models_line_edit.completer().complete()
        else:
            self.ili_models_line_edit.completer().setCompletionMode(QCompleter.PopupCompletion)

    def update_models_completer(self):
        completer = QCompleter(self.ilicache.model, self.ili_models_line_edit)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        self.delegate = ModelCompleterDelegate()
        completer.popup().setItemDelegate(self.delegate)
        self.ili_models_line_edit.setCompleter(completer)
        self.multiple_models_dialog.models_line_edit.setCompleter(completer)

    def show_message(self, level, message):
        if level == Qgis.Warning:
            self.bar.pushMessage(message, Qgis.Info, 10)
        elif level == Qgis.Critical:
            self.bar.pushMessage(message, Qgis.Warning, 10)

    def fill_models_line_edit(self):
        self.ili_models_line_edit.setText(
            self.multiple_models_dialog.get_models_string())

    def fill_toml_file_info_label(self):
        text = None
        if self.ili2db_options.toml_file():
            text = self.tr('Extra Model Information File: {}').format(('…'+self.ili2db_options.toml_file()[len(self.ili2db_options.toml_file())-40:]) if len(self.ili2db_options.toml_file()) > 40 else self.ili2db_options.toml_file())
        self.toml_file_info_label.setText(text)
        self.toml_file_info_label.setToolTip(self.ili2db_options.toml_file())

    def help_requested(self):
        os_language = QLocale(QSettings().value(
            'locale/userLocale')).name()[:2]
        if os_language in ['es', 'de']:
            webbrowser.open(
                "https://opengisch.github.io/QgisModelBaker/docs/{}/user-guide.html#generate-project".format(os_language))
        else:
            webbrowser.open(
                "https://opengisch.github.io/QgisModelBaker/docs/user-guide.html#generate-project")

    def advance_progress_bar_by_text(self, text):
        if text.strip() == 'Info: compile models…':
            self.progress_bar.setValue(20)
        elif text.strip() == 'Info: create table structure…':
            self.progress_bar.setValue(30)

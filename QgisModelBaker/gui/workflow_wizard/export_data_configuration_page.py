# -*- coding: utf-8 -*-
"""
/***************************************************************************
                              -------------------
        begin                : 06.07.2021
        git sha              : :%H$
        copyright            : (C) 2021 by Dave Signer
        email                : david at opengis ch
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


from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QValidator
from qgis.PyQt.QtWidgets import QWizardPage

import QgisModelBaker.gui.workflow_wizard.wizard_tools as wizard_tools
from QgisModelBaker.utils.qt_utils import (
    FileValidator,
    Validators,
    make_save_file_selector,
)

from ...utils import ui

PAGE_UI = ui.get_ui_class("workflow_wizard/export_data_configuration.ui")


class ExportDataConfigurationPage(QWizardPage, PAGE_UI):
    ValidExtensions = wizard_tools.TransferExtensions

    def __init__(self, parent, title):
        QWizardPage.__init__(self, parent)
        self.workflow_wizard = parent

        self.setupUi(self)
        self.setTitle(title)
        self.is_complete = False

        self.xtf_file_browse_button.clicked.connect(
            make_save_file_selector(
                self.xtf_file_line_edit,
                title=self.tr("Save in XTF Transfer File"),
                file_filter=self.tr(
                    "XTF Transfer File (*.xtf *XTF);;Interlis 1 Transfer File (*.itf *ITF);;XML (*.xml *XML);;GML (*.gml *GML)"
                ),
                extension=".xtf",
                extensions=["." + ext for ext in self.ValidExtensions],
            )
        )

        self.validators = Validators()

        fileValidator = FileValidator(
            pattern=["*." + ext for ext in self.ValidExtensions],
            allow_non_existing=True,
        )

        self.xtf_file_line_edit.setValidator(fileValidator)
        self.xtf_file_line_edit.textChanged.connect(self.validators.validate_line_edits)
        self.xtf_file_line_edit.textChanged.connect(self._set_current_export_target)
        self.xtf_file_line_edit.textChanged.emit(self.xtf_file_line_edit.text())

        self.select_all_checkbox.stateChanged.connect(self._select_all_items)
        self.filter_combobox.currentIndexChanged.connect(self._filter_changed)

    def isComplete(self):
        return self.is_complete

    def setComplete(self, complete):
        if self.is_complete != complete:
            self.is_complete = complete
            self.completeChanged.emit()

    def nextId(self):
        return self.workflow_wizard.next_id()

    def setup_dialog(self, basket_handling):
        # disconnect currentIndexChanged signal while refreshing the combobox
        try:
            self.filter_combobox.currentIndexChanged.disconnect()
        except Exception:
            pass

        self._refresh_filter_combobox(basket_handling)

        self.filter_combobox.currentIndexChanged.connect(self._filter_changed)

    def _refresh_filter_combobox(self, basket_handling):
        stored_index = self.filter_combobox.findData(
            self.workflow_wizard.current_export_filter
        )
        self.filter_combobox.clear()
        self.filter_combobox.addItem(
            self.tr("No filter (export all models)"),
            wizard_tools.ExportFilterMode.NO_FILTER,
        )
        self.filter_combobox.addItem(
            self.tr("Models"), wizard_tools.ExportFilterMode.MODEL
        )
        if basket_handling:
            self.filter_combobox.addItem(
                self.tr("Datasets"), wizard_tools.ExportFilterMode.DATASET
            )
            self.filter_combobox.addItem(
                self.tr("Baskets"), wizard_tools.ExportFilterMode.BASKET
            )
        if self.filter_combobox.itemData(stored_index):
            self.filter_combobox.setCurrentIndex(stored_index)
            if (
                self.filter_combobox.itemData(stored_index)
                != wizard_tools.ExportFilterMode.NO_FILTER
            ):
                self._set_select_all_checkbox()
        else:
            self.filter_combobox.setCurrentIndex(0)
            self._filter_changed()

    def _set_export_filter_view_model(self, model):
        try:
            self.export_items_view.clicked.disconnect()
            self.export_items_view.space_pressed.disconnect()
            self.export_items_view.model().dataChanged.disconnect()
        except Exception:
            pass

        self.export_items_view.setModel(model)
        self.export_items_view.clicked.connect(self.export_items_view.model().check)
        self.export_items_view.space_pressed.connect(
            self.export_items_view.model().check
        )
        self.export_items_view.model().dataChanged.connect(
            lambda: self._set_select_all_checkbox()
        )

    def _filter_changed(self):
        filter = self.filter_combobox.currentData()
        if filter == wizard_tools.ExportFilterMode.NO_FILTER:
            self.export_items_view.setHidden(True)
            self.select_all_checkbox.setHidden(True)
        else:
            self.export_items_view.setVisible(True)
            self.select_all_checkbox.setVisible(True)
            if filter == wizard_tools.ExportFilterMode.MODEL:
                self._set_export_filter_view_model(
                    self.workflow_wizard.export_models_model
                )
                self.select_all_checkbox.setText(self.tr("Select all models"))
            if filter == wizard_tools.ExportFilterMode.DATASET:
                self._set_export_filter_view_model(
                    self.workflow_wizard.export_datasets_model
                )
                self.select_all_checkbox.setText(self.tr("Select all datasets"))
            if filter == wizard_tools.ExportFilterMode.BASKET:
                self._set_export_filter_view_model(
                    self.workflow_wizard.export_baskets_model
                )
                self.select_all_checkbox.setText(self.tr("Select all baskets"))
            self._set_select_all_checkbox()
        self.workflow_wizard.current_export_filter = filter

    def _select_all_items(self, state):
        if state != Qt.PartiallyChecked and state != self._evaluated_check_state(
            self.export_items_view.model()
        ):
            self.export_items_view.model().check_all(state)

    def _set_select_all_checkbox(self):
        self.select_all_checkbox.setCheckState(
            self._evaluated_check_state(self.export_items_view.model())
        )

    def _evaluated_check_state(self, model):
        nbr_of_checked = len(model.checked_entries())
        if nbr_of_checked:
            if nbr_of_checked == model.rowCount():
                return Qt.Checked
            return Qt.PartiallyChecked
        return Qt.Unchecked

    def _set_current_export_target(self, text):
        self.setComplete(
            self.xtf_file_line_edit.validator().validate(text, 0)[0]
            == QValidator.Acceptable
        )
        self.workflow_wizard.current_export_target = text

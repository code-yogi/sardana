#!/usr/bin/env python

##############################################################################
##
# This file is part of Sardana
##
# http://www.sardana-controls.org/
##
# Copyright 2011 CELLS / ALBA Synchrotron, Bellaterra, Spain
##
# Sardana is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
##
# Sardana is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
##
# You should have received a copy of the GNU Lesser General Public License
# along with Sardana.  If not, see <http://www.gnu.org/licenses/>.
##
##############################################################################

"""
This module provides a button for executing macros
"""

__all__ = ['MacroButton']

import functools
import shlex
import re

import PyTango

import taurus
from taurus.core import TaurusEventType, TaurusDevice
from taurus.external.qt import Qt
from taurus.qt.qtgui.container import TaurusWidget
from taurus.qt.qtgui.base import TaurusBaseWidget
from taurus.qt.qtgui.button import TaurusCommandButton
from taurus.qt.qtgui.dialog import ProtectTaurusMessageBox
from taurus.core.util.colors import DEVICE_STATE_PALETTE
from taurus.qt.qtgui.util.ui import UILoadable


class DoorStateListener(Qt.QObject):
    '''A listener of Change and periodic events from a Door State attribute.
    It converts the received Tango events and emits a Qt signal
    '''

    __pyqtSignals__ = ["doorStateChanged"]

    def eventReceived(self, evt_src, evt_type, evt_value):
        if evt_type not in (TaurusEventType.Change, TaurusEventType.Periodic):
            return
        door_state = evt_value.value
        self.emit(Qt.SIGNAL('doorStateChanged'), door_state)


@UILoadable(with_ui='ui')
class MacroButton(TaurusWidget):
    ''' A button to execute/pause/stop macros. The model must be a valid door.

    .. todo:: Not implemented but will be needed: set an icon

    .. todo:: It may be useful to have all the streams from qdoor available
             somehow (right-click?)
    '''

    __pyqtSignals__ = ['statusUpdated', 'resultUpdated']

    def __init__(self, parent=None, designMode=False):
        TaurusWidget.__init__(self, parent, designMode)
        self.loadUi()
        self.door = None
        self.door_state_listener = None
        self.macro_name = ''
        self.macro_args = []
        self.macro_id = None
        self.running_macro = None

        self.ui.progress.setValue(0)

        self.ui.button.setCheckable(True)
        self.connect(self.ui.button, Qt.SIGNAL('clicked()'),
                     self._onButtonClicked)

    def toggleProgress(self, visible):
        '''deprecated'''
        self.warning('toggleProgress is deprecated. Use showProgress')
        self.showProgress(visible)

    def showProgress(self, visible):
        '''Set whether the progress bar is shown

        :param visible: (bool) If True, the progress bar is shown. Otherwise it
                        is hidden'''
        self.ui.progress.setVisible(visible)

    def setModel(self, model):
        '''
        reimplemented from :class:`TaurusWidget`. A door device name is
        expected as the model
        '''
        TaurusWidget.setModel(self, model)
        if self.door is not None:
            self.disconnect(self.door, Qt.SIGNAL(
                'macroStatusUpdated'), self._statusUpdated)
            self.disconnect(self.door, Qt.SIGNAL(
                'resultUpdated'), self._resultUpdated)

            # disable management of Door Tango States
            self.door.getAttribute('State').removeListener(
                self.door_state_listener)
            self.disconnect(self.door_state_listener, Qt.SIGNAL(
                'doorStateChanged'), self._doorStateChanged)
            self.door_state_listener = None

        try:
            self.door = taurus.Device(model)
        except:
            return

        self.connect(self.door, Qt.SIGNAL(
            'macroStatusUpdated'), self._statusUpdated)
        self.connect(self.door, Qt.SIGNAL(
            'resultUpdated'), self._resultUpdated)

        # Manage Door Tango States
        self.door_state_listener = DoorStateListener()
        self.connect(self.door_state_listener, Qt.SIGNAL(
            'doorStateChanged'), self._doorStateChanged)
        self.door.getAttribute('State').addListener(self.door_state_listener)

    def _doorStateChanged(self, state):
        '''slot called on door state changes'''
        color = '#' + DEVICE_STATE_PALETTE.hex(state)
        stylesheet = 'QFrame{border: 4px solid %s;}' % color
        self.ui.frame.setStyleSheet(stylesheet)

        # In case state is not ON, and macro not triggered by the button,
        # disable it
        door_available = True
        if state not in [PyTango.DevState.ON, PyTango.DevState.ALARM] and not self.ui.button.isChecked():
            door_available = False

        self.ui.button.setEnabled(door_available)
        self.ui.progress.setEnabled(door_available)

    def _statusUpdated(self, *args):
        '''slot called on status changes'''
        # SHOULD SEE THE DOCUMENTATION ABOUT THE ARGS AND ALSO THE STATUS STATE MACHINE
        # ARGS FORMAT IS (GUESSING WITH PRINT STATEMENTS)
        # e.g. ((<sardana.taurus.core.tango.sardana.macro.Macro object at 0x7f29300bc210>, [{u'step': 100.0, u'state': u'stop', u'range': [0.0, 100.0], u'id': u'b226f5e8-c807-11e0-8abe-001d0969db5b'}]),)
        # ( (MacroObj, [status_dict, .?.]), .?.)

        # QUESTIONS: THIS MACRO OBJECT HAS ALOS STEP, RANGE, ...
        # AND ALSO THE STATUS DICT... WHICH SHOULD I USE?

        first_tuple = args[0]
        self.running_macro = first_tuple[0]

        status_dict = first_tuple[1][0]
        # KEYS RECEIVED FROM A 'SCAN' MACRO AND A 'TWICE' MACRO: IS IT GENERAL
        # ?!?!?!
        macro_id = status_dict['id']
        # if macro id is unknown ignoring this signal
        if macro_id is None:
            return
        # check if we have launch this macro, otherwise ignore the signal
        if macro_id != str(self.macro_id):
            return
        state = status_dict['state']
        step = status_dict['step']
        step_range = status_dict['range']

        # Update progress bar
        self.ui.progress.setMinimum(step_range[0])
        self.ui.progress.setMaximum(step_range[1])
        self.ui.progress.setValue(step)

        if state in ['stop', 'abort', 'finish', 'alarm']:
            self.ui.button.setChecked(False)

        self.emit(Qt.SIGNAL('statusUpdated'), status_dict)

    def _resultUpdated(self, *args):
        '''slot called on result changes'''
        # ARGS APPEAR TO BE EMPTY... SHOULD THEY CONTAIN THE RESULT ?!?!?!
        # I have to rely on the 'macro object' received in the last status
        # update
        if self.running_macro is None:
            return
        result = self.running_macro.getResult()
        self.emit(Qt.SIGNAL('resultUpdated'), result)

    def setText(self, text):
        '''set the button text

        :param text: (str) text for the button
        '''
        self.setButtonText(text)

    def setButtonText(self, text):
        '''same as :meth:`setText`
        '''
        # SHOULD ALSO BE POSSIBLE TO SET AN ICON
        self.ui.button.setText(text)

    def setMacroName(self, name):
        '''set the name of the macro to be executed

        :param name: (str) text for the button
        '''
        self.macro_name = str(name)
        # update tooltip
        self.setToolTip(self.macro_name + ' ' + ' '.join(self.macro_args))

    def updateMacroArgument(self, index, value):
        '''change a given argument

        :param index: (int) positional index for this argument
        :param value: (str) value for this argument
        '''
        # make sure that the macro_args is at least as long as index
        while len(self.macro_args) < index + 1:
            self.macro_args.append('')
        # update the given argument
        if re.search('\s', value):
            value = '"{0}"'.format(value)
        self.macro_args[index] = str(value)
        # update tooltip
        self.setToolTip(self.macro_name + ' ' + ' '.join(self.macro_args))

    def updateMacroArgumentFromSignal(self, index, obj, signal):
        '''deprecated'''
        msg = 'updateMacroArgumentFromSignal is deprecated. connectArgEditors'
        self.warning(msg)
        self.connect(obj, signal,
                     functools.partial(self.updateMacroArgument, index))

    def connectArgEditors(self, signals):
        '''Associate signals to argument changes.

        :param signals: (seq<tuple>) An ordered sequence of (`obj`, `sig`)
                        tuples , where `obj` is a parameter editor object and
                        `sig` is a signature for a signal emitted by `obj` which
                        provides the value of a parameter as its argument.
                        Each (`obj`, `sig`) tuple is associated to parameter
                        corresponding to its position in the `signals` sequence.
                        '''

        for i, (obj, sig) in enumerate(signals):
            self.connect(obj, Qt.SIGNAL(sig),
                         functools.partial(self.updateMacroArgument, i))

    def _onButtonClicked(self):
        if self.ui.button.isChecked():
            self.runMacro()
        else:
            self.abort()

    @ProtectTaurusMessageBox(msg='Error while executing the macro.')
    def runMacro(self):
        '''execute the macro with the current arguments'''
        if self.door is None:
            return

        # TODO: make macrobutton compatible with macros with advanced usage of
        # repeat parameters e.g. multiple repeat parameters, nested repeat
        # parameters, etc.
        macro_args = shlex.split(' '.join(self.macro_args))
        try:
            self.door.runMacro(self.macro_name, macro_args)
            sec_xml = self.door.getRunningXML()
            # get the id of the current running macro
            self.macro_id = sec_xml[0].get("id")
        except Exception, e:
            self.ui.button.setChecked(False)
            raise e

    def abort(self):
        '''abort the macro.'''
        if self.door is None:
            return
        self.door.PauseMacro()
        # Since this could be done by error (impatient users clicking more than once)
        # we provide a warning message that does not make the process too slow
        # It may also be useful and 'ABORT' at TaurusApplication level
        # (macros+motions+acquisitions)
        title = 'Aborting macro'
        message = 'The following macro is still running:\n\n'
        message += '%s %s\n\n' % (self.macro_name, ' '.join(self.macro_args))
        message += 'Are you sure you want to abort?\n'
        buttons = Qt.QMessageBox.Ok | Qt.QMessageBox.Cancel
        ans = Qt.QMessageBox.warning(
            self, title, message, buttons, Qt.QMessageBox.Ok)
        if ans == Qt.QMessageBox.Ok:
            self.door.abort(synch=True)
        else:
            self.ui.button.setChecked(True)
            self.door.ResumeMacro()

    @classmethod
    def getQtDesignerPluginInfo(cls):
        '''reimplemented from :class:`TaurusWidget`'''
        return {'container': False,
                'group': 'Taurus Sardana',
                'module': 'taurus.qt.qtgui.extra_macroexecutor',
                'icon': ':/designer/pushbutton.png'}


class MacroButtonAbortDoor(Qt.QPushButton, TaurusBaseWidget):
    '''Deprecated class. Instead use TaurusCommandButton.
    A button for aborting macros on a door
    '''
    # todo: why not inheriting from (TaurusBaseComponent, Qt.QPushButton)?

    def __init__(self, parent=None, designMode=False):
        name = self.__class__.__name__
        self.call__init__wo_kw(Qt.QPushButton, parent)
        self.call__init__(TaurusBaseWidget, name, designMode=designMode)
        self.warning('Deprecation warning: use TaurusCommandButton class ' +
                     'instead of MacroButtonAbortDoor')

        self.setText('Abort')
        self.setToolTip('Abort Macro')
        self.connect(self, Qt.SIGNAL('clicked()'), self.abort)

    def getModelClass(self):
        '''reimplemented from :class:`TaurusBaseWidget`'''
        return TaurusDevice

    @ProtectTaurusMessageBox(msg='An error occurred trying to abort the macro.')
    def abort(self):
        '''stops macros'''
        door = self.getModelObj()
        if door is not None:
            door.stopMacro()


if __name__ == '__main__':
    import sys
    from taurus.qt.qtgui.application import TaurusApplication
    from taurus.core.util.argparse import get_taurus_parser
    from sardana.taurus.qt.qtcore.tango.sardana.macroserver import \
        registerExtensions
    registerExtensions()

    parser = get_taurus_parser()
    parser.set_usage("python macrobutton.py [door_name] [macro_name]")
    parser.set_description("Macro button for macro execution")

    app = TaurusApplication(app_name="macrobutton",
                            app_version=taurus.Release.version)

    args = app.get_command_line_args()

    nargs = len(args)

    if nargs == 1:
        macro_name = 'lsmac'
    elif nargs == 2:
        macro_name = args[1]
    else:
        parser.print_help(sys.stderr)
        sys.exit(1)
    door_name = args[0]

    class TestWidget(Qt.QWidget):

        def __init__(self, door, macro):
            Qt.QWidget.__init__(self)

            self.door_name = door
            self.macro_name = macro
            self.w_macro_name = None
            self.create_layout(macro)

        def update_macro_name(self, macro_name):
            self.macro_name = macro_name

        def update_result(self, result):
            self.result_label.setText(str(result))

        def toggle_progress(self, toggle):
            visible = self.show_progress.isChecked()
            self.mb.toggleProgress(visible or toggle)

        def getMacroInfo(self, macro_name):

            door = taurus.Device(self.door_name)
            try:
                pars = door.macro_server.getMacroInfoObj(macro_name).parameters
            except AttributeError as e:
                print "Macro %s does not exists!" % macro_name
                return None

            param_names = []
            default_values = []
            for p in pars:
                ptype = p['type']

                if isinstance(ptype, list):
                    for pr in ptype:
                        param_names.append(pr['name'])
                        default_values.append(pr['default_value'])
                else:
                    param_names.append(p['name'])
                    default_values.append(p['default_value'])

            self.macro_name = macro_name
            return param_names, default_values

        def clean_layout(self, layout):

            while layout.count():
                child = layout.takeAt(0)
                if child.widget():
                    child.widget().deleteLater()

        def create_layout(self, macro_name):
            p_names, d_values = self.getMacroInfo(macro_name)
            # Create TOP layout
            self.w_arg = Qt.QWidget()
            self.w_arg.setLayout(Qt.QGridLayout())
            col = 0
            self.w_arg.layout().addWidget(Qt.QLabel('macro name'), 0, col)
            self.w_macro_name = Qt.QLineEdit()
            self.w_arg.layout().addWidget(self.w_macro_name, 1, col)

            _argEditors = []
            for name in p_names:
                col += 1
                self.w_arg.layout().addWidget(Qt.QLabel(name), 0, col)
                self.argEdit = Qt.QLineEdit()
                self.w_arg.layout().addWidget(self.argEdit, 1, col)
                _argEditors.append(self.argEdit)

            for e, v in zip(_argEditors, d_values):
                e.setText(v)

            # Create bottom layout
            self.mb = MacroButton()
            self.mb.setModel(door_name)
            self.w_bottom = Qt.QWidget()
            self.w_bottom.setLayout(Qt.QGridLayout())
            self.w_bottom.layout().addWidget(self.mb, 0, 0, 2, 7)
            self.w_bottom.layout().addWidget(Qt.QLabel('Result:'), 2, 0)

            self.result_label = Qt.QLabel()
            self.w_bottom.layout().addWidget(self.result_label, 2, 1, 1, 5)

            self.show_progress = Qt.QCheckBox('Progress')
            self.show_progress.setChecked(True)
            self.w_bottom.layout().addWidget(self.show_progress, 3, 0)

            mb_abort = TaurusCommandButton(command='StopMacro',
                                           icon=':/actions/media_playback_stop.svg')
            mb_abort.setModel(door_name)
            self.w_bottom.layout().addWidget(mb_abort, 3, 1)

            # Toggle progressbar
            Qt.QObject.connect(self.show_progress, Qt.SIGNAL(
                'stateChanged(int)'), self.toggle_progress)
            # connect the argument editors
            signals = [(e, 'textChanged(QString)') for e in _argEditors]
            self.mb.connectArgEditors(signals)

            self.setLayout(Qt.QVBoxLayout())
            self.layout().addWidget(self.w_arg)
            self.layout().addWidget(self.w_bottom)

            # Update possible macro result
            Qt.QObject.connect(self.mb, Qt.SIGNAL(
                'resultUpdated'), self.update_result)

            Qt.QObject.connect(self.w_macro_name, Qt.SIGNAL(
                'textEdited(QString)'), self.update_macro_name)
            Qt.QObject.connect(self.w_macro_name, Qt.SIGNAL(
                'editingFinished()'), self.update_layout)
            Qt.QObject.connect(self.w_macro_name, Qt.SIGNAL(
                'textChanged(QString)'), self.mb.setMacroName)
            Qt.QObject.connect(self.w_macro_name, Qt.SIGNAL(
                'textChanged(QString)'), self.mb.setButtonText)

            # Since everything is now connected, the parameters will be updated
            self.w_macro_name.setText(macro_name)

        def update_layout(self):
            if self.getMacroInfo(self.macro_name):
                self.clean_layout(self.layout())
                self.create_layout(self.macro_name)

    w = TestWidget(door_name, macro_name)
    w.show()
    sys.exit(app.exec_())

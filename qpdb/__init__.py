"""
@file qpdb - main script
@author Valentin Schmidt
"""

import ast
import json
import os
import re
import sys
import tokenize
import traceback
import intervaltree
from PyQt5.QtCore import Qt, QResource, QProcess, QProcessEnvironment, QSettings, QFile
from PyQt5.QtGui import QColor, QGuiApplication, QFont, QIcon, QKeySequence
from PyQt5.QtWidgets import (QApplication, qApp, QMainWindow, QLabel, QComboBox, QMessageBox,
    QLineEdit, QSplitter, QHBoxLayout, QWidget, QSizePolicy, QFileDialog,
    QListWidgetItem, QTreeWidgetItem, QAction, QAbstractItemView)
from PyQt5 import uic
from PyQt5.Qsci import QsciScintilla, QsciLexerPython, QsciAPIs
if os.name == 'nt':
    import ctypes
    user32 = ctypes.windll.user32

PATH = os.path.dirname(os.path.realpath(__file__))

# ######################################
# config
# ######################################

# Note: CHM support in macos requires xchm, which you can install with MacPorts:
# port install xchm
CHM_FILES = [
    PATH + '/resources/python374.chm'
]

ASSISTANT_BIN = None
# ASSISTANT_BIN = 'C:\\dev\\qt5\\5.15.2\\msvc2019_64\\bin\\assistant.exe'
# ASSISTANT_BIN = '/Users/fluxus/Qt/5.15.0/clang_64/bin/Assistant.app/Contents/MacOS/Assistant'

API_FILE = PATH + '/resources/prepared.api'

# editor settings
if os.name == 'nt':
    DEFAULT_FONT = QFont('Consolas', 10)
    LINENO_FONT = QFont('Consolas', 8)
else:
    DEFAULT_FONT = QFont('Menlo', 12)
    LINENO_FONT = QFont('Menlo', 10)
MARGIN_COLOR = QColor('#666666')
MARGIN_BGCOLOR = QColor('#e0e0e0')
FOLD_MARGIN_COLOR = QColor('#e0e0e0')
FOLD_MARGIN_HICOLOR = QColor('#ffffff')
FOLD_MARGIN_NUM = 2
FOLD_MARGIN_WIDTH = 14
FOLDING = 1
BREAKPOINT_SYMBOL = 0
BREAKPOINT_COLOR = QColor('#ff0000')
ACTIVE_LINE_SYMBOL = 22
ACTIVE_LINE_COLOR = QColor('#ccffcc')

PROC_ENCODING = 'windows-1252' if os.name == 'nt' else 'utf-8'

# ######################################
# /config
# ######################################


class SCI:
    """ Scintilla constants """
    SCI_COLOURISE = 4003
    SCI_SETFOLDMARGINCOLOUR = 2290
    SCI_SETFOLDMARGINHICOLOUR = 2291
    SCI_SETHSCROLLBAR = 2130
    SCI_SETMARGINWIDTHN = 2242
    SCI_SETSTYLING = 2033
    SCI_STARTSTYLING = 2032
    SCI_STYLESETFONT = 2056
    SCI_STYLESETFORE = 2051
    SCI_STYLESETSIZE = 2055
    STYLE_LINENUMBER = 33
    STYLE_STDOUT = 1
    STYLE_STDERR = 2


class Main(QMainWindow):
    """ Main class """

    def __init__(self):
        """ constructor """
        super().__init__()

        QApplication.setStyle('Fusion')
        QResource.registerResource(PATH + '/resources/main.rcc')
        file = QFile(':/style.css')
        file.open(QFile.ReadOnly)
        qApp.setStyleSheet(file.readAll().data().decode())
        uic.loadUi(PATH + '/resources/main.ui', self)

        # setup statusBar
        self.label_info = QLabel(self.statusbar)
        self.label_info.setContentsMargins(0, 0, 10, 0)
        self.statusbar.addPermanentWidget(self.label_info)

        # setup QProcess
        self.__proc = QProcess()
        self.__proc.readyReadStandardOutput.connect(self._slot_stdout)
        self.__proc.readyReadStandardError.connect(self._slot_stderr)
        self.__proc.finished.connect(self._slot_complete)

        if ASSISTANT_BIN:
            self.__proc_assistant = QProcess(self)
            self.__proc_assistant.setProgram(ASSISTANT_BIN)
            self.__proc_assistant.setArguments(['-enableRemoteControl'])

        # defaults
        self.__last_chunk = ''
        self.__running = False
        self.__dbg_running = False
        self.__filename = None
        self.__saved_breakpoints = {}
        self.__encoding = 'UTF-8'
        self.setWindowTitle('unsaved[*] - qpdb')

        # compiled regular expressions
        self.__re_active = re.compile(r'^> (.*)\(([0-9]+)\)')
        self.__re_bp_add = re.compile(r'^Breakpoint ([0-9]+) at (.*):([0-9]+)')
        self.__re_bp_del = re.compile(r'^Deleted breakpoint ([0-9]+) at (.*):([0-9]+)')
        self.__re_stack = re.compile(r'^[ >] (.*)\(([0-9]+)\)([^(]+)\(\)')

        # some editor stuff
        self.__symbol_margin_num = 1
        self.__breakpoint_marker_num = 1
        self.__breakpoint_marker_mask = 2 ** self.__breakpoint_marker_num
        self.__active_line_marker_num = 2

        # setup components
        self._setup_actions()
        self._setup_toolbar()
        self._setup_lists()
        self._setup_console()
        self._setup_editor()
        self._setup_outline()

        # restore window state
        self.__state = QSettings('fx', 'qpdb')
        val = self.__state.value('MainWindow/Geometry')
        if val is not None:
            self.restoreGeometry(val)
        val = self.__state.value('MainWindow/State')
        if val is not None:
            self.restoreState(val)

        for i in range(1, len(sys.argv)):
            self._load_script(sys.argv[i])

        self.show()

    def closeEvent(self, evt):
        """ Qt event handler """
        self.__proc.kill()
        self.__proc.waitForFinished(1000)
        if not self._maybe_save():
            evt.ignore()
            return
        if ASSISTANT_BIN:
            self.__proc_assistant.kill()
            self.__proc_assistant.waitForFinished(1000)
        # save window state
        self.__state.setValue('MainWindow/Geometry', self.saveGeometry())
        self.__state.setValue('MainWindow/State', self.saveState())

    def dragEnterEvent(self, evt):
        """ Qt event handler """
        if evt.mimeData().hasText():
            evt.accept()

    def dropEvent(self, evt):
        """ Qt event handler """
        if not self._maybe_save():
            return
        files = evt.mimeData().text().split('\n')
        for filename in files:
            if filename is not None:
                if os.name == 'posix':
                    self._load_script(filename[7:])
                else:
                    self._load_script(filename[8:])

    @staticmethod
    def _color_to_bgr_int(col):
        return col.red() + (col.green() << 8) + (col.blue() << 16)

    @staticmethod
    def _get_bom(data):
        """ Checks bytes for Byte-Order-Mark (BOM), returns BOM-type as string or False. """
        if len(data) >= 3:
            if data[0] == 239 and data[1] == 187 and data[2] == 191:
                return 'UTF-8'
        if len(data) >= 2:
            if data[0] == 255 and data[1] == 254:
                return 'UTF-16' # LE
            if data[0] == 254 and data[1] == 255:
                return 'UTF-16BE' # BE
        return False

    @staticmethod
    def _is_utf8(data):
        """
        Checks bytes for invalid UTF-8 sequences.
        Notice: since ASCII is a UTF-8 subset, function returns True for pure ASCII data
        @return {bool}
        """
        _len = len(data)
        i = -1
        while True:
            i += 1
            if i >= _len:
                break
            if data[i] < 128:
                continue
            if data[i] & 224 == 192 and data[i] > 193:
                cnt = 1  # 110bbbbb (C0-C1)
            elif data[i] & 240 == 224:
                cnt = 2  # 1110bbbb
            elif data[i] & 248 == 240 and data[i] < 245:
                cnt = 3  # 11110bbb (F5-FF)
            else:
                return False  # invalid UTF-8 sequence
            for _ in range(cnt):
                i += 1
                if i > _len:
                    return False  # invalid UTF-8 sequence
                if data[i] & 192 != 128:
                    return False  # invalid UTF-8 sequence
        return True  # no invalid UTF-8 sequence found

    @staticmethod
    def _get_eol_mode(txt):
        """ Tries to detect the eolMode of the specified string. """
        if '\r' in txt and not '\n' in txt:
            return 1  # EolMac
        if '\n' in txt and not '\r' in txt:
            return 2  # EolUnix
        return 0  # EolWindows

    @staticmethod
    def _get_encoding(data):
        """
        Tries to detect encoding of specified bytes.
        @return {str} 'UTF-8', 'UTF-16', 'UTF-16BE' or 'Windows-1252'
        """
        bom = Main._get_bom(data)
        if bom:
            return bom
        # utf-16be without BOM?
        if len(data) > 0 and data[0] == 0:
            return 'UTF-16BE'
        # utf-16 (le) without BOM?
        if len(data) > 1 and data[1] == 0:
            return 'UTF-16'
        # valid UTF-8?
        if Main._is_utf8(data):
            return 'UTF-8'
        # default: return ANSI (Windows-1252)
        return 'Windows-1252'

    def _setup_actions(self):
        self.actionLoad.triggered.connect(self._slot_load)
        self.actionClose.triggered.connect(self._slot_close)
        self.actionSave.triggered.connect(self._slot_save)
        self.actionSaveAs.triggered.connect(self._slot_save_as)
        self.actionUndo.triggered.connect(self.editor.undo)
        self.actionRedo.triggered.connect(self.editor.redo)
        self.actionCut.triggered.connect(self.editor.cut)
        self.actionCopy.triggered.connect(self.editor.copy)
        self.actionPaste.triggered.connect(self.editor.paste)
        QGuiApplication.clipboard().dataChanged.connect(
            lambda: self.actionPaste.setEnabled(
                QGuiApplication.clipboard().text() != ''))
        self.actionDelete.triggered.connect(self.editor.removeSelectedText)
        self.actionSelectAll.triggered.connect(self.editor.selectAll)
        self.actionComment.triggered.connect(self._slot_comment)
        self.actionUncomment.triggered.connect(self._slot_uncomment)
        self.actionShowWhitespace.triggered.connect(
            lambda checked: self.editor.setWhitespaceVisibility(
                QsciScintilla.WsVisible if checked else QsciScintilla.WsInvisible))
        self.actionShowEol.triggered.connect(self.editor.setEolVisibility)
        if ASSISTANT_BIN:
            action = QAction('Qt Assistant', self.menuHelp)
            action.triggered.connect(self._slot_help_assistant)
            action.setShortcut(QKeySequence('Ctrl+F1'))
            self.menuHelp.addAction(action)
        for i, chm_file in enumerate(CHM_FILES):
            action = QAction(os.path.basename(chm_file), self.menuHelp)
            action.triggered.connect(lambda _, chm_file=chm_file: self._slot_help_chm(chm_file))
            if i < 11:
                action.setShortcut(QKeySequence('Ctrl+F' + str(i+2)))
            self.menuHelp.addAction(action)

        self.actionAbout.triggered.connect(self._slot_about)

    def _setup_toolbar(self):
        # toolbar actions
        self.actionRun.triggered.connect(self._slot_toggle_run)
        self.actionDebug.triggered.connect(self._slot_toggle_debug)
        self.actionContinue.triggered.connect(self._slot_continue)
        self.actionStepInto.triggered.connect(self._slot_step_into)
        self.actionStepOver.triggered.connect(self._slot_step_over)
        self.actionStepOut.triggered.connect(self._slot_step_out)
        self.actionToggleBreakpoint.triggered.connect(self._slot_toggle_breakpoint)
        self.actionClearBreakpoints.triggered.connect(self._slot_clear_breakpoints)
        # add lineEdit for specifying program args
        self.line_edit_args = QLineEdit()
        # add a comboBox to allow switching between loaded files
        self.combo_box_files = QComboBox()
        self.combo_box_files.textActivated.connect(self._slot_combobox_item_activated)
        splitter = QSplitter(Qt.Horizontal, self)
        widget = QWidget()
        layout = QHBoxLayout()
        layout.setContentsMargins(5, 0, 5, 0)
        widget.setLayout(layout)
        label = QLabel('Program args:')
        label.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)
        layout.addWidget(label)
        layout.addWidget(self.line_edit_args)
        splitter.addWidget(widget)
        widget = QWidget()
        layout = QHBoxLayout()
        layout.setContentsMargins(5, 0, 0, 0)
        widget.setLayout(layout)
        label = QLabel('Loaded scripts:')
        label.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)
        layout.addWidget(label)
        layout.addWidget(self.combo_box_files)
        splitter.addWidget(widget)
        self.toolBar.addWidget(splitter)

    def _setup_lists(self):
        self.listWidgetBreakpoints.clicked.connect(self._slot_breakpoint_list_clicked)
        self.treeWidgetLocals.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.treeWidgetLocals.itemDoubleClicked.connect(self._slot_var_item_double_clicked)
        self.treeWidgetLocals.itemChanged.connect(self._slot_var_item_changed)
        self.treeWidgetGlobals.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.treeWidgetGlobals.itemDoubleClicked.connect(self._slot_var_item_double_clicked)
        self.treeWidgetGlobals.itemChanged.connect(self._slot_var_item_changed)
        self.treeWidgetStack.index = 0
        self.treeWidgetStack.itemClicked.connect(self._slot_stack_item_clicked)

    def _setup_console(self):
        # remove horizontal scrollBar
        self.console.SendScintilla(SCI.SCI_SETHSCROLLBAR, 0, 0)
        self.console.setEolMode(2) # LF
        self.console.setWrapMode(1)
        # hide the default fold margin
        self.console.setMarginWidth(1, 0)
        if os.name == 'nt':
            font_family = 'Consolas'
            font_size = 9
        else:
            font_family = 'Menlo'
            font_size = 11
        colors = ('#000000', '#0000ff', '#ff0000', '#8080ff')
        for i, color in enumerate(colors):
            self.console.SendScintilla(
                SCI.SCI_STYLESETFORE, i, Main._color_to_bgr_int(QColor(color)))
            self.console.SendScintilla(SCI.SCI_STYLESETFONT, i, font_family.encode())
            self.console.SendScintilla(SCI.SCI_STYLESETSIZE, i, font_size)
        self.console.setReadOnly(True)
        self.console.SCN_URIDROPPED.connect(self._slot_file_dropped)

    def _setup_editor(self):
        # set default editor settings
        self.editor.setFont(DEFAULT_FONT)
        self.editor.setMarginsFont(LINENO_FONT)
        self.editor.setBraceMatching(QsciScintilla.SloppyBraceMatch)
        self.editor.setCaretLineVisible(True)
        self.editor.setCaretForegroundColor(QColor('#000000'))
        self.editor.setCaretLineBackgroundColor(QColor('#c4e8fd'))
        self.editor.setAutoIndent(True)
        self.editor.setTabIndents(True)
        # tab width
        self.editor.setTabWidth(4)
        #  line numbers (margin 0)
        self.editor.setMarginLineNumbers(0, True)
        self.editor.setMarginWidth(0, '00000')
        # hide symbol margin
        self.editor.setMarginWidth(1, 0)
        # folding
        self.editor.setFolding(1)
        # indentation guides
        self.editor.setIndentationGuides(True)
        # wrap mode
        wrap_lines = False
        self.editor.setWrapMode(wrap_lines)
        if wrap_lines:
            # remove horizontal scrollBar
            self.editor.SendScintilla(SCI.SCI_SETHSCROLLBAR, 0, 0)
        lexer = QsciLexerPython(self)
        # apply default settings to lexer
        lexer.setDefaultFont(DEFAULT_FONT)
        lexer.setFont(DEFAULT_FONT)
        # margins
        lexer.setPaper(MARGIN_BGCOLOR, SCI.STYLE_LINENUMBER)
        lexer.setColor(MARGIN_COLOR, SCI.STYLE_LINENUMBER)
        lexer.setFont(DEFAULT_FONT, SCI.STYLE_LINENUMBER)
        # assign the lexer
        self.editor.setLexer(lexer)
        self.editor.SendScintilla(SCI.SCI_COLOURISE, 0, -1)
        # margins
        self.editor.setMarginsBackgroundColor(MARGIN_BGCOLOR)
        self.editor.setMarginsForegroundColor(MARGIN_COLOR)
        self.editor.setMarginsFont(LINENO_FONT)
        # folding
        self.editor.setFolding(FOLDING)
        self.editor.SendScintilla(SCI.SCI_SETMARGINWIDTHN, FOLD_MARGIN_NUM, FOLD_MARGIN_WIDTH)
        # set fold margin colors
        self.editor.SendScintilla(
            SCI.SCI_SETFOLDMARGINCOLOUR, True, Main._color_to_bgr_int(FOLD_MARGIN_COLOR))
        self.editor.SendScintilla(
            SCI.SCI_SETFOLDMARGINHICOLOUR, True, Main._color_to_bgr_int(FOLD_MARGIN_HICOLOR))
        # create and configure the breakpoint column
        self.editor.setMarginWidth(self.__symbol_margin_num, 17)
        self.editor.markerDefine(BREAKPOINT_SYMBOL, self.__breakpoint_marker_num)
        self.editor.setMarginMarkerMask(self.__symbol_margin_num, self.__breakpoint_marker_mask)
        self.editor.setMarkerBackgroundColor(BREAKPOINT_COLOR, self.__breakpoint_marker_num)
        # make breakpoint margin clickable
        self.editor.setMarginSensitivity(self.__symbol_margin_num, True)
        # add new callback for breakpoints
        self.editor.marginClicked.connect(self._slot_margin_clicked)
        # setup active line marker
        self.editor.markerDefine(ACTIVE_LINE_SYMBOL, self.__active_line_marker_num)
        self.editor.setMarkerForegroundColor(ACTIVE_LINE_COLOR, self.__active_line_marker_num)
        self.editor.setMarkerBackgroundColor(ACTIVE_LINE_COLOR, self.__active_line_marker_num)
        # connect signals
        self.editor.textChanged.connect(self._slot_text_changed)
        self.editor.modificationChanged.connect(self._slot_editor_modification_changed)
        self.editor.SCN_URIDROPPED.connect(self._slot_file_dropped)
        self.editor.copyAvailable.connect(self.actionCut.setEnabled)
        self.editor.copyAvailable.connect(self.actionCopy.setEnabled)
        self.editor.selectionChanged.connect(
            lambda: self.actionDelete.setEnabled(self.editor.hasSelectedText()))
        self.editor.selectionChanged.connect(
            lambda: self.actionSelectAll.setEnabled(self.editor.hasSelectedText()))
        # autocomplete
        if API_FILE is not None:
            apis = QsciAPIs(self.editor.lexer())
            apis.loadPrepared(API_FILE)
        self.editor.setAutoCompletionThreshold(3)
        # The source is any installed APIs.
        self.editor.setAutoCompletionSource(QsciScintilla.AcsAPIs)

    def _setup_outline(self):
        self.__class_icon = QIcon(':/icons/outline-class.png')
        self.__meth_icon = QIcon(':/icons/outline-method.png')
        self.__func_icon = QIcon(':/icons/outline-function.png')
        self.outline.itemClicked.connect(self._slot_outline_clicked)

    def _update_status_info(self):
        """ Updates infos in statusbar. """
        msg = 'File Size: ' + str(self.editor.length())
        msg += '  |  Encoding: ' + self.__encoding
        msg += '  |  EOL: ' + ['Win (CR LF)', 'Mac (CR)', 'Unix (LF)'][self.editor.eolMode()]
        self.label_info.setText(msg)

    def _maybe_save(self):
        """
        Checks for unsaved changes in loaded script.
        @return {bool} success
        """
        if not self.editor.isModified():
            return True
        btns = QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel
        msg = 'The script has been modified.<br>Do you want to save your changes?'
        ret = QMessageBox.warning(self, 'qpdb', msg, btns)
        if ret == QMessageBox.Save:
            return self._slot_save()
        if ret == QMessageBox.Cancel:
            return False
        return True

    def _print_console(self, msg):
        """ Prints raw message to console. """
        self.console.append(msg)
        # make sure last line is visible and set cursor position to end
        lastline_index = self.console.lines() - 1
        self.console.ensureLineVisible(lastline_index)
        pos = self.console.lineLength(lastline_index)
        self.console.setCursorPosition(lastline_index, pos)

    def _print_stdout(self, msg):
        """ Prints message to console, styled as stdout. """
        pos = self.console.positionFromLineIndex(self.console.lines() - 1, 0)
        self._print_console(msg)
        # change color
        pos2 = self.console.positionFromLineIndex(self.console.lines() - 1, 0)
        self.console.SendScintilla(SCI.SCI_STARTSTYLING, pos, SCI.STYLE_STDOUT)
        self.console.SendScintilla(SCI.SCI_SETSTYLING, pos2 - pos, SCI.STYLE_STDOUT)

    def _print_stderr(self, msg):
        """ Prints message to console, styled as stderr. """
        pos = self.console.positionFromLineIndex(self.console.lines() - 1, 0)
        self._print_console(msg)
        # change color
        pos2 = self.console.positionFromLineIndex(self.console.lines() - 1, 0)
        self.console.SendScintilla(SCI.SCI_STARTSTYLING, pos, SCI.STYLE_STDERR)
        self.console.SendScintilla(SCI.SCI_SETSTYLING, pos2 - pos, SCI.STYLE_STDERR)

    def _run(self):
        # auto-save?
        if self.editor.isModified():
            self._slot_save()
        self.console.clear()
        self.editor.setReadOnly(True)
        qenv = QProcessEnvironment.systemEnvironment()
        qenv.insert('PYTHONPATH', PATH)
        self.__proc.setProcessEnvironment(qenv)
        self.__proc.setWorkingDirectory(os.path.dirname(os.path.realpath(self.__filename)))
        self.__proc.start(sys.executable + ' "'+self.__filename+'" '+self.line_edit_args.text())

        self.__running = True
        self._update_ui()

    def _debug(self):
        if self.editor.isModified():
            self._slot_save()
        self.console.clear()
        self.editor.setReadOnly(True)
        qenv = QProcessEnvironment.systemEnvironment()
        qenv.insert('PYTHONPATH', PATH)
        self.__proc.setProcessEnvironment(qenv)
        self.__proc.setWorkingDirectory(os.path.dirname(os.path.realpath(self.__filename)))
        self.__proc.start(
            sys.executable + ' -u -m jsonpdb "'+self.__filename+'" '+self.line_edit_args.text())
        # set breakpoints (for current file and others)
        for row in range(self.listWidgetBreakpoints.count()):
            list_item = self.listWidgetBreakpoints.item(row)
            lineno = list_item.data(Qt.UserRole + 1)
            self.__proc.write(
                ('b ' + self.__filename + ':' + str(lineno + 1) + '\n').encode(PROC_ENCODING))
        for filename, linenos in self.__saved_breakpoints.items():
            if filename == self.__filename:
                continue
            for lineno in linenos:
                self.__proc.write(
                    ('b ' + filename + ':' + str(lineno + 1) + '\n').encode(PROC_ENCODING))
        self.__dbg_running = True
        self._update_ui()
        self._update_vars_and_stack()

    def _stop(self):
        self.__proc.kill()
        self.editor.setReadOnly(False)
        self.__running = False
        self.__dbg_running = False
        self._update_ui()

    def _load_script(self, filename):
        filename = os.path.realpath(filename).lower()  # normalize
        try:
            data = open(filename, 'rb').read()
        except FileNotFoundError:
            return False
        if self.combo_box_files.findText(filename) < 0:
            self.combo_box_files.addItem(filename)
        self.combo_box_files.setCurrentText(filename)
        # if another script is already opened, save its breakpoints
        if self.__filename:
            self.__saved_breakpoints[self.__filename] = []
            for row in range(self.listWidgetBreakpoints.count()):
                list_item = self.listWidgetBreakpoints.item(row)
                lineno = list_item.data(Qt.UserRole + 1)
                self.__saved_breakpoints[self.__filename].append(lineno)
        self.__filename = filename
        # guess encoding
        self.__encoding = Main._get_encoding(data)
        self.editor.setUtf8(True)
        txt = data.decode(self.__encoding, 'ignore')
        self.setWindowTitle(os.path.basename(self.__filename) + '[*] - qpdb')
        self.editor.textChanged.disconnect(self._slot_text_changed)
        self.editor.setText(txt)  # triggers textChanged
        self.editor.textChanged.connect(self._slot_text_changed)
        self.listWidgetBreakpoints.clear()
        # restore saved breakpoints
        if self.__filename in self.__saved_breakpoints:
            for lineno in self.__saved_breakpoints[self.__filename]:
                marker_handle = self.editor.markerAdd(lineno, self.__breakpoint_marker_num)
                # add new breakpoint to breakpoints pane
                list_item = QListWidgetItem()
                list_item.setText('Breakpoint ' + str(lineno + 1).zfill(4))
                list_item.setData(Qt.UserRole, marker_handle)
                list_item.setData(Qt.UserRole + 1, lineno)
                self.listWidgetBreakpoints.addItem(list_item)
        else:
            self.__saved_breakpoints[self.__filename] = []
        # guess eolMode
        eol_mode = Main._get_eol_mode(txt)
        self.editor.setEolMode(eol_mode)
        self.editor.setModified(False)
        self._update_ui()
        self._update_outline()
        self._update_status_info()
        return True

    def _add_var_item(self, parent_item, var_name, var_type, var_value):
        tree_item = QTreeWidgetItem()
        tree_item.setText(0, var_name)
        tree_item.setText(1, var_type)
        if not isinstance(var_value, (dict, list)):
            txt = str(var_value)
            if var_type == 'str':
                txt = "'" + txt.replace("'", "\\'") + "'"
            tree_item.setText(2, txt)
        tree_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
        parent_item.addChild(tree_item)
        if isinstance(var_value, dict):
            for key, data in var_value.items():
                self._add_var_item(tree_item, key, data[0], data[1])
        elif isinstance(var_value, list):
            for i, elem in enumerate(var_value):
                self._add_var_item(tree_item, '[' + str(i) + ']', elem[0], elem[1])
        elif hasattr(var_value, '__dict__'):
            for key, data in var_value.__dict__.items():
                self._add_var_item(tree_item, key, data[0], data[1])

    def _update_vars_and_stack(self):
        self._update_vars()
        self._update_stack()

    def _update_vars(self):
        self.__proc.write('dump\n'.encode(PROC_ENCODING))

    def _update_stack(self):
        self.__proc.write('w\n'.encode(PROC_ENCODING))  # where

    def _update_ui(self):
        self.actionLoad.setEnabled(not self.__dbg_running)
        self.actionClose.setEnabled(self.__filename is not None)
        self.actionRun.setChecked(self.__running)
        self.actionRun.setEnabled(self.__filename is not None and not self.__dbg_running)
        self.actionDebug.setChecked(self.__dbg_running)
        self.actionDebug.setEnabled(self.__filename is not None and not self.__running)
        self.actionContinue.setEnabled(self.__dbg_running)
        self.actionStepInto.setEnabled(self.__dbg_running)
        self.actionStepOver.setEnabled(self.__dbg_running)
        self.actionStepOut.setEnabled(self.__dbg_running)
        self.actionToggleBreakpoint.setEnabled(self.__filename is not None)
        self.actionClearBreakpoints.setEnabled(self.__filename is not None)
        self.menuEdit.setEnabled(not self.__dbg_running)
        self.combo_box_files.setEnabled(not self.__dbg_running and not self.__running)

    @staticmethod
    def _compute_interval(node):
        min_lineno = node.lineno
        max_lineno = node.lineno
        for sub_node in ast.walk(node):
            if hasattr(sub_node, "lineno"):
                min_lineno = min(min_lineno, sub_node.lineno)
                max_lineno = max(max_lineno, sub_node.lineno)
        return (min_lineno, max_lineno + 1)

    @staticmethod
    def _file_to_tree(filename):
        with tokenize.open(filename) as file:
            parsed = ast.parse(file.read(), filename=filename)
        classes = intervaltree.IntervalTree()
        tree = intervaltree.IntervalTree()
        for node in ast.walk(parsed):
            if isinstance(node, (ast.ClassDef)):
                start, end = Main._compute_interval(node)
                classes[start:end] = node
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                start, end = Main._compute_interval(node)
                tree[start:end] = node
        return classes, tree

    def _update_outline(self):
        self.outline.clear()
        if self.__filename is None:
            return
        classes, functions = Main._file_to_tree(self.__filename)
        root_item = self.outline.invisibleRootItem()
        # classes
        classes = sorted(classes, key=lambda iv: iv[2].name)
        for iv_class in classes:
            begin, end, data = iv_class
            methods = functions[begin:end]
            functions.remove_overlap(begin, end)
            parent_item = QTreeWidgetItem()
            parent_item.setText(0, data.name)
            parent_item.setIcon(0, self.__class_icon)
            parent_item.setData(0, Qt.UserRole, data.lineno)
            root_item.addChild(parent_item)
            parent_item.setExpanded(True)
            # class methods
            methods = sorted(methods, key=lambda iv: iv[2].name)
            for iv_method in methods:
                begin, end, data = iv_method
                tree_item = QTreeWidgetItem()
                tree_item.setText(0, data.name)
                tree_item.setIcon(0, self.__meth_icon)
                tree_item.setData(0, Qt.UserRole, data.lineno)
                parent_item.addChild(tree_item)
        # top-level functions
        functions = sorted(functions, key=lambda iv: iv[2].name)
        for iv_function in functions:
            begin, end, data = iv_function
            tree_item = QTreeWidgetItem()
            tree_item.setText(0, data.name)
            tree_item.setIcon(0, self.__func_icon)
            tree_item.setData(0, Qt.UserRole, data.lineno)
            root_item.addChild(tree_item)

    def _handle_chunk(self, msg):
        lines = msg.split('\r\n' if os.name == 'nt' else '\n')
        for i, line in enumerate(lines):
            if line == '':
                continue
            match = re.match(self.__re_active, line)
            if match is not None:
                filename = match.group(1)
                lineno = int(match.group(2))
                # <frozen importlib._bootstrap>
                if filename[0] == '<':
                    continue
                self.editor.markerDeleteAll(self.__active_line_marker_num)
                if filename != self.__filename:
                    self._load_script(filename)
                    self.editor.setReadOnly(True)
                self.editor.markerAdd(lineno - 1, self.__active_line_marker_num)
                self.editor.ensureLineVisible(lineno - 1)
                continue
            # breakpoints
            if line[:21] == 'Clear all breaks? ...':
                continue
            # Breakpoint 1 at d:\projects\python_debug\dbg_test.py:17
            match = re.match(self.__re_bp_add, line)
            if match is not None:
                #num = match.group(1)
                filename = match.group(2)
                lineno = int(match.group(3))
                continue
            # Deleted breakpoint 2 at d:\projects\python_debug\dbg_test.py:22
            match = re.match(self.__re_bp_del, line)
            if match is not None:
                #num = match.group(1)
                filename = match.group(2)
                continue
            # check for vars update
            if line.startswith('__ENV__:'):
                try:
                    env = json.loads(line[8:])
                except json.JSONDecodeError as err:
                    print(err)
                    env = None
                if isinstance(env, dict):
                    self.treeWidgetLocals.clear()
                    for var_name, data in env['locals'][1].items():
                        self._add_var_item(
                            self.treeWidgetLocals.invisibleRootItem(), var_name, data[0], data[1])
                    self.treeWidgetGlobals.clear()
                    for var_name, data in env['globals'][1].items():
                        self._add_var_item(
                            self.treeWidgetGlobals.invisibleRootItem(), var_name, data[0], data[1])
                continue
            # check for stack update
            if line[:2] == '  ':
                self.treeWidgetStack.clear()
                for j in range(i + 1, len(lines)):
                    line = lines[j]
                    if line[:3] == '-> ':
                        continue
                    match = re.match(self.__re_stack, line)
                    if match is not None:
                        filename = match.group(1)
                        if filename.startswith('<'):
                            continue # only files
                        # ignore pdb related files
                        if os.path.basename(filename) in ['pdb.py', 'bdb.py', 'jsonpdb.py']:
                            continue
                        tree_item = QTreeWidgetItem()
                        tree_item.setText(0, os.path.basename(filename))
                        tree_item.setText(1, match.group(2))  # lineno
                        tree_item.setText(2, match.group(3))  # func
                        tree_item.setToolTip(0, filename)
                        self.treeWidgetStack.invisibleRootItem().addChild(tree_item)
                cnt = self.treeWidgetStack.topLevelItemCount()
                if cnt > 0:
                    self.treeWidgetStack.setCurrentItem(self.treeWidgetStack.topLevelItem(cnt - 1))
                    self.treeWidgetStack.index = cnt - 1
                break
            self._print_console(line + '\n')

    def _toggle_breakpoint(self, lineno):
        mask = self.editor.markersAtLine(lineno)
        if mask & self.__breakpoint_marker_mask:  # line has a breakpoint, so remove it
            # find the marker_handle
            for row in range(self.listWidgetBreakpoints.count()):
                list_item = self.listWidgetBreakpoints.item(row)
                if list_item.data(Qt.UserRole + 1) == lineno:
                    marker_handle = list_item.data(Qt.UserRole)
                    # remove breakpoint item
                    self.listWidgetBreakpoints.takeItem(row)
                    break
            # delete the marker
            self.editor.markerDelete(lineno, self.__breakpoint_marker_num)
            if self.__dbg_running:
                self.__proc.write(
                    ('cl ' + self.__filename + ':' + str(lineno + 1) + '\n').encode(PROC_ENCODING))
        else:  # line has no breakpoint, so add a new one
            # check if valid position
            txt = self.editor.text(lineno).strip()
            if txt == '' or txt[0] == '#':
                return False
            marker_handle = self.editor.markerAdd(lineno, self.__breakpoint_marker_num)
            # add new breakpoint to breakpoints pane
            list_item = QListWidgetItem()
            list_item.setText('Breakpoint ' + str(lineno + 1).zfill(4))
            list_item.setData(Qt.UserRole, marker_handle)
            list_item.setData(Qt.UserRole + 1, lineno)
            self.listWidgetBreakpoints.addItem(list_item)
            self.listWidgetBreakpoints.sortItems()
            if self.__dbg_running:
                self.__proc.write(
                    ('b ' + self.__filename + ':' + str(lineno + 1) + '\n').encode(PROC_ENCODING))
        return True

    def _slot_toggle_run(self, flag):
        if flag:
            self._run()
        else:
            self._stop()

    def _slot_toggle_debug(self, flag):
        if flag:
            self._debug()
        else:
            self._stop()

    def _slot_load(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, 'Load Python Scripts', '', 'Python Files (*.py *.pyw);;All Files (*.*)')
        for filename in files:
            self._load_script(filename)

    def _slot_save(self):
        if self.__filename is None:
            return self._slot_save_as()
        try:
            with open(self.__filename, 'wb') as file:
                file.write(self.editor.text().encode(self.__encoding))
            self.editor.setModified(False)
            self._update_outline()
            return True
        except FileNotFoundError:
            return False

    def _slot_save_as(self):
        filename, _ = QFileDialog.getSaveFileName(
            self, 'Save Python Script', '', 'Python Files (*.py *.pyw);;All Files (*.*)')
        if not filename:
            return False
        try:
            filename = os.path.realpath(filename).lower() # normalize
            with open(filename, 'wb') as file:
                file.write(self.editor.text().encode(self.__encoding))
            if self.combo_box_files.findText(filename) < 0:
                self.combo_box_files.addItem(filename)
            self.combo_box_files.setCurrentText(filename)
            self.__filename = filename
            self.editor.append('') # clear undo history
            self.editor.setModified(False)
            self.setWindowTitle(os.path.basename(self.__filename) + '[*] - qpdb')
            self._update_outline()
            return True
        except FileNotFoundError:
            return False

    def _slot_close(self):
        if self.__dbg_running:
            self._stop()
        if not self._maybe_save():
            return
        self.editor.clear()
        self.console.clear()
        self.combo_box_files.clear()
        self.outline.clear()
        self.listWidgetBreakpoints.clear()
        self.__filename = None
        self.__encoding = 'UTF-8'
        self.editor.setModified(False)
        self.setWindowTitle('unsaved[*] - qpdb')
        self._update_ui()
        self.label_info.clear()
        self.__saved_breakpoints = {}

    def _slot_stdout(self):
        res = self.__proc.readAllStandardOutput().data().decode(PROC_ENCODING, 'ignore')
        if self.__dbg_running:
            chunks = res.split('(Pdb) ')
            cnt = len(chunks)
            if cnt == 1: # no (Pdb) found
                self.__last_chunk += chunks[0]
            else:
                self._handle_chunk(self.__last_chunk + chunks[0])
                self.__last_chunk = ''
                if cnt > 2:
                    for i in range(1, cnt - 1):
                        self._handle_chunk(chunks[i])
                self.__last_chunk = chunks[cnt - 1]
        else:
            self._print_stdout(res)

    # ######################################
    def _slot_stderr(self):
        res = self.__proc.readAllStandardError().data().decode(PROC_ENCODING, 'ignore')
        if self.__dbg_running:
            # remove debugger internals from Traceback
            if res[:7] == '  File ':
                lines = res.split('\r\n')
                res = '\r\n'.join(lines[7:])
        self._print_stderr(res)

    def _slot_complete(self):
        self._print_console('Execution finished.\n')
        # remove active line marker
        self.editor.markerDeleteAll(self.__active_line_marker_num)
        # unlock editor
        self.editor.setReadOnly(False)
        # clear stack and var panes
        self.treeWidgetLocals.clear()
        self.treeWidgetGlobals.clear()
        self.treeWidgetStack.clear()
        self.__running = False
        self.__dbg_running = False
        self._update_ui()

    def _slot_step_into(self):
        self.__proc.write('s\n'.encode(PROC_ENCODING)) # step
        self._update_vars_and_stack()

    def _slot_step_over(self):
        self.__proc.write('n\n'.encode(PROC_ENCODING)) # next
        self._update_vars_and_stack()

    def _slot_step_out(self):
        self.__proc.write('r\n'.encode(PROC_ENCODING)) # return
        self._update_vars_and_stack()

    def _slot_continue(self):
        self.__proc.write('c\n'.encode(PROC_ENCODING)) # continue
        self._update_vars_and_stack()

    def _slot_toggle_breakpoint(self):
        lineno, _ = self.editor.getCursorPosition()
        self._toggle_breakpoint(lineno)

    def _slot_clear_breakpoints(self):
        self.editor.markerDeleteAll(self.__breakpoint_marker_num)
        self.listWidgetBreakpoints.clear()
        self.__saved_breakpoints[self.__filename] = []
        if not self.__dbg_running:
            return
        self.__proc.write('cl\ny\n'.encode(PROC_ENCODING)) # asks for confirmation

    def _slot_margin_clicked(self, marg, lineno, _):
        if marg != self.__symbol_margin_num or lineno < 0:
            return
        self._toggle_breakpoint(lineno)

    def _slot_text_changed(self):
        for row in range(self.listWidgetBreakpoints.count() - 1, -1, -1):
            list_item = self.listWidgetBreakpoints.item(row)
            marker_handle = list_item.data(Qt.UserRole)
            lineno = self.editor.markerLine(marker_handle)
            if lineno < 0: # marker was deleted, remove list_item
                self.listWidgetBreakpoints.takeItem(row)
            else:
                lineno_saved = list_item.data(Qt.UserRole + 1)
                if lineno_saved != lineno:
                    list_item.setData(Qt.UserRole + 1, lineno)
                    # update breakpoint name
                    list_item.setText('Breakpoint ' + str(lineno + 1).zfill(4))
        self._update_status_info()

    def _slot_editor_modification_changed(self, changed):
        self.setWindowModified(changed)
        # qt5 bug fix for macos
        if os.name != 'nt':
            title = self.windowTitle()
            self.setWindowTitle('')
            self.setWindowTitle(title)
        self.actionSave.setEnabled(changed)
        self.actionUndo.setEnabled(self.editor.isUndoAvailable())
        self.actionRedo.setEnabled(self.editor.isRedoAvailable())

    def _slot_var_item_double_clicked(self, tree_item, column):
        if not self.__dbg_running or column != 2:
            return
        if tree_item.text(1) in ('str', 'int', 'float'):
            self.sender().editItem(tree_item, 2)

    def _slot_var_item_changed(self, tree_item, _):
        var_name = tree_item.text(0)
        var_value = tree_item.text(2)
        var_names = []
        var_types = []
        while tree_item.parent() is not None:
            tree_item = tree_item.parent()
            var_names.insert(0, tree_item.text(0))
            var_types.insert(0, tree_item.text(1))
        if len(var_names) > 0:
            txt = var_names[0]
            for i in range(1, len(var_names)):
                if var_types[i - 1] == 'list':
                    txt += var_names[i]
                elif var_types[i - 1] == 'dict':
                    txt += '[\'' + var_names[i] + '\']'
                else:
                    txt += '.' + var_names[i] # other object
            if var_types[-1] == 'list':
                txt += var_name
            elif var_types[-1] == 'dict':
                txt += '[\'' + var_name + '\']'
            else:
                txt += '.' + var_name # other object
            self.__proc.write(('!' + txt + '=' + var_value + '\n').encode(PROC_ENCODING))
        else:
            # next
            self.__proc.write(('!' + var_name + '=' + var_value + '\n').encode(PROC_ENCODING))

    def _slot_stack_item_clicked(self, tree_item, _):
        idx = self.treeWidgetStack.indexFromItem(tree_item).row()
        if idx == self.treeWidgetStack.index:
            return
        delta = idx - self.treeWidgetStack.index
        self.treeWidgetStack.index = idx
        if delta < 0:
            command = 'u ' + str(-delta) + '\n' # up
        else:
            command = 'd ' + str(delta) + '\n' # down
        self.__proc.write(command.encode(PROC_ENCODING))
        self._update_vars()

    def _slot_breakpoint_list_clicked(self, model_index):
        marker_handle = self.listWidgetBreakpoints.item(model_index.row()).data(Qt.UserRole)
        lineno = self.editor.markerLine(marker_handle)
        self.editor.ensureLineVisible(lineno)
        self.editor.setCursorPosition(lineno, 0)
        self.editor.setFocus(Qt.MouseFocusReason)

    def _slot_outline_clicked(self, tree_item, _):
        lineno = tree_item.data(0, Qt.UserRole) - 1
        self.editor.ensureLineVisible(lineno)
        self.editor.setCursorPosition(lineno, 0)
        self.editor.setFocus(Qt.MouseFocusReason)

    def _slot_comment(self):
        """ Comments out current selection. """
        # get full line selection
        line_from, _, line_to, _ = self.editor.getSelection()
        eol = ['\r\n', '\r', '\n'][self.editor.eolMode()]
        last_line = self.editor.text(line_to)
        self.editor.setSelection(line_from, 0, line_to, len(last_line) - len(eol))
        # replace
        txt = self.editor.selectedText()
        lines = txt.split(eol)
        for i, line in enumerate(lines):
            lines[i] = '#' + line
        txt = eol.join(lines)
        self.editor.replaceSelectedText(txt)
        # reset selection
        last_line = self.editor.text(line_to)
        self.editor.setSelection(line_from, 0, line_to, len(last_line) - len(eol))

    def _slot_uncomment(self):
        """ Uncomments current selection. """
        # get full line selection
        line_from, _, line_to, _ = self.editor.getSelection()
        eol = ['\r\n', '\r', '\n'][self.editor.eolMode()]
        last_line = self.editor.text(line_to)
        self.editor.setSelection(line_from, 0, line_to, len(last_line) - len(eol))
        # replace
        txt = self.editor.selectedText()
        lines = txt.split(eol)
        for i, line in enumerate(lines):
            if line.startswith('#'):
                lines[i] = line[1:]
        txt = eol.join(lines)
        self.editor.replaceSelectedText(txt)
        # reset selection
        last_line = self.editor.text(line_to)
        self.editor.setSelection(line_from, 0, line_to, len(last_line) - len(eol))

    def _slot_help_chm(self, chm_file):
        if os.name == 'nt':
            txt = self.editor.selectedText()
            if txt == '':
                args = ['-DirHelp', chm_file]
            else:
                args = ['-#klink', txt, '-DirHelp', chm_file]
            proc = QProcess()
            proc.setProgram(PATH + '/resources/bin/win/KeyHH.exe')
            proc.setArguments(args)
            proc.startDetached()
        else:
            proc = QProcess()
            # install with MacPorts: port install xchm
            proc.setProgram('/opt/local/bin/xchm')
            proc.setArguments([chm_file])
            proc.startDetached()

    def _slot_help_assistant(self):
        if self.__proc_assistant.state() == QProcess.NotRunning:
            self.__proc_assistant.start()
            if not self.__proc_assistant.waitForStarted():
                QMessageBox.critical(self, 'Remote Control', 'Could not start Qt Assistant.')
                return
        txt = self.editor.selectedText()
        if txt != '':
            self.__proc_assistant.write(
                'show index;activateKeyword {}\n'.format(txt).encode(PROC_ENCODING))
        if os.name == 'nt':
            hwnd = user32.FindWindowW(None, 'Qt Assistant')
            if hwnd:
                user32.SetForegroundWindow(hwnd)

    def _slot_about(self):
        QMessageBox.about(self, 'About qpdb', '''
            <b>qpdb</b><br>(c) 2020 Valentin Schmidt<br><br>
            A simple visual Python 3 debugger and code editor
            based on pdb, PyQt5 and Scintilla.
            ''')

    def _slot_combobox_item_activated(self, filename):
        if not self._maybe_save():
            self.combo_box_files.setCurrentText(self.__filename)
            return
        self._load_script(filename)

    def _slot_file_dropped(self, url):
        if not self._maybe_save():
            return
        self._load_script(url.toLocalFile())


def main():
    """ main """
    sys.excepthook = traceback.print_exception
    app = QApplication(sys.argv)
    Main()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()

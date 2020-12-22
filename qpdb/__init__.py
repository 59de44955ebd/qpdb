#+++++++++++++++++++++++++++++++++++++++
# @file qpdb - main class
# @author Valentin Schmidt
# @version 0.1
#+++++++++++++++++++++++++++++++++++++++

import json
import os
import re
import sys
import time
from PyQt5.QtCore import Qt, QResource, QProcess, QProcessEnvironment, QSettings
from PyQt5.QtGui import QColor, QGuiApplication, QFont, QIcon, QKeySequence
from PyQt5.QtWidgets import (QApplication, QMainWindow, QLabel, QComboBox, QMessageBox,
		QFileDialog, QListWidgetItem, QTreeWidgetItem, QAction, QAbstractItemView)
from PyQt5 import uic
from PyQt5.Qsci import QsciScintilla, QsciLexer, QsciLexerPython, QsciAPIs

#+++++++++++++++++++++++++++++++++++++++
# config
#+++++++++++++++++++++++++++++++++++++++

CHM_FILES = []
#CHM_FILES = ['C:\\dev\\python\\python_3.7.4-x64\\Doc\\python374.chm']

ASSISTANT_BIN = None
#ASSISTANT_BIN = 'C:\\dev\\qt5\\5.15.2\\msvc2019_64\\bin\\assistant.exe'

PROC_ENCODING = 'windows-1252'

class SCI():
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

PATH = os.path.dirname(os.path.realpath(__file__))
API_FILE = PATH+'/resources/prepared.api'

#+++++++++++++++++++++++++++++++++++++++
# Checks bytes for Byte-Order-Mark (BOM), returns BOM-type or False
# @param {bytes} b
# @return {string|false} - either 'UTF-8', 'UTF-16', 'UTF-16BE' or False
#+++++++++++++++++++++++++++++++++++++++
def has_bom (b):
	if len(b)>=3:
		if b[0]==239 and b[1]==187 and b[2]==191:
			return 'UTF-8'
	if len(b)>=2:
		if b[0]==255 and b[1]==254:
			return 'UTF-16' #LE
		elif b[0]==254 and b[1]==255:
			return 'UTF-16BE' #BE
	return False

#+++++++++++++++++++++++++++++++++++++++
# Checks bytes for invalid UTF-8 sequences
# Notice: since ASCII is a UTF-8 subset, function returns True for pure ASCII data
# @param {bytes} b
# @return {bool}
#+++++++++++++++++++++++++++++++++++++++
def is_utf8 (b):
	_len = len(b)
	i = -1
	while True:
		i += 1
		if i>=_len:
			break
		ord = b[i]
		if (ord < 128):
			continue
		elif ord & 224==192 and ord>193:
			n = 1 # 110bbbbb (C0-C1)
		elif ord & 240==224:
			n = 2 # 1110bbbb
		elif ord & 248==240 and ord<245:
			n = 3 # 11110bbb (F5-FF)
		else:
			return False # invalid UTF-8 sequence
		for c in range(n):
			i += 1
			if i>_len:
				return False # invalid UTF-8 sequence
			if b[i] & 192 != 128:
				return False # invalid UTF-8 sequence
	return True # no invalid UTF-8 sequence found

#+++++++++++++++++++++++++++++++++++++++
#
#+++++++++++++++++++++++++++++++++++++++
class Main (QMainWindow):

	#+++++++++++++++++++++++++++++++++++++++
	# @constructor
	#+++++++++++++++++++++++++++++++++++++++
	def __init__(self):
		super().__init__()

		QApplication.setStyle('Fusion')

		self.setStyleSheet(
		'''QDockWidget{
			font-size:11px;
			margin:0px;
			padding:0px;
			border-width:0px;
		}
		QDockWidget::title{
			border-left:1px solid #b9b9b9;
			padding-top:3px;
			padding-left:2px;
			padding-bottom:0px;
			background:#dadada;
		}
		QMainWindow::separator{
			height:1px;
			background:#dadada;
		}
		''')

		# load UI
		QResource.registerResource(PATH+'/resources/main.rcc')
		uic.loadUi(PATH+'/resources/main.ui', self)

		# setup statusBar
		self.labelInfo = QLabel(self.statusbar)
		self.labelInfo.setContentsMargins(0,0,10,0)
		self.statusbar.addPermanentWidget(self.labelInfo)

		self.__proc = QProcess()
		self.__proc.readyReadStandardOutput.connect(self.slot_stdout)
		self.__proc.readyReadStandardError.connect(self.slot_stderr)
		self.__proc.finished.connect(self.slot_complete)

		self.__symbol_margin_num = 1
		self.__last_chunk = ''
		self.__dbg_running = False
		self.__filename = None
		self.__saved_breakpoints = {}

		self.__re_active = re.compile('^> (.*)\(([0-9]+)\)')
		self.__re_bp_add = re.compile('^Breakpoint ([0-9]+) at (.*):([0-9]+)')
		self.__re_bp_del = re.compile('^Deleted breakpoint ([0-9]+) at (.*):([0-9]+)')
		self.__re_stack  = re.compile('^[ >] (.*)\(([0-9]+)\)([^(]+)\(\)')

		#+++++++++++++++++++++++++++++++++++++++
		# breakpoint marker
		#+++++++++++++++++++++++++++++++++++++++
		self.__breakpoint_marker_num = 1
		self.__breakpoint_marker_mask = 2 ** self.__breakpoint_marker_num
		self.__breakpoint_symbol = 0
		self.__breakpoint_color = QColor('#ff0000')

		#+++++++++++++++++++++++++++++++++++++++
		# active line marker
		#+++++++++++++++++++++++++++++++++++++++
		self.__active_line_marker_num = 2
		self.__active_line_symbol = 22
		self.__active_line_color = QColor('#ccffcc')

		self.setup_actions()
		self.setup_lists()
		self.setup_console()
		self.setup_editor()
		self.setup_outline()

		# restore window state
		self.__state = QSettings('fx', 'qpdb')
		val = self.__state.value('MainWindow/Geometry')
		if val is not None:
			self.restoreGeometry(val)
		val = self.__state.value('MainWindow/State')
		if val is not None:
			self.restoreState(val)

		self.show()

	#+++++++++++++++++++++++++++++++++++++++
	#
	#+++++++++++++++++++++++++++++++++++++++
	def closeEvent (self, e):
		self.__proc.kill()
		self.__proc.waitForFinished(5000)

		if not self.maybe_save():
			e.ignore()
			return

		# save window state
		self.__state.setValue('MainWindow/Geometry', self.saveGeometry())
		self.__state.setValue('MainWindow/State', self.saveState())

	#+++++++++++++++++++++++++++++++++++++++
	#
	#+++++++++++++++++++++++++++++++++++++++
	def dragEnterEvent (self, e):
		if e.mimeData().hasText():
			e.accept()

	#+++++++++++++++++++++++++++++++++++++++
	#
	#+++++++++++++++++++++++++++++++++++++++
	def dropEvent (self, e):
		if not self.maybe_save():
			return
		files = e.mimeData().text().split('\n')
		for fn in files:
			if fn is not None:
				if os.name=='posix':
					self.load_script(fn[7:])
				else:
					self.load_script(fn[8:])

	#+++++++++++++++++++++++++++++++++++++++
	# update statusbar
	#+++++++++++++++++++++++++++++++++++++++
	def update_status_info (self):
		msg = 'File Size: '+str(self.editor.length())
		msg += '  |  Encoding: '+self.__encoding
		msg += '  |  EOL: '+['Win (CR LF)', 'Mac (CR)', 'Unix (LF)'][self.editor.eolMode()]
		self.labelInfo.setText(msg)

	#+++++++++++++++++++++++++++++++++++++++
	# Checks for unsaved changes in loaded script
	# @return {bool} success
	#+++++++++++++++++++++++++++++++++++++++
	def maybe_save (self):
		if not self.editor.isModified():
			return True
		btns = QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel
		msg = 'The script has been modified.<br>Do you want to save your changes?'
		ret = QMessageBox.warning(self, 'qpdb', msg, btns)
		if ret == QMessageBox.Save:
			return self.slot_save()
		if ret == QMessageBox.Cancel:
			return False
		return True

	#+++++++++++++++++++++++++++++++++++++++
	#
	#+++++++++++++++++++++++++++++++++++++++
	def __color_to_bgr_int (self, col):
		return col.red() + (col.green() << 8) + (col.blue() << 16)

	#+++++++++++++++++++++++++++++++++++++++
	#
	#+++++++++++++++++++++++++++++++++++++++
	def setup_actions (self):
		self.actionLoad.triggered.connect(self.slot_load)
		self.actionClose.triggered.connect(self.slot_close)
		self.actionSave.triggered.connect(self.slot_save)

		self.actionUndo.triggered.connect(self.editor.undo)
		self.actionRedo.triggered.connect(self.editor.redo)
		self.actionCut.triggered.connect(self.editor.cut)
		self.actionCopy.triggered.connect(self.editor.copy)
		self.actionPaste.triggered.connect(self.editor.paste)

		self.actionDelete.triggered.connect(self.editor.removeSelectedText)
		self.actionSelectAll.triggered.connect(self.editor.selectAll)

		self.editor.copyAvailable.connect(self.actionCut.setEnabled)
		self.editor.copyAvailable.connect(self.actionCopy.setEnabled)
		QGuiApplication.clipboard().dataChanged.connect(lambda:
			self.actionPaste.setEnabled(QGuiApplication.clipboard().text()!=''))
		self.editor.selectionChanged.connect(lambda:
			self.actionDelete.setEnabled(self.editor.hasSelectedText()))
		self.editor.selectionChanged.connect(lambda:
			self.actionSelectAll.setEnabled(self.editor.hasSelectedText()))

		self.actionComment.triggered.connect(self.slot_comment)
		self.actionUncomment.triggered.connect(self.slot_uncomment)

		self.actionShowWhitespace.triggered.connect(lambda checked:
				self.editor.setWhitespaceVisibility(QsciScintilla.WsVisible if checked else QsciScintilla.WsInvisible))
		self.actionShowEol.triggered.connect(self.editor.setEolVisibility)

		for i in range(len(CHM_FILES)):
			a = QAction(os.path.basename(CHM_FILES[i]), self.menuHelp)
			a.triggered.connect(lambda _,chm=CHM_FILES[i]: self.slot_help_chm(chm))
			if i<11:
				a.setShortcut(QKeySequence('F'+str(i+1)))
			self.menuHelp.addAction(a)
		if ASSISTANT_BIN and os.path.isfile(ASSISTANT_BIN):
			a = QAction('Qt Assistant', self.menuHelp)
			a.triggered.connect(self.slot_help_assistant)
			a.setShortcut(QKeySequence('F12'))
			self.menuHelp.addAction(a)

		self.actionAbout.triggered.connect(self.slot_about)

		# toolbar actions
		self.actionDebug.triggered.connect(self.slot_toggle_debug)
		self.actionContinue.triggered.connect(self.slot_continue)
		self.actionStepInto.triggered.connect(self.slot_step_into)
		self.actionStepOver.triggered.connect(self.slot_step_over)
		self.actionStepOut.triggered.connect(self.slot_step_out)
		self.actionToggleBreakpoint.triggered.connect(self.slot_toggle_breakpoint)
		self.actionClearBreakpoints.triggered.connect(self.slot_clear_breakpoints)

		# add a comboBox to allow switching between loaded files
		self.comboBoxFiles = QComboBox()
		self.comboBoxFiles.setSizeAdjustPolicy(QComboBox.AdjustToContents)
		self.comboBoxFiles.textActivated.connect(self.slot_combobox_item_activated)
		self.toolBar.addWidget(QLabel('Loaded Scripts: '))
		self.toolBar.addWidget(self.comboBoxFiles)

	#+++++++++++++++++++++++++++++++++++++++
	#
	#+++++++++++++++++++++++++++++++++++++++
	def setup_lists (self):
		self.listWidgetBreakpoints.clicked.connect(self.slot_breakpoint_list_clicked)

		self.treeWidgetLocals.setEditTriggers(QAbstractItemView.NoEditTriggers)
		self.treeWidgetLocals.itemDoubleClicked.connect(self.slot_var_item_double_clicked)
		self.treeWidgetLocals.itemChanged.connect(self.slot_var_item_changed)

		self.treeWidgetGlobals.setEditTriggers(QAbstractItemView.NoEditTriggers)
		self.treeWidgetGlobals.itemDoubleClicked.connect(self.slot_var_item_double_clicked)
		self.treeWidgetGlobals.itemChanged.connect(self.slot_var_item_changed)

		self.treeWidgetStack.index = 0
		self.treeWidgetStack.itemClicked.connect(self.slot_stack_item_clicked)

	#+++++++++++++++++++++++++++++++++++++++
	#
	#+++++++++++++++++++++++++++++++++++++++
	def setup_console (self):
		# remove horizontal scrollBar
		self.console.SendScintilla(SCI.SCI_SETHSCROLLBAR, 0, 0)

		self.console.setEolMode(2) # LF
		self.console.setWrapMode(1)

		# hide the default fold margin
		self.console.setMarginWidth(1, 0)

		# set theme colors
		font_family = 'Consolas'
		font_size = 9.0
		colors = ('#000000', '#0000ff', '#ff0000', '#8080ff')
		for i in range(len(colors)):
			self.console.SendScintilla(SCI.SCI_STYLESETFORE, i, self.__color_to_bgr_int(QColor(colors[i])))
			self.console.SendScintilla(SCI.SCI_STYLESETFONT, i, font_family.encode())
			self.console.SendScintilla(SCI.SCI_STYLESETSIZE, i, font_size)

		self.console.setReadOnly(True)
		self.console.SCN_URIDROPPED.connect(self.slot_file_dropped)

	#+++++++++++++++++++++++++++++++++++++++
	#
	#+++++++++++++++++++++++++++++++++++++++
	def slot_file_dropped (self, u):
		if not self.maybe_save():
			return
		self.load_script(u.toLocalFile())

	#+++++++++++++++++++++++++++++++++++++++
	#
	#+++++++++++++++++++++++++++++++++++++++
	def setup_editor (self):

		# settings

		self.__default_font = QFont('Consolas', 10)
		self.__linenum_font = QFont('Consolas', 8)

		self.__margin_color = QColor('#666666')
		self.__margin_bgcolor = QColor('#e0e0e0')

		self.__fold_margin_color = QColor('#e0e0e0')
		self.__fold_margin_hicolor = QColor('#ffffff')

		self.__fold_margin_num = 2
		self.__fold_margin_width = 14

		#self.setAcceptDrops(False) # needed for macos

		# set default editor settings
		self.editor.setFont(self.__default_font)
		self.editor.setMarginsFont(self.__linenum_font)

		self.editor.setBraceMatching(QsciScintilla.SloppyBraceMatch)

		self.editor.setCaretLineVisible(True)
		self.editor.setCaretForegroundColor(QColor('#000000'))
		self.editor.setCaretLineBackgroundColor(QColor('#c4e8fd'))

		self.editor.setAutoIndent(True)
		self.editor.setTabIndents(True)

		# TabWidth
		self.editor.setTabWidth(4)

		# LineNumbers (margin 0)
		self.editor.setMarginLineNumbers(0, True)
		self.editor.setMarginWidth(0, '00000')

		# hide symbol margin
		self.editor.setMarginWidth(1, 0)

		# Folding
		self.__folding = 1
		self.editor.setFolding(1)

		# IndentationGuides
		self.editor.setIndentationGuides(True)

		# Wrap-Mode
		wrap_lines = False
		self.editor.setWrapMode(wrap_lines)
		if wrap_lines:
			# remove horizontal scrollBar
			self.editor.SendScintilla(SCI.SCI_SETHSCROLLBAR, 0, 0)

		lexer = QsciLexerPython(self)

		# apply default settings to lexer
		lexer.setDefaultFont(self.__default_font)
		lexer.setFont(self.__default_font)

		# margins
		lexer.setPaper(self.__margin_bgcolor, SCI.STYLE_LINENUMBER)
		lexer.setColor(self.__margin_color, SCI.STYLE_LINENUMBER)
		lexer.setFont(self.__default_font, SCI.STYLE_LINENUMBER)

		# assign the lexer
		self.editor.setLexer(lexer)
		self.editor.SendScintilla(SCI.SCI_COLOURISE, 0, -1)

		# margins
		self.editor.setMarginsBackgroundColor(self.__margin_bgcolor)
		self.editor.setMarginsForegroundColor(self.__margin_color)
		self.editor.setMarginsFont(self.__linenum_font)

		# folding
		self.editor.setFolding(self.__folding)
		self.editor.SendScintilla(SCI.SCI_SETMARGINWIDTHN, self.__fold_margin_num, self.__fold_margin_width)
		# set fold margin colors
		self.editor.SendScintilla(SCI.SCI_SETFOLDMARGINCOLOUR, True, self.__color_to_bgr_int(self.__fold_margin_color))
		self.editor.SendScintilla(SCI.SCI_SETFOLDMARGINHICOLOUR, True, self.__color_to_bgr_int(self.__fold_margin_hicolor))

		# create and configure the breakpoint column
		self.editor.setMarginWidth(self.__symbol_margin_num, 17)
		self.editor.markerDefine(self.__breakpoint_symbol, self.__breakpoint_marker_num)
		self.editor.setMarginMarkerMask(self.__symbol_margin_num, self.__breakpoint_marker_mask)
		self.editor.setMarkerBackgroundColor(self.__breakpoint_color, self.__breakpoint_marker_num)
		# make breakpoint margin clickable
		self.editor.setMarginSensitivity(self.__symbol_margin_num, True)
		# add new callback for breakpoints
		self.editor.marginClicked.connect(self.slot_margin_clicked)

		# setup active line marker
		self.editor.markerDefine(self.__active_line_symbol, self.__active_line_marker_num)
		self.editor.setMarkerForegroundColor(self.__active_line_color, self.__active_line_marker_num)
		self.editor.setMarkerBackgroundColor(self.__active_line_color, self.__active_line_marker_num)

		# connect signals
		self.editor.textChanged.connect(self.slot_text_changed)
		self.editor.modificationChanged.connect(self.slot_editor_modification_changed)
		self.editor.SCN_URIDROPPED.connect(self.slot_file_dropped)

		# autocomplete
		if API_FILE is not None:
			apis = QsciAPIs(self.editor.lexer())
			ok = apis.loadPrepared(API_FILE)
		self.editor.setAutoCompletionThreshold(3)
		self.editor.setAutoCompletionSource(QsciScintilla.AcsAPIs) # The source is any installed APIs.

	#+++++++++++++++++++++++++++++++++++++++
	#
	#+++++++++++++++++++++++++++++++++++++++
	def setup_outline (self):
		self.__class_icon = QIcon(':/icons/outline-class.png')
		self.__meth_icon = QIcon(':/icons/outline-method.png')
		self.__func_icon = QIcon(':/icons/outline-function.png')
		self.outline.itemClicked.connect(self.slot_outline_clicked)

	#+++++++++++++++++++++++++++++++++++++++
	# Prints raw message to console
	# @param {string} msg
	#+++++++++++++++++++++++++++++++++++++++
	def __print (self, msg):
		self.console.append(msg)
		# make sure last line is visible and set cursor position to end
		lastline_index = self.console.lines() - 1
		self.console.ensureLineVisible(lastline_index)
		pos = self.console.lineLength(lastline_index)
		self.console.setCursorPosition(lastline_index, pos)

	#+++++++++++++++++++++++++++++++++++++++
	# Prints message to console, styled as stdout
	# @param {string} msg
	#+++++++++++++++++++++++++++++++++++++++
	def print_stdout (self, msg):
		pos = self.console.positionFromLineIndex(self.console.lines()-1, 0)
		self.__print(msg)
		# change color
		pos2 = self.console.positionFromLineIndex(self.console.lines()-1, 0)
		self.console.SendScintilla(SCI.SCI_STARTSTYLING, pos, SCI.STYLE_STDOUT)
		self.console.SendScintilla(SCI.SCI_SETSTYLING, pos2-pos, SCI.STYLE_STDOUT)

	#+++++++++++++++++++++++++++++++++++++++
	# Prints message to console, styled as stderr
	# @param {string} msg
	#+++++++++++++++++++++++++++++++++++++++
	def print_stderr (self, msg):
		pos = self.console.positionFromLineIndex(self.console.lines()-1, 0)
		self.__print(msg)
		# change color
		pos2 = self.console.positionFromLineIndex(self.console.lines()-1, 0)
		self.console.SendScintilla(SCI.SCI_STARTSTYLING, pos, SCI.STYLE_STDERR)
		self.console.SendScintilla(SCI.SCI_SETSTYLING, pos2-pos, SCI.STYLE_STDERR)

	#+++++++++++++++++++++++++++++++++++++++
	#
	#+++++++++++++++++++++++++++++++++++++++
	def slot_load (self):
		files, _ = QFileDialog.getOpenFileNames(self, 'Load Python Scripts', '', 'Python Files (*.py *.pyw);;All Files (*.*)')
		for fn in files:
			ok = self.load_script(fn)

	#+++++++++++++++++++++++++++++++++++++++
	#
	#+++++++++++++++++++++++++++++++++++++++
	def slot_save (self):
		try:
			with open(self.__filename, 'wb') as f:
				f.write(self.editor.text().encode(self.__encoding))
			self.editor.setModified(False)
			self.update_outline()
			return True
		except:
			return False

	#+++++++++++++++++++++++++++++++++++++++
	#
	#+++++++++++++++++++++++++++++++++++++++
	def slot_toggle_debug (self, flag):
		if flag:
			self.run()
		else:
			self.stop()

	#+++++++++++++++++++++++++++++++++++++++
	#
	#+++++++++++++++++++++++++++++++++++++++
	def run (self):
		# auto-save?
		if self.editor.isModified():
			self.slot_save()

		self.console.clear()
		self.editor.setReadOnly(True)
		qenv = QProcessEnvironment.systemEnvironment()
		qenv.insert('PYTHONPATH', PATH)
		self.__proc.setProcessEnvironment(qenv)
		self.__proc.setWorkingDirectory(os.path.dirname(os.path.realpath(self.__filename))) # ???
		args = ['-u', '-m', 'jsonpdb', self.__filename] # -cc
		self.__proc.start(sys.executable, args)

		# set breakpoints (for current file and others)
		for row in range(self.listWidgetBreakpoints.count()):
			list_item = self.listWidgetBreakpoints.item(row)
			linenum = list_item.data(Qt.UserRole+1)
			self.__proc.write(('b '+self.__filename+':'+str(linenum+1)+'\n').encode(PROC_ENCODING))
		for fn,lns in self.__saved_breakpoints.items():
			if fn == self.__filename:
				continue
			for ln in lns:
				self.__proc.write(('b '+fn+':'+str(ln+1)+'\n').encode(PROC_ENCODING))

		self.__dbg_running = True
		self.update_ui()
		self.update_vars_and_stack()

	#+++++++++++++++++++++++++++++++++++++++
	#
	#+++++++++++++++++++++++++++++++++++++++
	def stop (self):
		self.__proc.kill()
		self.editor.setReadOnly(False)
		self.__dbg_running = False
		self.update_ui()

	#+++++++++++++++++++++++++++++++++++++++
	#
	#+++++++++++++++++++++++++++++++++++++++
	def load_script (self, fn):
		fn = os.path.realpath(fn).lower() # normalize
		try:
			data = open(fn, 'rb').read()
		except:
			return False

		if self.comboBoxFiles.findText(fn)<0:
			self.comboBoxFiles.addItem(fn)

		self.comboBoxFiles.setCurrentText(fn)

		# if another script is already opened, save its breakpoints
		if self.__filename:
			self.__saved_breakpoints[self.__filename] = []
			for row in range(self.listWidgetBreakpoints.count()):
				list_item = self.listWidgetBreakpoints.item(row)
				linenum = list_item.data(Qt.UserRole+1)
				self.__saved_breakpoints[self.__filename].append(linenum)

		self.__filename = fn

		# guess encoding
		self.__encoding = self.__get_encoding(data)
		self.editor.setUtf8(True)
		s = data.decode(self.__encoding, 'ignore')

		self.setWindowTitle('qpdb - '+os.path.basename(self.__filename)+'[*]')

		self.editor.textChanged.disconnect(self.slot_text_changed)
		self.editor.setText(s) # triggers textChanged
		self.editor.textChanged.connect(self.slot_text_changed)

		self.listWidgetBreakpoints.clear()

		# restore saved breakpoints
		if self.__filename in self.__saved_breakpoints:
			for linenum in self.__saved_breakpoints[self.__filename]:
				marker_handle = self.editor.markerAdd(linenum, self.__breakpoint_marker_num)

				# add new breakpoint to breakpoints pane
				list_item = QListWidgetItem()
				list_item.setText('Breakpoint '+str(linenum+1).zfill(4))
				list_item.setData(Qt.UserRole, marker_handle)
				list_item.setData(Qt.UserRole+1, linenum)
				self.listWidgetBreakpoints.addItem(list_item)
		else:
			self.__saved_breakpoints[self.__filename] = []

		# guess eolMode
		eolMode = self.__get_eol_mode(s)
		self.editor.setEolMode(eolMode)

		self.editor.setModified(False)

		self.update_ui()
		self.update_outline()
		self.update_status_info()

		return True

	#+++++++++++++++++++++++++++++++++++++++
	#
	#+++++++++++++++++++++++++++++++++++++++
	def slot_close (self):
		if self.__dbg_running:
			self.stop()

		if not self.maybe_save():
			return

		self.editor.clear()
		self.console.clear()
		self.comboBoxFiles.clear()
		self.outline.clear()
		self.listWidgetBreakpoints.clear()

		self.__filename = None
		self.editor.setModified(False)
		self.setWindowTitle('qpdb')
		self.update_ui()
		self.labelInfo.clear()

		self.__saved_breakpoints = {}

	#+++++++++++++++++++++++++++++++++++++++
	#
	#+++++++++++++++++++++++++++++++++++++++
	def slot_stdout (self):
		res = self.__proc.readAllStandardOutput().data().decode(PROC_ENCODING, 'ignore')
		chunks = res.split('(Pdb) ')
		cnt = len(chunks)
		if cnt==1: # no (Pdb) found
			self.__last_chunk += chunks[0]
		else:
			self.__handle_chunk(self.__last_chunk + chunks[0])
			self.__last_chunk = ''
			if cnt>2:
				for i in range(1, cnt-1):
					self.__handle_chunk(chunks[i])
			self.__last_chunk = chunks[cnt-1]

	#+++++++++++++++++++++++++++++++++++++++
	#
	#+++++++++++++++++++++++++++++++++++++++
	def slot_stderr (self):
		res = self.__proc.readAllStandardError().data().decode(PROC_ENCODING, 'ignore')
		#test: remove debugger internals from Traceback
		if res[:7] == '  File ':
			lines = res.split('\r\n')
			res = '\r\n'.join(lines[7:])
		self.print_stderr(res)

	#+++++++++++++++++++++++++++++++++++++++
	#
	#+++++++++++++++++++++++++++++++++++++++
	def slot_complete (self):
		self.__print('Execution finished.\n')

		self.actionDebug.setChecked(False)

		# remove active line marker
		self.editor.markerDeleteAll(self.__active_line_marker_num)

		# unlock UI
		self.editor.setReadOnly(False)

		# clear stack and var panes?
		self.treeWidgetLocals.clear()
		self.treeWidgetGlobals.clear()
		self.treeWidgetStack.clear()

		self.__dbg_running = False

	#+++++++++++++++++++++++++++++++++++++++
	#
	#+++++++++++++++++++++++++++++++++++++++
	def __handle_chunk (self, msg):
		lines = msg.split('\r\n')
		for i in range(len(lines)):
			l = lines[i]
			if l == '':
				continue

			m = re.match(self.__re_active, l)
			if m is not None:
				fn = m.group(1)
				ln = int(m.group(2))
				#print('ACTIVE LINE', fn, ln)

				# <frozen importlib._bootstrap>
				if fn[0] == '<':
					continue

				self.editor.markerDeleteAll(self.__active_line_marker_num)

				if fn != self.__filename:
					#print('EXTERNAL FILE', fn)
					self.load_script(fn)
					self.editor.setReadOnly(True)

				self.editor.markerAdd(ln-1, self.__active_line_marker_num)
				continue

			# breakpoints
			if l[:21]=='Clear all breaks? ...':
				continue

			# Breakpoint 1 at d:\data\projekte\python_debug\dbg_test.py:17
			m = re.match(self.__re_bp_add, l)
			if m is not None:
				num = m.group(1)
				fn = m.group(2)
				ln = int(m.group(3))
				#print('BREAKPOINT ADD', num, fn, ln)
				continue

			# Deleted breakpoint 2 at d:\data\projekte\python_debug\dbg_test.py:22
			m = re.match(self.__re_bp_del, l)
			if m is not None:
				num = m.group(1)
				fn = m.group(2)
				#print('BREAKPOINT DEL', num, fn)
				continue

			# check for vars update
			if l.startswith('__ENV__:'):
				try:
					env = json.loads(l[8:])
				except Exception as e:
					print(e)
					env = None
				if type(env) is dict:
					self.treeWidgetLocals.clear()
					root_item = self.treeWidgetLocals.invisibleRootItem()
					for var_name,data in env['locals'][1].items():
						var_type = data[0]
						var_value = data[1]
						self.add_var_item(root_item, var_name, var_type, var_value)

					self.treeWidgetGlobals.clear()
					root_item = self.treeWidgetGlobals.invisibleRootItem()
					for var_name,data in env['globals'][1].items():
						var_type = data[0]
						var_value = data[1]
						self.add_var_item(root_item, var_name, var_type, var_value)
				continue

			# check for stack update
			if l[:2] == '  ':
				self.treeWidgetStack.clear()
				root_item = self.treeWidgetStack.invisibleRootItem()

				for j in range(i+1,len(lines)):
					l = lines[j]
					if l[:3] == '-> ':
						continue

					m = re.match(self.__re_stack, l)
					if m is not None:
						fn = m.group(1)
						if fn.startswith('<'):
							continue # only files

						# ignore pdb related files
						if os.path.basename(fn) in ['pdb.py','bdb.py','jsonpdb.py']:
							continue

						ln = m.group(2)
						func = m.group(3)

						tree_item = QTreeWidgetItem()
						tree_item.setText(0, os.path.basename(fn))
						tree_item.setText(1, ln)
						tree_item.setText(2, func)
						tree_item.setToolTip(0, fn)
						root_item.addChild(tree_item)

				cnt = self.treeWidgetStack.topLevelItemCount()
				if cnt>0:
					self.treeWidgetStack.setCurrentItem( self.treeWidgetStack.topLevelItem(cnt-1) )
					self.treeWidgetStack.index = cnt-1
				break

			self.__print(l+'\n')

	#+++++++++++++++++++++++++++++++++++++++
	# Tries to detect the eolMode of the specified string
	# @param {string} str
	# @return {integer}
	#+++++++++++++++++++++++++++++++++++++++
	def __get_eol_mode (self, s):
		if '\r' in s and not '\n' in s:
			return 1 # EolMac
		if '\n' in s and not '\r' in s:
			return 2 # EolUnix
		return 0 # EolWindows

	#+++++++++++++++++++++++++++++++++++++++
	# Tries to detect the encoding of the specified bytearray
	# @param {bytearray} data
	# @return {string} 'UTF-8', 'UTF-16', 'UTF-16BE' or 'Windows-1252'
	#+++++++++++++++++++++++++++++++++++++++
	def __get_encoding (self, data):
		# either 'utf-8', 'utf-16', 'utf-16be' or False
		bom = has_bom(data)
		if bom:
			return bom
		# utf-16be without BOM?
		if len(data)>0 and data[0]==0:
			return 'UTF-16BE'
		# utf-16 (le) without BOM?
		if len(data)>1 and data[1]==0:
			return 'UTF-16'
		# valid UTF-8?
		if is_utf8(data):
			return 'UTF-8'
		# default: return ANSI (windows-1252)
		return 'Windows-1252'

	#+++++++++++++++++++++++++++++++++++++++
	#
	#+++++++++++++++++++++++++++++++++++++++
	def slot_step_into (self):
		self.__proc.write('s\n'.encode(PROC_ENCODING)) # step
		self.update_vars_and_stack()

	#+++++++++++++++++++++++++++++++++++++++
	#
	#+++++++++++++++++++++++++++++++++++++++
	def slot_step_over (self):
		self.__proc.write('n\n'.encode(PROC_ENCODING)) # next
		self.update_vars_and_stack()

	#+++++++++++++++++++++++++++++++++++++++
	#
	#+++++++++++++++++++++++++++++++++++++++
	def slot_step_out (self):
		self.__proc.write('r\n'.encode(PROC_ENCODING)) # return
		self.update_vars_and_stack()

	#+++++++++++++++++++++++++++++++++++++++
	#
	#+++++++++++++++++++++++++++++++++++++++
	def slot_continue (self):
		self.__proc.write('c\n'.encode(PROC_ENCODING)) # continue
		self.update_vars_and_stack()

	#+++++++++++++++++++++++++++++++++++++++
	#
	#+++++++++++++++++++++++++++++++++++++++
	def slot_toggle_breakpoint (self):
		linenum, pos = self.editor.getCursorPosition()
		self.toggle_breakpoint(linenum)

	#+++++++++++++++++++++++++++++++++++++++
	#
	#+++++++++++++++++++++++++++++++++++++++
	def slot_clear_breakpoints (self):
		self.editor.markerDeleteAll(self.__breakpoint_marker_num)
		self.listWidgetBreakpoints.clear()
		self.__saved_breakpoints[self.__filename] = []
		if not self.__dbg_running:
			return
		self.__proc.write('cl\ny\n'.encode(PROC_ENCODING)) # asks for confirmation

	#+++++++++++++++++++++++++++++++++++++++
	#
	#+++++++++++++++++++++++++++++++++++++++
	def add_var_item (self, parentItem, var_name, var_type, var_value):
		tree_item = QTreeWidgetItem()
		tree_item.setText(0, var_name)
		tree_item.setText(1, var_type)
		if not (type(var_value) is dict or type(var_value) is list):
			s = str(var_value)
			if var_type == 'str':
				s = "'"+s.replace("'", "\\'")+"'"
			tree_item.setText(2, s)

		tree_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)

		parentItem.addChild(tree_item)
		if type(var_value) is dict:
			for k,data in var_value.items():
				self.add_var_item(tree_item, k, data[0], data[1])

		elif type(var_value) is list:
			for i in range(len(var_value)):
				self.add_var_item(tree_item, '['+str(i)+']', var_value[i][0], var_value[i][1])

		elif hasattr(var_value, '__dict__'):
			for k,data in var_value.__dict__.items():
				self.add_var_item(tree_item, k, data[0], data[1])

	#+++++++++++++++++++++++++++++++++++++++
	#
	#+++++++++++++++++++++++++++++++++++++++
	def update_vars_and_stack (self):
		self.update_vars()
		self.update_stack()

	#+++++++++++++++++++++++++++++++++++++++
	#
	#+++++++++++++++++++++++++++++++++++++++
	def update_vars (self):
		self.__proc.write('dump\n'.encode(PROC_ENCODING))

	#+++++++++++++++++++++++++++++++++++++++
	#
	#+++++++++++++++++++++++++++++++++++++++
	def update_stack (self):
		self.__proc.write('w\n'.encode(PROC_ENCODING)) # where

	#+++++++++++++++++++++++++++++++++++++++
	#
	#+++++++++++++++++++++++++++++++++++++++
	def update_ui (self):
		self.actionLoad.setEnabled(not self.__dbg_running)
		self.actionClose.setEnabled(self.__filename is not None)

		self.actionDebug.setChecked(self.__dbg_running)
		self.actionDebug.setEnabled(self.__filename is not None)

		self.actionContinue.setEnabled(self.__dbg_running)
		self.actionStepInto.setEnabled(self.__dbg_running)
		self.actionStepOver.setEnabled(self.__dbg_running)
		self.actionStepOut.setEnabled(self.__dbg_running)

		self.actionToggleBreakpoint.setEnabled(self.__filename is not None)
		self.actionClearBreakpoints.setEnabled(self.__filename is not None)

		self.menuEdit.setEnabled(not self.__dbg_running)

	#+++++++++++++++++++++++++++++++++++++++
	# @callback
	#+++++++++++++++++++++++++++++++++++++++
	def slot_margin_clicked (self, marg, linenum, keyState):
		if marg != self.__symbol_margin_num or linenum<0:
			return
		self.toggle_breakpoint(linenum)

	#+++++++++++++++++++++++++++++++++++++++
	#
	#+++++++++++++++++++++++++++++++++++++++
	def toggle_breakpoint (self, linenum):
		mask = self.editor.markersAtLine(linenum)
		if mask & self.__breakpoint_marker_mask: # line has a breakpoint, so remove it

			# find the marker_handle
			for row in range(self.listWidgetBreakpoints.count()):
				list_item = self.listWidgetBreakpoints.item(row)
				if list_item.data(Qt.UserRole+1) == linenum:
					marker_handle = list_item.data(Qt.UserRole)
					# remove breakpoint item
					self.listWidgetBreakpoints.takeItem(row)
					break

			# delete the marker
			self.editor.markerDelete(linenum, self.__breakpoint_marker_num)

			if self.__dbg_running:
				self.__proc.write(('cl '+self.__filename+':'+str(linenum+1)+'\n').encode(PROC_ENCODING))


		else: # line has no breakpoint, so add a new one

			# check if valid position
			s = self.editor.text(linenum).strip()
			if s == '' or s[0] == '#':
				return False

			marker_handle = self.editor.markerAdd(linenum, self.__breakpoint_marker_num)

			# add new breakpoint to breakpoints pane
			list_item = QListWidgetItem()
			list_item.setText('Breakpoint '+str(linenum+1).zfill(4))
			list_item.setData(Qt.UserRole, marker_handle)
			list_item.setData(Qt.UserRole+1, linenum)
			self.listWidgetBreakpoints.addItem(list_item)
			self.listWidgetBreakpoints.sortItems()

			if self.__dbg_running:
				self.__proc.write(('b '+self.__filename+':'+str(linenum+1)+'\n').encode(PROC_ENCODING))

		return True

	#+++++++++++++++++++++++++++++++++++++++
	# problem: scintilla (by editing text) allows to have multiple markers on same line!
	#+++++++++++++++++++++++++++++++++++++++
	def slot_text_changed (self):
		for row in range(self.listWidgetBreakpoints.count()-1, -1, -1):
			list_item = self.listWidgetBreakpoints.item(row)
			marker_handle = list_item.data(Qt.UserRole)
			linenum = self.editor.markerLine(marker_handle)

			if linenum < 0: # marker was deleted, remove list_item
				self.listWidgetBreakpoints.takeItem(row)

			else:
				linenum_saved = list_item.data(Qt.UserRole+1)
				if linenum_saved != linenum:
					list_item.setData(Qt.UserRole+1, linenum)
					# update breakpoint name
					list_item.setText('Breakpoint '+str(linenum+1).zfill(4))
		self.update_status_info()

	#+++++++++++++++++++++++++++++++++++++++
	#
	#+++++++++++++++++++++++++++++++++++++++
	def slot_editor_modification_changed (self, changed):
		self.setWindowModified(changed)
		self.actionSave.setEnabled(changed)
		self.actionUndo.setEnabled(self.editor.isUndoAvailable())
		self.actionRedo.setEnabled(self.editor.isRedoAvailable())

	#+++++++++++++++++++++++++++++++++++++++
	#
	#+++++++++++++++++++++++++++++++++++++++
	def slot_var_item_double_clicked (self, tree_item, column):
		if not self.__dbg_running or column != 2:
			return
		if tree_item.text(1) in ('str', 'int', 'float'):
			self.sender().editItem(tree_item, 2)

	#+++++++++++++++++++++++++++++++++++++++
	#
	#+++++++++++++++++++++++++++++++++++++++
	def slot_var_item_changed (self, tree_item, col):
		var_name = tree_item.text(0)
		var_value = tree_item.text(2)
		l = []
		t = []
		while tree_item.parent() is not None:
			tree_item = tree_item.parent()
			l.insert(0, tree_item.text(0))
			t.insert(0, tree_item.text(1))
		if len(l):
			s = l[0]
			for i in range(1, len(l)):
				if t[i -1] == 'list':
					s += l[i]
				elif t[i -1] == 'dict':
					s+= '[\''+l[i]+'\']'
				else:
					s += '.'+l[i] # other object
			if t[-1] == 'list':
				s += var_name
			elif t[-1] == 'dict':
				s += '[\''+var_name+'\']'
			else:
				s += '.'+var_name # other object
			self.__proc.write(('!'+s+'='+var_value+'\n').encode(PROC_ENCODING))
		else:
			self.__proc.write(('!'+var_name+'='+var_value+'\n').encode(PROC_ENCODING)) # next

	#+++++++++++++++++++++++++++++++++++++++
	#
	#+++++++++++++++++++++++++++++++++++++++
	def slot_stack_item_clicked (self, tree_item, column):
		index = self.treeWidgetStack.indexFromItem(tree_item).row()
		if index == self.treeWidgetStack.index:
			return
		df = index - self.treeWidgetStack.index
		self.treeWidgetStack.index = index
		if df<0:
			command = 'u '+str(-df)+'\n' # up
		else:
			command = 'd '+str(df)+'\n' # down
		self.__proc.write(command.encode(PROC_ENCODING))
		self.update_vars()

	#+++++++++++++++++++++++++++++++++++++++
	#
	#+++++++++++++++++++++++++++++++++++++++
	def slot_breakpoint_list_clicked (self, modelIndex):
		marker_handle = self.listWidgetBreakpoints.item(modelIndex.row()).data(Qt.UserRole)
		# get line index
		linenum = self.editor.markerLine(marker_handle)
		# goto line
		self.editor.ensureLineVisible(linenum)
		self.editor.setCursorPosition(linenum, 0)
		self.editor.setFocus(Qt.MouseFocusReason)

	#+++++++++++++++++++++++++++++++++++++++
	# Updates the class/function list
	#+++++++++++++++++++++++++++++++++++++++
	def update_outline (self):
		self.outline.clear()
		if self.__filename is None:
			return
		args = ['-n', '-f', '-', self.__filename]
		proc = QProcess()
		proc.start(PATH + '/resources/bin/ctags.exe', args)
		if not proc.waitForFinished():
			return False
		res = proc.readAll().data().decode('utf-8', 'ignore')
		res = self.__parse_ctags(res)

		root_item = self.outline.invisibleRootItem()

		classMap = {}

		# classes
		for c in res['classes']:
			tree_item = QTreeWidgetItem()
			tree_item.setText(0, c[0])
			tree_item.setIcon(0, self.__class_icon)
			tree_item.setData(0, Qt.UserRole, c[1])
			root_item.addChild(tree_item)
			tree_item.setExpanded(True)
			classMap[c[0]] = tree_item

		# functions
		for f in res['functions']:
			tree_item = QTreeWidgetItem()
			tree_item.setText(0, f[0])
			tree_item.setIcon(0, self.__func_icon)
			tree_item.setData(0, Qt.UserRole, f[1])
			root_item.addChild(tree_item)

		# methods
		for m in res['methods']:
			tree_item = QTreeWidgetItem()
			tree_item.setText(0, m[0])
			tree_item.setIcon(0, self.__meth_icon)
			tree_item.setData(0, Qt.UserRole, m[1])
			parent_item = classMap[m[2]]
			parent_item.addChild(tree_item)

	#+++++++++++++++++++++++++++++++++++++++
	#
	#+++++++++++++++++++++++++++++++++++++++
	def __parse_ctags (self, s):
		lines = s.split('\r\n')
		lines.pop()
		res = {}
		res['classes'] = []
		res['functions'] = []
		res['methods'] = []
		for l in lines:
			parts = l.split('\t')
			tag_type = parts[3]
			if tag_type != 'c' and tag_type != 'm' and tag_type != 'f': continue
			ln = parts[2]
			ln = int(ln[:len(ln)-2])
			if tag_type == 'c':
				res['classes'].append([parts[0], ln]) # name, ln
			elif tag_type == 'f':
				res['functions'].append([parts[0], ln])
			elif tag_type == 'm':
				cn = parts[4]
				res['methods'].append([parts[0], ln, cn[6:]])
		return res

	#+++++++++++++++++++++++++++++++++++++++
	#
	#+++++++++++++++++++++++++++++++++++++++
	def slot_outline_clicked (self, tree_item, _):
		linenum = tree_item.data(0, Qt.UserRole) - 1
		self.editor.ensureLineVisible(linenum)
		self.editor.setCursorPosition(linenum, 0)
		self.editor.setFocus(Qt.MouseFocusReason)

	#+++++++++++++++++++++++++++++++++++++++
	# Comments out current selection
	#+++++++++++++++++++++++++++++++++++++++
	def slot_comment (self):
		# get full line selection
		line_from, index_from, line_to, index_to = self.editor.getSelection()

		eol = ['\r\n', '\r', '\n'][self.editor.eolMode()]
		last_line = self.editor.text(line_to)
		self.editor.setSelection(line_from, 0, line_to, len(last_line)-len(eol))

		# replace
		s = self.editor.selectedText()
		lines = s.split(eol)
		for i in range(len(lines)):
			lines[i] = '#' + lines[i]

		s = eol.join(lines)
		self.editor.replaceSelectedText(s)

		# reset selection
		last_line = self.editor.text(line_to)
		self.editor.setSelection(line_from, 0, line_to, len(last_line)-len(eol))

	#+++++++++++++++++++++++++++++++++++++++
	# Uncomments current selection
	#+++++++++++++++++++++++++++++++++++++++
	def slot_uncomment (self):
		# get full line selection
		line_from, index_from, line_to, index_to = self.editor.getSelection()
		eol = ['\r\n', '\r', '\n'][self.editor.eolMode()]
		last_line = self.editor.text(line_to)
		self.editor.setSelection(line_from, 0, line_to, len(last_line)-len(eol))

		# replace
		s = self.editor.selectedText()
		lines = s.split(eol)
		for i in range(len(lines)):
			if lines[i].startswith('#'):
				lines[i] = lines[i][1:]

		s = eol.join(lines)
		self.editor.replaceSelectedText(s)

		# reset selection
		last_line = self.editor.text(line_to)
		self.editor.setSelection(line_from, 0, line_to, len(last_line)-len(eol))

	#+++++++++++++++++++++++++++++++++++++++
	#
	#+++++++++++++++++++++++++++++++++++++++
	def slot_help_chm (self, chm_file):
		s = self.editor.selectedText()
		if s == '':
			args = ['-DirHelp', chm_file]
		else:
			args = ['-#klink', s, '-DirHelp', chm_file]
		proc = QProcess()
		proc.setProgram(PATH + '/resources/bin/KeyHH.exe')
		proc.setArguments(args)
		proc.startDetached()

	#+++++++++++++++++++++++++++++++++++++++
	#
	#+++++++++++++++++++++++++++++++++++++++
	def slot_help_assistant (self):
		proc = QProcess(self)
		proc.setProgram(ASSISTANT_BIN)
		proc.setArguments(['-enableRemoteControl'])
		proc.start()
		if not proc.waitForStarted():
			QMessageBox.critical(self, 'Remote Control', 'Could not start Qt Assistant.')
			return
		s = self.editor.selectedText()
		if s != '':
			proc.write('show index;activateKeyword {}\n'.format(s).encode(PROC_ENCODING))

	#+++++++++++++++++++++++++++++++++++++++
	#
	#+++++++++++++++++++++++++++++++++++++++
	def slot_about (self):
		QMessageBox.about(self, 'About qpdb', '''
			<b>qpdb</b><br>(c) 2020 Valentin Schmidt<br><br>
			A simple graphical Python debugger and code editor<br>
			based on pdb, PyQt5 and Scintilla.
			''')

	#+++++++++++++++++++++++++++++++++++++++
	#
	#+++++++++++++++++++++++++++++++++++++++
	def slot_combobox_item_activated (self, fn):
		if not self.maybe_save():
			self.comboBoxFiles.setCurrentText(self.__filename)
			return
		self.load_script(fn)

#+++++++++++++++++++++++++++++++++++++++
#
#+++++++++++++++++++++++++++++++++++++++
def main ():
	app = QApplication(sys.argv)
	instance = Main()
	for i in range(1, len(sys.argv)):
		ok = instance.load_script(sys.argv[i])
	sys.exit(app.exec_())

if __name__ == '__main__':
	main()
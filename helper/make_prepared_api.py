import os
import sys
from PyQt5.QtWidgets import QApplication
from PyQt5.Qsci import QsciLexerPython, QsciAPIs

########################################
# You might add more files, e.g. 'PyQtWebEngine.api' etc., to folder
# api_raw and to this list
########################################
API_FILES = ['python3.api', 'PyQt5.api']

PATH = os.path.dirname(os.path.realpath(__file__))

class Main ():

	def __init__(self):
		self.lexer = QsciLexerPython()
		self.apis = QsciAPIs(self.lexer)
		self.lexer.setAPIs(self.apis)
		self.apis.apiPreparationFinished.connect(self.slotApiPreparationFinished)
		for fn in API_FILES:
			ok = self.apis.load(PATH + '/api_raw/' + fn)
		self.apis.prepare()

	def slotApiPreparationFinished (self):
		self.apis.savePrepared(PATH + '/prepared.api')
		print('Done.')
		QApplication.quit()

if __name__ == '__main__':
	app = QApplication(sys.argv)
	main = Main()
	sys.exit(app.exec_())

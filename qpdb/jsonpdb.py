import os
import json
import sys
import traceback
from pdb import Pdb, Restart

def dump(obj, stack):
	if type(obj) in (int, float, bool):
		return (type(obj).__name__, str(obj))

	if obj in stack:
		return [type(obj).__name__+' <recursion>', str(obj)]

	if type(obj).__name__=='module':
		return ('module', str(obj))

	if type(obj)==dict:
		stack.append(obj)
		return ('dict', {k: dump(v, stack) for k, v in obj.items()})

	if type(obj)==list:
		stack.append(obj)
		return ('list', [dump(v, stack) for v in obj])

	if hasattr(obj, '__dict__'):
		stack.append(obj)
		return (type(obj).__name__, {k: dump(v, stack) for k, v in obj.__dict__.items()})

	return (type(obj).__name__, str(obj))

class JsonPdb(Pdb):
	def do_dump(self, arg):
		env = {
			'locals': dump(self._getval('locals()'), []),
			'globals': dump(self._getval('globals()'), [])
		}
		s = json.dumps(env)
		self.message('__ENV__:'+s)

def main():
	import getopt

	opts, args = getopt.getopt(sys.argv[1:], 'mhc:', ['--help', '--command='])

	if not args:
		print(_usage)
		sys.exit(2)

	commands = []
	run_as_module = False
	for opt, optarg in opts:
		if opt in ['-h', '--help']:
			print(_usage)
			sys.exit()
		elif opt in ['-c', '--command']:
			commands.append(optarg)
		elif opt in ['-m']:
			run_as_module = True

	mainpyfile = args[0]	 # Get script filename
	if not run_as_module and not os.path.exists(mainpyfile):
		print('Error:', mainpyfile, 'does not exist')
		sys.exit(1)

	sys.argv[:] = args # Hide "pdb.py" and pdb options from argument list

	# Replace pdb's dir with script's dir in front of module search path.
	if not run_as_module:
		sys.path[0] = os.path.dirname(mainpyfile)

	# Note on saving/restoring sys.argv: it's a good idea when sys.argv was
	# modified by the script being debugged. It's a bad idea when it was
	# changed by the user from the command line. There is a "restart" command
	# which allows explicit specification of command line arguments.
	pdb = JsonPdb()
	pdb.use_rawinput = 0 # VS
	pdb.rcLines.extend(commands)
	while True:
		try:
			if run_as_module:
				pdb._runmodule(mainpyfile)
			else:
				pdb._runscript(mainpyfile)
			if pdb._user_requested_quit:
				break
			print("The program finished and will be restarted")
		except Restart:
			print("Restarting", mainpyfile, "with arguments:")
			print("\t" + " ".join(args))
		except SystemExit:
			break
		except SyntaxError:
			traceback.print_exc()
			sys.exit(1)
		except:
			traceback.print_exc()
			print("Uncaught exception. Entering post mortem debugging")
			print("Running 'cont' or 'step' will restart the program")
			t = sys.exc_info()[2]
			pdb.interaction(None, t)
			print("Post mortem debugger finished. The " + mainpyfile +
				  " will be restarted")

# When invoked as main program, invoke the debugger on a script
if __name__ == '__main__':
	import jsonpdb
	jsonpdb.main()

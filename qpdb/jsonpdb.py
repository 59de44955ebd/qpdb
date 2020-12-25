"""
@file qpdb - jsonpdb script
@author Valentin Schmidt
"""

from pdb import Pdb
import json
import os
import sys
import traceback


def _dump(obj, stack):
    if isinstance(obj, (int, float, bool)):
        return (type(obj).__name__, str(obj))
    try:
        if obj in stack:
            return [type(obj).__name__+' <recursion>', str(obj)]

        if type(obj).__name__=='module':
            return ('module', str(obj))

        if isinstance(obj, dict):
            stack.append(obj)
            return ('dict', {k: _dump(v, stack) for k, v in obj.items()})

        if isinstance(obj, list):
            stack.append(obj)
            return ('list', [_dump(v, stack) for v in obj])

        if hasattr(obj, '__dict__'):
            stack.append(obj)
            return (type(obj).__name__, {k: _dump(v, stack) for k, v in obj.__dict__.items()})
    except:
        pass

    return (type(obj).__name__, str(obj))


class JsonPdb(Pdb):
    """ Extends Pdb with JSON output. """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.use_rawinput = 0

    def do_dump(self, _):
        env = {
            'locals': _dump(self._getval('locals()'), []),
            'globals': _dump(self._getval('globals()'), [])
        }
        s = json.dumps(env)
        self.message('__ENV__:'+s)


def main():
    mainpyfile = sys.argv[1]
    if not os.path.exists(mainpyfile):
        print('Error:', mainpyfile, 'does not exist')
        sys.exit(1)

    # Hide "jsonpdb.py" from argument list
    sys.argv[:] = sys.argv[1:]

    # Replace pdb's dir with script's dir in front of module search path.
    sys.path[0] = os.path.dirname(mainpyfile)

    # Note on saving/restoring sys.argv: it's a good idea when sys.argv was
    # modified by the script being debugged. It's a bad idea when it was
    # changed by the user from the command line. There is a "restart" command
    # which allows explicit specification of command line arguments.
    _pdb = JsonPdb()
    while True:
        try:
            _pdb._runscript(mainpyfile)
        except SystemExit:
            break
        except SyntaxError:
            traceback.print_exc()
            sys.exit(1)
        except:
            traceback.print_exc()
            print("Uncaught exception. Entering post mortem debugging")
            print("Running 'Continue' or 'Step...' will restart the program")
            t = sys.exc_info()[2]
            _pdb.interaction(None, t)
            print("Post mortem debugger finished. The " + mainpyfile +
                  " will be restarted")


# When invoked as main program, invoke the debugger on a script
if __name__ == '__main__':
    import jsonpdb
    jsonpdb.main()

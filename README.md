# qpdb
A simple visual Python 3 debugger and code editor based on pdb, PyQt5 and Scintilla

**Requirements**

pip install PyQt5 QScintilla intervaltree

**Usage**

* python -m qpdb
* python -m qpdb /path/to/foo.php
* python -m qpdb /path/to/foo.php /path/to/bar.php ...

**Features**

* One or multiple scripts can be loaded, either by:
    * passing it/them as commandline arguments
    * loading it/them via menu->File->Load...
    * dropping it/them into the application window
* If multiple files are loaded, you can use a combobox in the toolbar to switch between them and then add breakpoints
* You can add/remove breakpoints either by toolbar button or by clicking into column on the right of the linenumbers
* Current breakpoints are listed in the "Breakpoints" pane, clicking on a breakpont jumps to the corresponding code line
* Simple data types (str, int, float, bool) can be edited at runtime in the "Locals"/"Globals" panes
* You can jump to stack frames by clicking on the corresponding line in the "Stack" pane
* Comment (Alt+C)/uncomment (Alt+U) current code selection
* Click-sensitive "Outline" pane showing classes, methods and top-level functions
* Context-sensitive help, arbitrary CHM help files can be integrated, as well as Qt Assistant

**Todo**

* Multithreading support
* Currently qpdb was only tested in Windows and macOS, but making it Linux compatible should be no big deal.
* Currently qpdb uses the same Python executable it was started with for debugging scripts, but different Python versions/virtual environments should be supported as well.
* I might add some additional features to turn it into a real Python code editor, e.g.:
    * Settings dialog that allows finetuning of the editor's behavior (e.g. concerning line wrapping, tabs etc.)
    * Search/Replace
    * Bookmarks
    * Interactive Python shell for quickly testing stuff inside the app
    * FullScreen and Distraction-Free edit modes
    * ...

**Screenshots**

* qpdb debugging its own script (Windows 8.1):

  ![](screenshots/qpdb_debugging.png)

* qpdb debugging its own script (macOS 10.15 Catalina):

  ![](screenshots/qpdb_debugging_macos.png)

* Autocomplete:

  ![](screenshots/qpdb_autocomplete.png)

* Calltip:

  ![](screenshots/qpdb_calltip.png)

* Context-sensitive help:

  ![](screenshots/qpdb_help.png)

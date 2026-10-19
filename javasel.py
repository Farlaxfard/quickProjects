#!/usr/bin/env python3
import os
import sys
import time
import subprocess
import re
import shutil
import threading
import curses
import base64
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# Excluded directory names to optimize scanning
EXCLUDED_DIR_NAMES = {
    'node_modules', '.git', '.cache', 'tmp', 'proc', 'sys', 'dev', 'run', 
    'var/lib/docker', '.cargo', '.rustup', '.npm', '.yarn', 'Library/Caches',
    '__pycache__', '.gradle', '.idea', '.vscode', '.metadata', 'target', 'build',
    '.steam', '.config', '.gemini', 'Games', 'Music', 'Pictures', 'Videos', 
    'Public', 'Templates', 'Arduino', 'Calibre Library', '.arduino15', '.arduinoIDE'
}

# Whitelisted hidden directories at the root of search that we should inspect
HIDDEN_WHITELIST = {
    '.sdkman', '.jenv', '.asdf', '.local', '.var', '.jdks'
}

# Global state
installations = []
searching = False
status_msg = ""
status_timer = 0

def get_java_details(java_home):
    """
    Extracts version, implementor/vendor, VM variant, type, and arch of a Java Home directory.
    Combines reading 'release' file and running 'java -XshowSettings:properties -version' as fallback.
    """
    details = {
        "path": java_home,
        "version": "Unknown",
        "vendor": "Unknown",
        "vm_name": "Unknown",
        "type": "Unknown",
        "arch": "Unknown",
        "has_compiler": False,
        "is_symlink": os.path.islink(java_home),
        "symlink_target": None
    }
    
    if details["is_symlink"]:
        try:
            details["symlink_target"] = os.path.realpath(java_home)
        except Exception:
            pass

    # Check if compiler exists
    javac_path = os.path.join(java_home, "bin", "javac")
    if os.path.exists(javac_path) and os.access(javac_path, os.X_OK):
        details["has_compiler"] = True
        details["type"] = "JDK"
    else:
        details["type"] = "JRE"

    # Try reading the release file first
    release_path = os.path.join(java_home, "release")
    if os.path.exists(release_path):
        try:
            with open(release_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            
            properties = {}
            for line in content.splitlines():
                if "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    properties[key] = val
            
            if "JAVA_VERSION" in properties:
                details["version"] = properties["JAVA_VERSION"]
            if "IMPLEMENTOR" in properties:
                details["vendor"] = properties["IMPLEMENTOR"]
            elif "IMPLEMENTOR_VERSION" in properties:
                details["vendor"] = properties["IMPLEMENTOR_VERSION"]
            
            if "JVM_VARIANT" in properties:
                details["vm_name"] = properties["JVM_VARIANT"]
            if "IMAGE_TYPE" in properties:
                details["type"] = properties["IMAGE_TYPE"]
            if "OS_ARCH" in properties:
                details["arch"] = properties["OS_ARCH"]
        except Exception:
            pass

    # If key details are still Unknown, run fallback subprocess setting check
    if (details["version"] == "Unknown" or 
        details["vendor"] == "Unknown" or 
        details["vm_name"] == "Unknown" or 
        details["arch"] == "Unknown"):
        
        java_exe = os.path.join(java_home, "bin", "java")
        if os.path.exists(java_exe) and os.access(java_exe, os.X_OK):
            try:
                # Runs java -XshowSettings:properties -version (settings print to stderr)
                proc = subprocess.run(
                    [java_exe, "-XshowSettings:properties", "-version"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=1.5
                )
                output = proc.stderr
                
                version_match = re.search(r"java\.version\s*=\s*(.*)", output)
                vendor_match = re.search(r"java\.vendor\s*=\s*(.*)", output)
                vm_match = re.search(r"java\.vm\.name\s*=\s*(.*)", output)
                arch_match = re.search(r"os\.arch\s*=\s*(.*)", output)
                
                if version_match and details["version"] == "Unknown":
                    details["version"] = version_match.group(1).strip()
                if vendor_match and details["vendor"] == "Unknown":
                    details["vendor"] = vendor_match.group(1).strip()
                if vm_match and details["vm_name"] == "Unknown":
                    details["vm_name"] = vm_match.group(1).strip()
                if arch_match and details["arch"] == "Unknown":
                    details["arch"] = arch_match.group(1).strip()
                    
                # Standard java -version backup parser
                if details["version"] == "Unknown":
                    version_line = re.search(r'version "([^"]+)"', output)
                    if version_line:
                        details["version"] = version_line.group(1)
            except Exception:
                pass
                
    return details


def scan_dir(path, max_depth, found_dirs):
    """
    Scans a directory path recursively up to max_depth for JAVA_HOMEs.
    Skips non-Java folders and sub-scans once a JVM installation is found.
    """
    try:
        # Check if java binary exists in bin/java
        java_bin = os.path.join(path, "bin", "java")
        if os.path.exists(java_bin) and os.access(java_bin, os.X_OK):
            found_dirs.add(os.path.abspath(path))
            return  # Found JDK/JRE - don't scan deeper under this path!
            
        if max_depth <= 0:
            return

        with os.scandir(path) as it:
            for entry in it:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        name = entry.name
                        # Exclude hidden unless whitelisted
                        if name.startswith('.'):
                            if name not in HIDDEN_WHITELIST:
                                continue
                        # Exclude standard directories
                        if name in EXCLUDED_DIR_NAMES:
                            continue
                        
                        scan_dir(entry.path, max_depth - 1, found_dirs)
                except PermissionError:
                    continue
                except Exception:
                    continue
    except PermissionError:
        pass
    except Exception:
        pass


def search_java_installations():
    """
    Finds all Java Home directories on the system.
    Scans standard paths, environment variables, PATH contents, and user home folders.
    Runs scans in parallel using thread executor.
    """
    found_dirs = set()
    
    # 1. Check current PATH
    path_env = os.environ.get("PATH", "")
    for p in path_env.split(os.pathsep):
        if not p:
            continue
        try:
            java_bin = os.path.join(p, "java")
            if os.path.exists(java_bin) and os.access(java_bin, os.X_OK):
                real_bin = os.path.realpath(java_bin)
                java_home = os.path.dirname(os.path.dirname(real_bin))
                if os.path.exists(os.path.join(java_home, "bin", "java")):
                    found_dirs.add(os.path.abspath(java_home))
        except Exception:
            pass
            
    # Check env variables
    for env_var in ["JAVA_HOME", "JDK_HOME"]:
        val = os.environ.get(env_var)
        if val and os.path.exists(os.path.join(val, "bin", "java")):
            found_dirs.add(os.path.abspath(val))

    # 2. Gather system and user directories to scan
    home_dir = str(Path.home())
    targets = [
        # (Path, MaxDepth)
        ("/usr/lib/jvm", 3),
        ("/usr/lib/jdk", 3),
        ("/usr/lib32/jvm", 3),
        ("/usr/lib64/jvm", 3),
        ("/usr/java", 3),
        ("/opt", 4),
        ("/usr/local", 4),
        ("/var/lib/flatpak", 4),
        (os.path.join(home_dir, ".sdkman", "candidates", "java"), 2),
        (os.path.join(home_dir, ".jdks"), 2),
        (os.path.join(home_dir, ".jenv", "versions"), 2),
        (os.path.join(home_dir, ".asdf", "installs", "java"), 3),
        (os.path.join(home_dir, ".local", "share", "jvm"), 2),
        (os.path.join(home_dir, ".local", "share", "java"), 2),
        (os.path.join(home_dir, ".local", "share", "JetBrains"), 5),
    ]

    # Dynamically scan user's non-hidden home directories
    try:
        with os.scandir(home_dir) as it:
            for entry in it:
                if entry.is_dir(follow_symlinks=False):
                    name = entry.name
                    if name.startswith('.'):
                        if name not in HIDDEN_WHITELIST:
                            continue
                    if name in EXCLUDED_DIR_NAMES:
                        continue
                    targets.append((entry.path, 3))
    except Exception:
        pass

    # Deduplicate and check directory existence
    valid_targets = []
    seen_paths = set()
    for path, depth in targets:
        abs_path = os.path.abspath(path)
        if abs_path not in seen_paths and os.path.exists(abs_path) and os.path.isdir(abs_path):
            valid_targets.append((abs_path, depth))
            seen_paths.add(abs_path)

    # Walk directories concurrently using thread pool
    with ThreadPoolExecutor(max_workers=min(len(valid_targets) + 1, 16)) as executor:
        futures = {executor.submit(scan_dir, path, depth, found_dirs): path for path, depth in valid_targets}
        for future in as_completed(futures):
            pass

    # Extract JVM properties in parallel
    results = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(get_java_details, path): path for path in found_dirs}
        for future in as_completed(futures):
            results.append(future.result())

    # Sort: Unique physical JDKs first, then JREs, sorted by version descending, symlinks last
    results.sort(key=lambda x: (not x["is_symlink"], x["type"] == "JDK", x["version"]), reverse=True)
    return results


def open_in_file_explorer(path):
    """
    Launches system file manager showing the target directory path.
    """
    try:
        if shutil.which("xdg-open"):
            subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        elif shutil.which("kde-open"):
            subprocess.Popen(["kde-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        elif shutil.which("gnome-open"):
            subprocess.Popen(["gnome-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        return False
    except Exception:
        return False


def copy_to_clipboard(text):
    """
    Copies text to system clipboard.
    Combines Wayland, X11 commands and OSC 52 terminal copy escapes.
    """
    success = False
    
    # 1. Try wl-copy (Wayland)
    if shutil.which("wl-copy"):
        try:
            p = subprocess.Popen(["wl-copy"], stdin=subprocess.PIPE)
            p.communicate(input=text.encode('utf-8'))
            success = True
        except Exception:
            pass

    # 2. Try xclip (X11)
    if not success and shutil.which("xclip"):
        try:
            p = subprocess.Popen(["xclip", "-selection", "clipboard"], stdin=subprocess.PIPE)
            p.communicate(input=text.encode('utf-8'))
            success = True
        except Exception:
            pass

    # 3. Try xsel (X11)
    if not success and shutil.which("xsel"):
        try:
            p = subprocess.Popen(["xsel", "--clipboard", "--input"], stdin=subprocess.PIPE)
            p.communicate(input=text.encode('utf-8'))
            success = True
        except Exception:
            pass

    # 4. Fallback to Terminal OSC 52 Copy (highly supported by modern terminals)
    try:
        b64_data = base64.b64encode(text.encode('utf-8')).decode('utf-8')
        sys.stdout.write(f"\x1b]52;c;{b64_data}\x07")
        sys.stdout.flush()
        success = True
    except Exception:
        pass

    return success


def update_shell_profile(profile_path, java_home):
    """
    Appends or updates the export JAVA_HOME block in user shell startup file.
    """
    profile_path = os.path.expanduser(profile_path)
    try:
        content = ""
        if os.path.exists(profile_path):
            with open(profile_path, "r", encoding="utf-8") as f:
                content = f.read()
        
        pattern = r"# >>> Java Environment Selector >>>.*?# <<< Java Environment Selector <<<\n?"
        replacement = (
            "# >>> Java Environment Selector >>>\n"
            f'export JAVA_HOME="{java_home}"\n'
            'export PATH="$JAVA_HOME/bin:$PATH"\n'
            "# <<< Java Environment Selector <<<\n"
        )
        
        if re.search(pattern, content, re.DOTALL):
            new_content = re.sub(pattern, replacement, content, flags=re.DOTALL)
        else:
            prefix = "\n" if content and not content.endswith("\n") else ""
            new_content = content + prefix + replacement
            
        with open(profile_path, "w", encoding="utf-8") as f:
            f.write(new_content)
        return True
    except Exception:
        return False


def launch_subshell(stdscr, java_home):
    """
    Temporarily exits curses to open a bash session preconfigured with selected JVM,
    then resumes curses on exit.
    """
    curses.endwin()
    
    print("\n" + "═" * 70)
    print(" ☕  JAVA ENVIRONMENT SUBSHELL ")
    print("═" * 70)
    print(f"JAVA_HOME set to  : {java_home}")
    print(f"PATH prefixed with: {java_home}/bin")
    print("Type 'exit' or Ctrl+D to return to the Java Environment Selector.")
    print("═" * 70 + "\n")
    
    env = os.environ.copy()
    env["JAVA_HOME"] = java_home
    env["PATH"] = os.path.join(java_home, "bin") + os.pathsep + env.get("PATH", "")
    
    try:
        subprocess.run(["bash"], env=env)
    except Exception as e:
        print(f"Error starting bash: {e}")
        input("Press Enter to continue...")
        
    stdscr.clear()
    stdscr.refresh()
    try:
        curses.curs_set(0)
    except Exception:
        pass


def test_java_version(java_home):
    """
    Runs the java -version command of the target JVM and returns output text.
    """
    java_exe = os.path.join(java_home, "bin", "java")
    if not (os.path.exists(java_exe) and os.access(java_exe, os.X_OK)):
        return ["Error: java binary not found or not executable!"]
    
    try:
        proc = subprocess.run(
            [java_exe, "-version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=3.0
        )
        output = proc.stderr if proc.stderr else proc.stdout
        return [f"Command: {java_exe} -version", ""] + output.splitlines()
    except subprocess.TimeoutExpired:
        return ["Error: Process timed out after 3 seconds."]
    except Exception as e:
        return [f"Error running java: {e}"]


def show_popup(stdscr, title, lines):
    """
    Displays a centered popup message modal.
    """
    height = min(len(lines) + 4, curses.LINES - 4)
    width = min(max(len(l) for l in lines) + 6, curses.COLS - 6)
    if height < 4 or width < 10:
        return
        
    start_y = (curses.LINES - height) // 2
    start_x = (curses.COLS - width) // 2
    
    win = curses.newwin(height, width, start_y, start_x)
    win.keypad(True)
    win.box()
    
    # Title
    win.addstr(0, 2, f" {title} ", curses.A_BOLD)
    
    # Content lines
    for idx, line in enumerate(lines[:height-4]):
        win.addstr(idx + 2, 3, line[:width-6])
        
    # Close instruction
    close_text = "Press any key to close"
    win.addstr(height - 2, (width - len(close_text)) // 2, close_text, curses.A_REVERSE)
    win.refresh()
    
    win.getch()
    del win
    stdscr.clear()
    stdscr.refresh()


def read_input_popup(stdscr, prompt):
    """
    Displays an input dialog popup.
    """
    height, width = 6, min(curses.COLS - 6, 80)
    if height < 4 or width < 15:
        return ""
    start_y = (curses.LINES - height) // 2
    start_x = (curses.COLS - width) // 2
    
    win = curses.newwin(height, width, start_y, start_x)
    win.keypad(True)
    
    try:
        curses.curs_set(1)  # Show cursor
    except Exception:
        pass
        
    input_str = ""
    while True:
        win.erase()
        win.box()
        win.addstr(1, 2, prompt[:width-4], curses.A_BOLD)
        
        # Draw input box border
        win.addstr(3, 2, input_str[-(width-6):])
        win.move(3, min(2 + len(input_str), width-4))
        win.refresh()
        
        ch = win.getch()
        if ch in (10, 13):  # Enter
            break
        elif ch == 27:  # ESC
            input_str = ""
            break
        elif ch in (curses.KEY_BACKSPACE, 127, 8):  # Backspace
            input_str = input_str[:-1]
        elif 32 <= ch <= 126:
            input_str += chr(ch)
            
    try:
        curses.curs_set(0)  # Hide cursor
    except Exception:
        pass
        
    del win
    stdscr.clear()
    stdscr.refresh()
    return input_str.strip()


def show_selection_popup(stdscr, title, prompt, options):
    """
    Renders a selection popup list. Arrow keys or numbers to select. Returns option index.
    """
    height = len(options) + 6
    width = max(len(prompt), max(len(o) for o in options)) + 8
    width = min(width, curses.COLS - 6)
    if height < 4 or width < 15:
        return -1
        
    start_y = (curses.LINES - height) // 2
    start_x = (curses.COLS - width) // 2
    
    win = curses.newwin(height, width, start_y, start_x)
    win.keypad(True)
    
    selected_idx = 0
    while True:
        win.erase()
        win.box()
        win.addstr(1, 2, title, curses.A_BOLD)
        win.addstr(2, 2, prompt, curses.A_DIM)
        
        for idx, option in enumerate(options):
            y = idx + 4
            if idx == selected_idx:
                win.addstr(y, 2, f" ➜ {option} ", curses.A_REVERSE | curses.A_BOLD)
            else:
                win.addstr(y, 2, f"   {option} ")
                
        win.refresh()
        
        ch = win.getch()
        if ch == curses.KEY_UP:
            selected_idx = (selected_idx - 1) % len(options)
        elif ch == curses.KEY_DOWN:
            selected_idx = (selected_idx + 1) % len(options)
        elif ch in (10, 13):  # Enter
            break
        elif ch == 27:  # ESC
            selected_idx = -1
            break
        elif ord('1') <= ch < ord('1') + len(options):
            selected_idx = ch - ord('1')
            break
            
    del win
    stdscr.clear()
    stdscr.refresh()
    return selected_idx


def create_layout():
    """
    Returns recalculated window layouts.
    """
    h_y, h_x = 0, 0
    h_h, h_w = 3, curses.COLS
    
    # Left pane (list)
    l_y, l_x = 3, 0
    l_h, l_w = curses.LINES - 6, curses.COLS // 2
    
    # Right pane (details)
    r_y, r_x = 3, curses.COLS // 2
    r_h, r_w = curses.LINES - 6, curses.COLS - (curses.COLS // 2)
    
    # Footer
    f_y, f_x = curses.LINES - 3, 0
    f_h, f_w = 3, curses.COLS
    
    header_win = curses.newwin(h_h, h_w, h_y, h_x)
    left_win = curses.newwin(l_h, l_w, l_y, l_x)
    right_win = curses.newwin(r_h, r_w, r_y, r_x)
    footer_win = curses.newwin(f_h, f_w, f_y, f_x)
    
    left_win.keypad(True)
    right_win.keypad(True)
    footer_win.keypad(True)
    
    return header_win, left_win, right_win, footer_win


def draw_details(win, w_h, w_w, item, has_colors):
    """
    Renders details pane for selected Java installation.
    """
    win.erase()
    win.box()
    
    # Color attribute helpers
    def attr(pair_id, style=0):
        if has_colors:
            return curses.color_pair(pair_id) | style
        return style

    win.addstr(0, 2, " Installation Details ", attr(7, curses.A_BOLD))
    
    if not item:
        win.addstr(w_h // 2, (w_w - 24) // 2, "No installation selected", attr(5))
        win.refresh()
        return
        
    y = 2
    
    def draw_kv(win, y, key, val, val_pair=8, bold_val=False):
        if y >= w_h - 2:
            return y
        win.addstr(y, 3, f"{key}:", attr(3, curses.A_BOLD))
        val_attr = attr(val_pair, curses.A_BOLD if bold_val else 0)
        max_val_w = w_w - len(key) - 6
        
        if len(val) > max_val_w:
            chunks = [val[i:i+max_val_w] for i in range(0, len(val), max_val_w)]
            for chunk in chunks[:4]: # Max 4 lines wrap
                if y >= w_h - 2:
                    break
                win.addstr(y, len(key) + 5, chunk, val_attr)
                y += 1
            return y
        else:
            win.addstr(y, len(key) + 5, val, val_attr)
            return y + 1

    y = draw_kv(win, y, "Vendor     ", item["vendor"])
    y = draw_kv(win, y, "Version    ", item["version"])
    
    type_pair = 4 if item["type"] == "JDK" else 5
    y = draw_kv(win, y, "Type       ", "JDK (Development Kit)" if item["type"] == "JDK" else "JRE (Runtime Only)", type_pair, True)
    
    comp_str = "Available (javac)" if item["has_compiler"] else "Missing / JRE Only"
    comp_pair = 4 if item["has_compiler"] else 5
    y = draw_kv(win, y, "Compiler   ", comp_str, comp_pair)
    
    y = draw_kv(win, y, "VM Name    ", item["vm_name"])
    y = draw_kv(win, y, "Arch       ", item["arch"])
    
    sym_val = "Yes" if item["is_symlink"] else "No"
    sym_pair = 5 if item["is_symlink"] else 8
    y = draw_kv(win, y, "Symlink    ", sym_val, sym_pair)
    
    if item["is_symlink"]:
        y = draw_kv(win, y, "  Target   ", item["symlink_target"] or "Unknown")
        
    y = draw_kv(win, y, "Path       ", item["path"])
    
    # Executable check
    bin_java = os.path.join(item["path"], "bin", "java")
    executable = os.path.exists(bin_java) and os.access(bin_java, os.X_OK)
    status_str = "✔ Tested & Executable" if executable else "✖ Broken / Missing Executable"
    status_pair = 4 if executable else 6
    y = draw_kv(win, y, "Status     ", status_str, status_pair, True)

    win.refresh()


def run_search_thread():
    """
    Runs background search to avoid locking up TUI.
    """
    global installations, searching, status_msg, status_timer
    try:
        new_list = search_java_installations()
        installations = new_list
        status_msg = f"Scan complete. Found {len(installations)} JVMs."
        status_timer = time.time()
    except Exception as e:
        status_msg = f"Scan failed: {e}"
        status_timer = time.time()
    finally:
        searching = False


def curses_main(stdscr):
    global installations, searching, status_msg, status_timer
    
    # 1. Colors and general curses setup
    has_colors = curses.has_colors()
    if has_colors:
        curses.start_color()
        curses.use_default_colors()
        
        # Color pairs definitions
        curses.init_pair(1, curses.COLOR_WHITE, curses.COLOR_BLUE)    # Header
        curses.init_pair(2, curses.COLOR_BLACK, curses.COLOR_CYAN)    # List active item
        curses.init_pair(3, curses.COLOR_CYAN, -1)                    # Label key
        curses.init_pair(4, curses.COLOR_GREEN, -1)                   # JDK/OK
        curses.init_pair(5, curses.COLOR_YELLOW, -1)                  # JRE/Warn
        curses.init_pair(6, curses.COLOR_RED, -1)                     # Error
        curses.init_pair(7, curses.COLOR_MAGENTA, -1)                 # Accent/Title
        curses.init_pair(8, curses.COLOR_WHITE, -1)                   # Neutral text
        curses.init_pair(9, curses.COLOR_BLUE, -1)                    # Borders
        
    def attr(pair_id, style=0):
        if has_colors:
            return curses.color_pair(pair_id) | style
        return style

    try:
        curses.curs_set(0) # Hide cursor
    except Exception:
        pass
        
    # Start initial search
    searching = True
    threading.Thread(target=run_search_thread, daemon=True).start()
    
    selected_idx = 0
    scroll_offset = 0
    
    # Spinner frames for loading
    spinner_frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    spinner_idx = 0
    
    header_win = left_win = right_win = footer_win = None
    
    while True:
        # Check window size
        if curses.LINES < 15 or curses.COLS < 65:
            stdscr.clear()
            stdscr.addstr(curses.LINES // 2, (curses.COLS - 24) // 2, "Terminal size too small!", attr(6, curses.A_BOLD))
            stdscr.addstr(curses.LINES // 2 + 1, (curses.COLS - 33) // 2, "Please expand the window size.", attr(5))
            stdscr.refresh()
            time.sleep(0.2)
            # Flush keys to prevent buffer issues
            stdscr.nodelay(True)
            stdscr.getch()
            stdscr.nodelay(False)
            continue
            
        if searching:
            # RENDER LOADING SCREEN
            stdscr.nodelay(True)
            stdscr.clear()
            stdscr.box()
            
            spinner = spinner_frames[spinner_idx % len(spinner_frames)]
            spinner_idx += 1
            
            # Draw titles
            title = "☕ JAVA ENVIRONMENT SELECTOR ☕"
            stdscr.addstr(2, (curses.COLS - len(title)) // 2, title, attr(1, curses.A_BOLD))
            
            scan_text = f"Searching filesystem for Java versions {spinner}"
            stdscr.addstr(curses.LINES // 2, (curses.COLS - len(scan_text)) // 2, scan_text, attr(3) | curses.A_BOLD)
            
            detail_text = "Running an optimized parallel search on system folders..."
            stdscr.addstr(curses.LINES // 2 + 2, (curses.COLS - len(detail_text)) // 2, detail_text, attr(8, curses.A_DIM))
            
            stdscr.refresh()
            
            # Check for abort/quit
            ch = stdscr.getch()
            if ch in (ord('q'), ord('Q'), 27):
                break
                
            time.sleep(0.08)
            continue
        
        # Finished searching, switch back to blocking input
        stdscr.nodelay(False)
        
        # Initialize windows if not created
        if header_win is None:
            header_win, left_win, right_win, footer_win = create_layout()
            
        # Clear windows
        header_win.erase()
        left_win.erase()
        right_win.erase()
        footer_win.erase()
        
        # 1. RENDER HEADER
        header_win.bkgd(" ", attr(1))
        title = "☕  JAVA ENVIRONMENT SELECTOR  "
        header_win.addstr(1, 2, title, attr(1, curses.A_BOLD))
        
        count_str = f"Found {len(installations)} JVMs"
        header_win.addstr(1, curses.COLS - len(count_str) - 2, count_str, attr(1, curses.A_BOLD))
        header_win.refresh()
        
        # Bounds check on selection index
        if len(installations) > 0:
            selected_idx = max(0, min(selected_idx, len(installations) - 1))
            selected_item = installations[selected_idx]
        else:
            selected_item = None
            
        # 2. RENDER LEFT WINDOW (INSTALLATIONS LIST)
        left_win.box()
        left_win.addstr(0, 2, " Installations ", attr(7, curses.A_BOLD))
        
        l_h, l_w = left_win.getmaxyx()
        max_visible = l_h - 2
        
        # Adjust scroll offset
        if selected_idx >= scroll_offset + max_visible:
            scroll_offset = selected_idx - max_visible + 1
        elif selected_idx < scroll_offset:
            scroll_offset = selected_idx
            
        if len(installations) == 0:
            left_win.addstr(l_h // 2, (l_w - 22) // 2, "No JVMs found.", attr(6, curses.A_BOLD))
            left_win.addstr(l_h // 2 + 1, (l_w - 26) // 2, "Press 'A' to add manually.", attr(8))
        else:
            for i in range(max_visible):
                idx = i + scroll_offset
                if idx >= len(installations):
                    break
                    
                item = installations[idx]
                y = i + 1
                
                # Check target file link representation
                bullet = "◆" if idx == selected_idx else "◇"
                type_tag = f"[{item['type']}]"
                vendor = item["vendor"]
                
                # Shorten/prettify vendor name
                if "Adoptium" in vendor or "Temurin" in vendor:
                    vendor = "Temurin"
                elif "Oracle" in vendor:
                    vendor = "Oracle"
                elif "Amazon" in vendor or "Corretto" in vendor:
                    vendor = "Corretto"
                elif "JetBrains" in vendor:
                    vendor = "JetBrains"
                elif len(vendor) > 10:
                    vendor = vendor[:10]
                    
                line_str = f" {bullet} {idx+1}. {vendor} {item['version']}"
                if item["is_symlink"]:
                    line_str += " ➜ (current)" if "current" in item["path"] else " ➜ (link)"
                    
                # Pad to align selection highlights
                padded_str = line_str.ljust(l_w - len(type_tag) - 3)[:l_w - len(type_tag) - 3] + f" {type_tag}"
                
                # Print item with selection background
                if idx == selected_idx:
                    left_win.addstr(y, 1, padded_str[:l_w-2], attr(2, curses.A_BOLD))
                else:
                    left_win.addstr(y, 1, padded_str[:l_w-2])
                    # Add type highlight if not selected
                    tag_pos = l_w - len(type_tag) - 2
                    tag_attr = attr(4) if item["type"] == "JDK" else attr(5)
                    left_win.addstr(y, tag_pos, type_tag, tag_attr)
                    
        left_win.refresh()
        
        # 3. RENDER RIGHT WINDOW (DETAILS)
        r_h, r_w = right_win.getmaxyx()
        draw_details(right_win, r_h, r_w, selected_item, has_colors)
        
        # 4. RENDER FOOTER
        footer_win.box()
        
        # Print status message if active
        if status_msg and (time.time() - status_timer < 4.0):
            # Clear line first
            footer_win.addstr(1, 2, " " * (curses.COLS - 4))
            footer_win.addstr(1, 2, status_msg[:curses.COLS-4], attr(4, curses.A_BOLD))
        else:
            # Print standard shortcuts
            shortcuts = [
                ("Enter", "Open"),
                ("C", "Copy"),
                ("E", "Export"),
                ("T", "Test"),
                ("S", "Shell"),
                ("A", "Add"),
                ("R", "Rescan"),
                ("Q", "Quit")
            ]
            x = 2
            for key, label in shortcuts:
                if x >= curses.COLS - 10:
                    break
                key_str = f"[{key}]"
                footer_win.addstr(1, x, key_str, attr(3, curses.A_BOLD))
                x += len(key_str) + 1
                footer_win.addstr(1, x, label)
                x += len(label) + 2
                
        footer_win.refresh()
        
        # 5. INPUT HANDLING
        ch = left_win.getch()
        
        if ch in (ord('q'), ord('Q'), 27): # Quit on Q or ESC
            break
            
        elif ch == curses.KEY_UP:
            if len(installations) > 0:
                selected_idx = (selected_idx - 1) % len(installations)
                
        elif ch == curses.KEY_DOWN:
            if len(installations) > 0:
                selected_idx = (selected_idx + 1) % len(installations)
                
        elif ch == curses.KEY_PPAGE: # Page Up
            selected_idx = max(0, selected_idx - max_visible)
            
        elif ch == curses.KEY_NPAGE: # Page Down
            selected_idx = min(max(0, len(installations) - 1), selected_idx + max_visible)
            
        elif ch in (10, 13): # Enter: Open directory in File explorer
            if selected_item:
                target_path = selected_item["path"]
                if open_in_file_explorer(target_path):
                    status_msg = f"Opened directory in file manager: {target_path}"
                else:
                    status_msg = f"Failed to open file manager. Path: {target_path}"
                status_timer = time.time()
                
        elif ch in (ord('c'), ord('C')): # Copy JAVA_HOME path
            if selected_item:
                target_path = selected_item["path"]
                if copy_to_clipboard(target_path):
                    status_msg = f"Copied path to clipboard!"
                else:
                    status_msg = f"Failed to set clipboard. Path: {target_path}"
                status_timer = time.time()
                
        elif ch in (ord('t'), ord('T')): # Test java version
            if selected_item:
                lines = test_java_version(selected_item["path"])
                show_popup(stdscr, f"Test Run Output: java -version", lines)
                # Cleanup windows
                header_win = left_win = right_win = footer_win = None
                
        elif ch in (ord('s'), ord('S')): # Launch subshell
            if selected_item:
                launch_subshell(stdscr, selected_item["path"])
                # Cleanup windows to force recreate
                header_win = left_win = right_win = footer_win = None
                
        elif ch in (ord('r'), ord('R')): # Rescan
            searching = True
            selected_idx = 0
            scroll_offset = 0
            header_win = left_win = right_win = footer_win = None
            threading.Thread(target=run_search_thread, daemon=True).start()
            
        elif ch in (ord('a'), ord('A')): # Add manual path
            manual_path = read_input_popup(stdscr, "Enter Java Home path (e.g. /usr/lib/jvm/java):")
            header_win = left_win = right_win = footer_win = None
            if manual_path:
                abs_manual = os.path.abspath(os.path.expanduser(manual_path))
                bin_java = os.path.join(abs_manual, "bin", "java")
                if os.path.exists(bin_java) and os.access(bin_java, os.X_OK):
                    # Verify if already exists
                    exists = False
                    for idx, inst in enumerate(installations):
                        if os.path.samefile(inst["path"], abs_manual) if os.path.exists(inst["path"]) else inst["path"] == abs_manual:
                            selected_idx = idx
                            exists = True
                            break
                    if not exists:
                        status_msg = "Checking Java details..."
                        details = get_java_details(abs_manual)
                        installations.insert(0, details)
                        selected_idx = 0
                        status_msg = f"Manually added Java installation: {abs_manual}"
                    else:
                        status_msg = "Java path already in installations list."
                else:
                    show_popup(stdscr, "Error", [
                        f"Invalid Java Home path: {abs_manual}",
                        "Could not find executable file at: <path>/bin/java",
                        "Please verify the directory exists and contains a valid bin/java."
                    ])
                status_timer = time.time()
                
        elif ch in (ord('e'), ord('E')): # Export environment popup
            if selected_item:
                opts = [
                    "Copy export commands to clipboard",
                    "Write local setup script (./use-java.sh)",
                    "Append to user startup config (~/.bashrc)",
                    "Append to user startup config (~/.zshrc)",
                    "Cancel"
                ]
                sel = show_selection_popup(
                    stdscr, 
                    "EXPORT OPTIONS", 
                    f"Configure {selected_item['vendor']} {selected_item['version']}:", 
                    opts
                )
                header_win = left_win = right_win = footer_win = None
                
                if sel == 0: # Copy to clipboard
                    cmd = f'export JAVA_HOME="{selected_item["path"]}" && export PATH="$JAVA_HOME/bin:$PATH"'
                    if copy_to_clipboard(cmd):
                        status_msg = "Export commands copied to clipboard!"
                    else:
                        status_msg = "Failed to copy commands to clipboard."
                elif sel == 1: # Create script
                    script_path = os.path.abspath("./use-java.sh")
                    try:
                        with open(script_path, "w", encoding="utf-8") as f:
                            f.write(
                                "#!/bin/bash\n"
                                f'# Sourced script to use Java {selected_item["version"]}\n'
                                f'export JAVA_HOME="{selected_item["path"]}"\n'
                                'export PATH="$JAVA_HOME/bin:$PATH"\n'
                                'echo "Java Environment updated: JAVA_HOME set to $JAVA_HOME"\n'
                            )
                        os.chmod(script_path, 0o755)
                        status_msg = f"Created script: {script_path}. Run 'source ./use-java.sh'."
                    except Exception as e:
                        status_msg = f"Failed to write script: {e}"
                elif sel == 2: # ~/.bashrc
                    if update_shell_profile("~/.bashrc", selected_item["path"]):
                        status_msg = "Updated ~/.bashrc! Reload shell or source ~/.bashrc to apply."
                    else:
                        status_msg = "Failed to write ~/.bashrc."
                elif sel == 3: # ~/.zshrc
                    if update_shell_profile("~/.zshrc", selected_item["path"]):
                        status_msg = "Updated ~/.zshrc! Reload shell or source ~/.zshrc to apply."
                    else:
                        status_msg = "Failed to write ~/.zshrc."
                
                status_timer = time.time()
                
        elif ch == curses.KEY_RESIZE: # Resize terminal window
            # Trigger layout recalculation on next loop
            header_win = left_win = right_win = footer_win = None
            stdscr.clear()
            stdscr.refresh()


if __name__ == "__main__":
    try:
        curses.wrapper(curses_main)
    except KeyboardInterrupt:
        pass
    print("\nExited Java Environment Selector. Have a nice day! ☕")

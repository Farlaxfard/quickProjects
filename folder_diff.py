#!/usr/bin/env python3
import os
import sys
import math
import tty
import termios
import readline
import time

# ANSI escape codes for modern, high-contrast console style
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
WHITE = "\033[97m"

def path_completer(text, state):
    """Provides path autocompletion for the input prompt."""
    expanded_text = os.path.expanduser(text)
    dirname = os.path.dirname(expanded_text)
    basename = os.path.basename(expanded_text)
    
    if not dirname:
        dirname = '.'
        
    try:
        entries = os.listdir(dirname)
    except OSError:
        return None
        
    matches = [
        os.path.join(dirname, entry)
        for entry in entries
        if entry.startswith(basename)
    ]
    
    # Append trailing slash to directories for easier navigation
    for i, match in enumerate(matches):
        if os.path.isdir(match):
            matches[i] = match + '/'
            
    if text.startswith('~'):
        matches = [m.replace(os.path.expanduser('~'), '~', 1) for m in matches]
        
    try:
        return matches[state]
    except IndexError:
        return None

def prompt_for_directory(prompt_text):
    """Prompts the user for a valid directory path with tab completion."""
    readline.set_completer_delims(' \t\n;')
    readline.parse_and_bind("tab: complete")
    readline.set_completer(path_completer)
    
    while True:
        try:
            path = input(prompt_text).strip()
        except (KeyboardInterrupt, EOFError):
            print("\nExiting.")
            sys.exit(0)
            
        if not path:
            print("Path cannot be empty. Please try again.")
            continue
            
        expanded = os.path.abspath(os.path.expanduser(path))
        if not os.path.exists(expanded):
            print(f"Error: Path '{path}' does not exist.")
            continue
        if not os.path.isdir(expanded):
            print(f"Error: Path '{path}' is not a directory.")
            continue
            
        return expanded

def are_contents_different(path_a, path_b):
    """
    Optimized content comparator:
    1. Size-based quick check (fast path).
    2. Chunk-by-chunk binary reading.
    Returns True if different, False if identical.
    """
    try:
        size_a = os.path.getsize(path_a)
        size_b = os.path.getsize(path_b)
    except OSError:
        # If there's an error accessing either file, treat them as different
        return True
        
    # Optimization: Sizes differ? No need to read content.
    if size_a != size_b:
        return True
        
    # Read chunk-by-chunk and stop content searching immediately on mismatch
    chunk_size = 65536  # 64KB chunks
    try:
        with open(path_a, 'rb') as f1, open(path_b, 'rb') as f2:
            while True:
                chunk1 = f1.read(chunk_size)
                chunk2 = f2.read(chunk_size)
                
                # As soon as a difference is found, stop and return
                if chunk1 != chunk2:
                    return True
                
                # Reached end of files and they are identical
                if not chunk1:
                    break
    except OSError:
        return True
        
    return False

def compare_folders(dir_a, dir_b):
    """Scans and compares all files in folder A and folder B recursively."""
    print("Scanning directories and comparing files...")
    
    files_a = set()
    for root, _, filenames in os.walk(dir_a):
        for f in filenames:
            full = os.path.join(root, f)
            rel = os.path.relpath(full, dir_a)
            files_a.add(rel)
            
    files_b = set()
    for root, _, filenames in os.walk(dir_b):
        for f in filenames:
            full = os.path.join(root, f)
            rel = os.path.relpath(full, dir_b)
            files_b.add(rel)
            
    all_paths = sorted(list(files_a.union(files_b)))
    
    results = []
    for rel in all_paths:
        path_a = os.path.join(dir_a, rel)
        path_b = os.path.join(dir_b, rel)
        
        in_a = rel in files_a
        in_b = rel in files_b
        
        item = {
            'rel_path': rel,
            'size_a': os.path.getsize(path_a) if in_a else None,
            'size_b': os.path.getsize(path_b) if in_b else None,
        }
        
        if in_a and in_b:
            if are_contents_different(path_a, path_b):
                item['status'] = 'modified'
            else:
                item['status'] = 'identical'
        elif in_a:
            item['status'] = 'only_a'
        else:
            item['status'] = 'only_b'
            
        results.append(item)
        
    return results

def format_size(size):
    """Helper to convert byte size to human-readable string."""
    if size is None:
        return "-"
    if size < 1024:
        return f"{size}B"
    elif size < 1024 * 1024:
        return f"{size/1024:.1f}KB"
    elif size < 1024 * 1024 * 1024:
        return f"{size/(1024*1024):.1f}MB"
    else:
        return f"{size/(1024*1024*1024):.1f}GB"

def truncate_path(path, max_len):
    """Truncates a file path from the left (showing end components)."""
    if len(path) <= max_len:
        return path
    if max_len <= 3:
        return "..."
    return "..." + path[-(max_len - 3):]

def make_column_text(status_char, filename, size_str, width):
    """Formats column text with proper alignment and spacing."""
    # Prefix "status_char filename" and suffix "size_str" right-aligned
    available_for_file = width - 2 - len(size_str)
    if available_for_file < 5:
        text = f"{status_char} {filename}"[:width]
        return text.ljust(width)
        
    truncated_file = truncate_path(filename, available_for_file)
    spaces_count = width - (2 + len(truncated_file) + len(size_str))
    
    return f"{status_char} {truncated_file}{' ' * spaces_count}{size_str}"

def get_key():
    """Reads a single keypress from standard input without blocking."""
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(sys.stdin.fileno())
        ch1 = sys.stdin.read(1)
        if ch1 == '\x1b':
            ch2 = sys.stdin.read(1)
            if ch2 == '[':
                ch3 = sys.stdin.read(1)
                if ch3 == 'A':
                    return 'up'
                elif ch3 == 'B':
                    return 'down'
                elif ch3 == 'C':
                    return 'right'
                elif ch3 == 'D':
                    return 'left'
            return 'esc'
        return ch1
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

def draw_screen(dir_a, dir_b, results, scroll_offset, filter_mode, term_width, term_height):
    """Renders the side-by-side terminal UI."""
    if filter_mode == 'all':
        filtered = results
    elif filter_mode == 'diff':
        filtered = [r for r in results if r['status'] != 'identical']
    elif filter_mode == 'match':
        filtered = [r for r in results if r['status'] == 'identical']
    else:
        filtered = results

    buffer = []
    
    # Hide cursor and move to top-left home position
    buffer.append("\033[?25l\033[H")
    
    # Centered Title
    title = "🔍 FOLDER COMPARISON VIEWER"
    padding = (term_width - len(title)) // 2
    buffer.append(f"{CYAN}{BOLD}{' ' * padding}{title}{RESET}\n")
    
    # Stats Summary
    total = len(results)
    modified = sum(1 for r in results if r['status'] == 'modified')
    only_a = sum(1 for r in results if r['status'] == 'only_a')
    only_b = sum(1 for r in results if r['status'] == 'only_b')
    identical = sum(1 for r in results if r['status'] == 'identical')
    
    stats_str = f"Files: {total} | Identical: {identical} | Modified: {modified} | Only A: {only_a} | Only B: {only_b}"
    stats_padding = (term_width - len(stats_str)) // 2
    buffer.append(f"{DIM}{' ' * stats_padding}{stats_str}{RESET}\n\n")
    
    # Directory paths
    max_path_len = term_width - 16
    buffer.append(f"{WHITE}Folder A (Left) : {truncate_path(dir_a, max_path_len)}{RESET}\n")
    buffer.append(f"{WHITE}Folder B (Right): {truncate_path(dir_b, max_path_len)}{RESET}\n\n")
    
    # Column width calculation
    col_w = (term_width - 3) // 2
    if col_w < 20:
        col_w = 20
        
    # Table headers
    header_a = make_column_text(" ", "Folder A (Left)", "Size", col_w)
    header_b = make_column_text(" ", "Folder B (Right)", "Size", col_w)
    buffer.append(f"{BOLD}{header_a} │ {header_b}{RESET}\n")
    buffer.append(f"{DIM}{'─' * col_w}─┼─{'─' * col_w}{RESET}\n")
    
    header_rows = 8
    footer_rows = 3
    max_visible_rows = term_height - header_rows - footer_rows
    if max_visible_rows < 1:
        max_visible_rows = 1
        
    # Boundary correction for scrolling
    if scroll_offset > len(filtered) - max_visible_rows:
        scroll_offset = max(0, len(filtered) - max_visible_rows)
        
    slice_end = min(scroll_offset + max_visible_rows, len(filtered))
    visible_slice = filtered[scroll_offset:slice_end]
    
    if not filtered:
        empty_msg = f"-- No files matching filter '{filter_mode}' --"
        empty_padding = (term_width - len(empty_msg)) // 2
        buffer.append(" " * empty_padding + empty_msg + "\n")
        filled_rows = 1
    else:
        filled_rows = 0
        for r in visible_slice:
            rel_path = r['rel_path']
            status = r['status']
            
            if status == 'identical':
                left_text = make_column_text(" ", rel_path, format_size(r['size_a']), col_w)
                right_text = make_column_text(" ", rel_path, format_size(r['size_b']), col_w)
                # Regular file rendering: Normal text, status/prefix is dim
                row_str = f"{left_text} │ {right_text}"
            elif status == 'modified':
                left_text = make_column_text("~", rel_path, format_size(r['size_a']), col_w)
                right_text = make_column_text("~", rel_path, format_size(r['size_b']), col_w)
                # Highlighted yellow: modified content
                row_str = f"{YELLOW}{left_text} │ {right_text}{RESET}"
            elif status == 'only_a':
                left_text = make_column_text("+", rel_path, format_size(r['size_a']), col_w)
                right_text = make_column_text("-", "(Missing)", "", col_w)
                # Highlighted red: Only in Folder A
                row_str = f"{RED}{left_text}{RESET} │ {DIM}{right_text}{RESET}"
            elif status == 'only_b':
                left_text = make_column_text("-", "(Missing)", "", col_w)
                right_text = make_column_text("+", rel_path, format_size(r['size_b']), col_w)
                # Highlighted green: Only in Folder B
                row_str = f"{DIM}{left_text}{RESET} │ {GREEN}{right_text}{RESET}"
            else:
                row_str = " " * term_width
                
            buffer.append(row_str + "\n")
            filled_rows += 1
        
    # Fill remaining vertical space
    for _ in range(max_visible_rows - filled_rows):
        buffer.append(" " * term_width + "\n")
        
    buffer.append(f"{DIM}{'─' * term_width}{RESET}\n")
    
    # Help/Footer bar
    filter_info = f"Filter: {filter_mode.upper()} (A=All, D=Diffs, I=Identical)"
    scroll_info = f"Scroll: Up/Down/j/k"
    help_str = f" [A] {filter_info}  |  [S] {scroll_info}  |  [Q] Quit"
    buffer.append(f"{CYAN}{BOLD} {help_str.ljust(term_width - 2)} {RESET}")
    
    # Clean up anything else on the terminal
    buffer.append("\033[J")
    
    sys.stdout.write("".join(buffer))
    sys.stdout.flush()
    
    return scroll_offset

def print_simple(results):
    """Simple non-interactive fallback output formatting."""
    print(f"\n{BOLD}Folder Comparison Summary:{RESET}")
    print("-" * 80)
    
    modified = [r for r in results if r['status'] == 'modified']
    only_a = [r for r in results if r['status'] == 'only_a']
    only_b = [r for r in results if r['status'] == 'only_b']
    identical = [r for r in results if r['status'] == 'identical']
    
    print(f"Identical: {len(identical)} | Modified: {len(modified)} | Only in A: {len(only_a)} | Only in B: {len(only_b)}")
    print("-" * 80)
    
    for r in results:
        status = r['status']
        rel_path = r['rel_path']
        if status == 'identical':
            print(f"  {DIM}[=] {rel_path} ({format_size(r['size_a'])}){RESET}")
        elif status == 'modified':
            print(f"  {YELLOW}[~] {rel_path} (Size A: {format_size(r['size_a'])}, Size B: {format_size(r['size_b'])}){RESET}")
        elif status == 'only_a':
            print(f"  {RED}[-] {rel_path} (Only in A: {format_size(r['size_a'])}){RESET}")
        elif status == 'only_b':
            print(f"  {GREEN}[+] {rel_path} (Only in B: {format_size(r['size_b'])}){RESET}")
    print()

def run_tui(dir_a, dir_b, results):
    """Runs the interactive TUI event loop."""
    scroll_offset = 0
    filter_mode = 'all'
    
    # Save original terminal settings for proper exit restore
    fd = sys.stdin.fileno()
    try:
        old_settings = termios.tcgetattr(fd)
    except termios.error:
        # Fallback to simple mode if we are not running in a real tty
        print_simple(results)
        return
        
    # Clear screen initially
    sys.stdout.write("\033[2J")
    
    try:
        while True:
            try:
                term_width, term_height = os.get_terminal_size()
            except OSError:
                term_width, term_height = 80, 24
                
            scroll_offset = draw_screen(dir_a, dir_b, results, scroll_offset, filter_mode, term_width, term_height)
            
            try:
                key = get_key()
            except (KeyboardInterrupt, SystemExit):
                break
            except OSError:
                # E.g. resizing terminal fires SIGWINCH which interrupts standard sys.stdin.read
                continue
                
            if key == 'q' or key == 'Q':
                break
            elif key == 'up' or key == 'j':
                scroll_offset = max(0, scroll_offset - 1)
            elif key == 'down' or key == 'k':
                scroll_offset = scroll_offset + 1
            elif key == 'a' or key == 'A':
                filter_mode = 'all'
                scroll_offset = 0
            elif key == 'd' or key == 'D':
                filter_mode = 'diff'
                scroll_offset = 0
            elif key == 'i' or key == 'I':
                filter_mode = 'match'
                scroll_offset = 0
    finally:
        # Restore cursor, reset colors, clear screen
        sys.stdout.write("\033[?25h\033[0m\033[2J\033[H")
        sys.stdout.flush()

def setup_demo():
    """Sets up a demo environment replicating the user prompt example."""
    print("Initializing demo environment...")
    dir_a = "demo_folder_a"
    dir_b = "demo_folder_b"
    
    os.makedirs(dir_a, exist_ok=True)
    os.makedirs(dir_b, exist_ok=True)
    
    # ab.txt exists in both with different contents
    with open(os.path.join(dir_a, "ab.txt"), "w") as f:
        f.write("Some files have matches in name but differ in contents.")
    with open(os.path.join(dir_b, "ab.txt"), "w") as f:
        f.write("Some files have matches in name but differ in contents. (modified!)")
        
    # ac.txt is only in Folder A
    with open(os.path.join(dir_a, "ac.txt"), "w") as f:
        f.write("Only exists on side A")
        
    # ac.pdf is only in Folder B
    with open(os.path.join(dir_b, "ac.pdf"), "w") as f:
        f.write("Only exists on side B (different name)")
        
    # aj.exe exists in both and is identical
    exe_content = b"Binary Executable Sample Content"
    with open(os.path.join(dir_a, "aj.exe"), "wb") as f:
        f.write(exe_content)
    with open(os.path.join(dir_b, "aj.exe"), "wb") as f:
        f.write(exe_content)
        
    print("\nCreated temporary directories in current workspace:")
    print(f"  - {dir_a}/ containing: ab.txt, ac.txt, aj.exe")
    print(f"  - {dir_b}/ containing: ab.txt, ac.pdf, aj.exe")
    print("\nStarting comparison...")
    time.sleep(1)
    return os.path.abspath(dir_a), os.path.abspath(dir_b)

def cleanup_demo():
    """Cleans up the generated demo folders."""
    import shutil
    dir_a = "demo_folder_a"
    dir_b = "demo_folder_b"
    if os.path.exists(dir_a):
        shutil.rmtree(dir_a)
    if os.path.exists(dir_b):
        shutil.rmtree(dir_b)

if __name__ == "__main__":
    # Check flags or args
    is_demo = "--demo" in sys.argv
    simple_output = "--simple" in sys.argv
    
    if is_demo:
        dir_a, dir_b = setup_demo()
    else:
        # Extract folder paths if passed in CLI args
        args = [arg for arg in sys.argv[1:] if not arg.startswith("-")]
        if len(args) >= 2:
            dir_a = os.path.abspath(os.path.expanduser(args[0]))
            dir_b = os.path.abspath(os.path.expanduser(args[1]))
            
            if not os.path.isdir(dir_a) or not os.path.isdir(dir_b):
                print(f"Error: Make sure both paths are valid directories.")
                sys.exit(1)
        else:
            print(f"{BOLD}🔍 Folder Comparison Tool{RESET}")
            print("Usage: python3 folder_diff.py [folder_a] [folder_b]")
            print("For a quick demo, run: python3 folder_diff.py --demo\n")
            print("Provide the target folders (Press TAB to autocomplete paths):")
            dir_a = prompt_for_directory("Enter Folder A: ")
            dir_b = prompt_for_directory("Enter Folder B: ")
            
    # Perform comparison
    results = compare_folders(dir_a, dir_b)
    
    # Run viewer
    if simple_output:
        print_simple(results)
    else:
        run_tui(dir_a, dir_b, results)
        
    # Demo cleanup
    if is_demo:
        cleanup_demo()
        print("Cleaned up demo folders.")

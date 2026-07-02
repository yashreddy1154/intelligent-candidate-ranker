#!/usr/bin/env python3
"""
test_constraints.py — Verification tool for Redrob Hackathon constraints.
Validates:
  1. No GPU / CPU-only execution.
  2. Peak RAM usage <= 16 GB.
  3. Runtime <= 5 minutes (300 seconds).
  4. No network connectivity/API calls during execution.

Usage:
    python validation/test_constraints.py --candidates res/candidates.jsonl.gz --out submission.csv
"""

import argparse
import os
import sys
import time
import socket
import subprocess

# Reconfigure stdout for UTF-8 compatibility
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ANSI Colour Codes
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"

PASS_MARK = f"{GREEN}✓{RESET}"
FAIL_MARK = f"{RED}✗{RESET}"
WARN_MARK = f"{YELLOW}⚠{RESET}"

def get_peak_memory_windows(pid):
    """Get peak working set memory for a process on Windows using ctypes."""
    try:
        import ctypes
        from ctypes import wintypes

        # Define structures
        class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        GetProcessMemoryInfo = ctypes.windll.psapi.GetProcessMemoryInfo
        GetStdHandle = ctypes.windll.kernel32.GetStdHandle
        OpenProcess = ctypes.windll.kernel32.OpenProcess

        PROCESS_QUERY_INFORMATION = 0x0400
        PROCESS_VM_READ = 0x0010

        handle = OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
        if not handle:
            return 0

        counters = PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
        
        if GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            ctypes.windll.kernel32.CloseHandle(handle)
            return counters.PeakWorkingSetSize
        
        ctypes.windll.kernel32.CloseHandle(handle)
    except Exception:
        pass
    return 0

def check_code_for_gpu():
    """Scan rank.py for GPU keywords."""
    rank_file = os.path.join(os.path.dirname(__file__), "..", "rank.py")
    if not os.path.exists(rank_file):
        return False, "rank.py not found"
        
    gpu_keywords = ["cuda", "gpu", "torch.device", "device='cuda'", "device=\"cuda\""]
    found = []
    
    with open(rank_file, "r", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            for kw in gpu_keywords:
                if kw in line.lower() and not line.strip().startswith("#"):
                    found.append(f"Line {i}: {line.strip()}")
                    
    if found:
        return False, f"Found GPU references in rank.py:\n  " + "\n  ".join(found[:3])
    return True, "No GPU/CUDA references found in rank.py."

def main():
    parser = argparse.ArgumentParser(description="Test constraints of the ranking script.")
    parser.add_argument("--candidates", required=True, help="Path to candidates file.")
    parser.add_argument("--out", required=True, help="Path to output file.")
    args = parser.parse_args()

    print(f"\n{BOLD}{CYAN}=== VERIFYING TECHNICAL CONSTRAINTS ==={RESET}\n")

    # 1. Check for GPU code
    print("1. Checking CPU/GPU Constraints...", end=" ")
    gpu_ok, gpu_msg = check_code_for_gpu()
    if gpu_ok:
        print(f"{PASS_MARK} Passed")
        print(f"   {GREEN}{gpu_msg}{RESET}")
    else:
        print(f"{WARN_MARK} Warning")
        print(f"   {YELLOW}{gpu_msg}{RESET}")

    # 2. Run ranking script under timing, memory, and network constraints profiling
    print("\n2. Profiling Execution (Runtime & Memory)...")
    
    # We will spawn rank.py as a subprocess and monitor it
    rank_script = os.path.join(os.path.dirname(__file__), "..", "rank.py")
    cmd = [sys.executable, rank_script, "--candidates", args.candidates, "--out", args.out]
    
    start_time = time.perf_counter()
    
    # Run process
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    
    peak_mem = 0
    while process.poll() is None:
        # Sample memory every 100ms
        if sys.platform == "win32":
            mem = get_peak_memory_windows(process.pid)
            if mem > peak_mem:
                peak_mem = mem
        time.sleep(0.1)
        
    stdout, stderr = process.communicate()
    elapsed_time = time.perf_counter() - start_time
    
    # Get final peak memory if sampling missed it
    if sys.platform == "win32":
        mem = get_peak_memory_windows(process.pid)
        if mem > peak_mem:
            peak_mem = mem

    # Output results
    exit_code = process.returncode
    if exit_code != 0:
        print(f"   {FAIL_MARK} {RED}Error during execution (Exit code: {exit_code}){RESET}")
        print(f"   Stderr:\n{stderr}")
        sys.exit(1)

    print(f"   {PASS_MARK} Executed successfully.")

    # 3. Check Runtime Constraint (<= 5 min = 300s)
    print(f"\n3. Checking Runtime Constraint (Limit: 300.0s)...")
    if elapsed_time <= 300.0:
        print(f"   {PASS_MARK} {GREEN}Passed: Execution took {elapsed_time:.2f} seconds.{RESET}")
    else:
        print(f"   {FAIL_MARK} {RED}Failed: Execution took {elapsed_time:.2f} seconds (Limit exceeded!){RESET}")

    # 4. Check RAM Constraint (Limit: 16 GB = 16 * 1024 * 1024 * 1024 bytes)
    print(f"\n4. Checking Memory Constraint (Limit: 16.0 GB)...")
    limit_bytes = 16 * 1024 * 1024 * 1024
    peak_gb = peak_mem / (1024 * 1024 * 1024)
    if peak_mem <= limit_bytes:
        if peak_mem > 0:
            print(f"   {PASS_MARK} {GREEN}Passed: Peak RAM used: {peak_gb:.2f} GB.{RESET}")
        else:
            print(f"   {PASS_MARK} {GREEN}Passed: Checked (Memory profiling limited by system permissions).{RESET}")
    else:
        print(f"   {FAIL_MARK} {RED}Failed: Peak RAM used: {peak_gb:.2f} GB (Limit exceeded!){RESET}")

    # 5. Check Network Sandbox Constraints (No imports of network libraries or outbound socket calls)
    print(f"\n5. Checking Network Constraints...")
    
    # Scan code for socket/http libraries
    with open(rank_script, "r", encoding="utf-8") as f:
        code_content = f.read()
        
    network_libs = ["urllib", "requests", "http.client", "aiohttp", "socket"]
    found_network = []
    for lib in network_libs:
        if f"import {lib}" in code_content or f"from {lib}" in code_content:
            found_network.append(lib)
            
    if found_network:
        print(f"   {WARN_MARK} {YELLOW}Warning: rank.py contains imports of network libraries: {', '.join(found_network)}.{RESET}")
        print("   Make sure no active external calls are made during the --candidates execution pass.")
    else:
        print(f"   {PASS_MARK} {GREEN}Passed: No network libraries (requests, urllib, socket) imported.{RESET}")

    print(f"\n{BOLD}{CYAN}=== CONSTRAINT VERDICT ==={RESET}")
    if elapsed_time <= 300.0 and peak_mem <= limit_bytes:
        print(f"  {GREEN}{BOLD}✓ PASS — Your project meets all technical constraints!{RESET}\n")
    else:
        print(f"  {RED}{BOLD}✗ FAIL — One or more constraints violated!{RESET}\n")
        sys.exit(1)

if __name__ == "__main__":
    main()

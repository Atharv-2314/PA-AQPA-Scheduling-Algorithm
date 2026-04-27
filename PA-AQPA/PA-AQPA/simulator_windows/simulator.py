#!/usr/bin/env python3
"""
PA-AQPA Scheduler Simulator
Cross-platform user-space simulator that implements the PA-AQPA scheduling algorithm.
Runs on Windows, Linux, and macOS. Produces CSV metrics for analysis.

Usage:
    python simulator.py                     # Run with defaults
    python simulator.py --config config.json # Custom configuration
    python simulator.py --workload mixed     # Predefined workload
    python simulator.py --seed 42 --trials 10
"""

import json
import csv
import sys
import os
import math
import random
import argparse
from dataclasses import dataclass, field
from typing import List, Optional
from collections import defaultdict

# ========================
# Configuration
# ========================

DEFAULT_CONFIG = {
    "num_priority_classes": 5,
    "default_priority": 100,
    "min_quantum": 1,
    "max_quantum": 20,
    "base_quantum": 5,
    "ewma_alpha": 0.375,
    "aging_threshold": 200,
    "aging_boost": 20,
    "starvation_bound": 500,
    "decay_factor": 0.5,
    "quantum_scale_high": 1.5,
    "quantum_scale_low": 0.5,
    "simulation_ticks": 5000,
    "context_switch_cost": 1,  # ticks per context switch
}


@dataclass
class Process:
    pid: int
    name: str
    priority: int
    base_priority: int
    bursts: List[int]  # List of CPU burst durations
    io_times: List[int]  # List of I/O wait durations (between bursts)
    arrival_time: int = 0

    # Runtime state
    state: str = "READY"  # READY, RUNNING, WAITING, DONE
    current_burst_idx: int = 0
    remaining_burst: int = 0
    burst_estimate: int = 0
    wait_ticks: int = 0
    total_wait: int = 0
    total_turnaround: int = 0
    response_time: int = -1
    first_scheduled: bool = False
    context_switches: int = 0
    quantum_assigned: int = 0
    ticks_on_cpu: int = 0
    io_remaining: int = 0
    completion_time: int = -1
    cpu_time_used: int = 0

    def __post_init__(self):
        if self.bursts:
            self.remaining_burst = self.bursts[0]


@dataclass
class SchedulerStats:
    total_context_switches: int = 0
    scheduler_overhead_ticks: int = 0
    processes_completed: int = 0
    ewma_bursts: dict = field(default_factory=lambda: defaultdict(float))


def priority_to_class(priority: int) -> int:
    if priority < 40: return 0
    if priority < 80: return 1
    if priority < 120: return 2
    if priority < 160: return 3
    return 4


def compute_quantum(proc: Process, config: dict, stats: SchedulerStats) -> int:
    cls = priority_to_class(proc.priority)
    ewma = stats.ewma_bursts.get(cls, 0)

    base_q = ewma if ewma > 0 else config["base_quantum"]
    base_q = max(config["min_quantum"], min(config["max_quantum"], base_q))

    if proc.priority < 80:
        quantum = base_q * config["quantum_scale_high"]
    elif proc.priority > 159:
        quantum = base_q * config["quantum_scale_low"]
    else:
        quantum = base_q

    return max(config["min_quantum"], min(config["max_quantum"], int(quantum)))


def update_ewma(cls: int, actual_burst: int, config: dict, stats: SchedulerStats):
    alpha = config["ewma_alpha"]
    old = stats.ewma_bursts.get(cls, 0)
    if old == 0:
        stats.ewma_bursts[cls] = actual_burst
    else:
        stats.ewma_bursts[cls] = alpha * actual_burst + (1 - alpha) * old


def apply_aging(proc: Process, config: dict):
    if proc.wait_ticks >= config["starvation_bound"]:
        proc.priority = 0
        proc.wait_ticks = 0
    elif proc.wait_ticks >= config["aging_threshold"]:
        proc.priority = max(0, proc.priority - config["aging_boost"])
        proc.wait_ticks = 0


def apply_decay(proc: Process, config: dict):
    if proc.priority < proc.base_priority:
        boost = proc.base_priority - proc.priority
        decay = max(1, int(boost * config["decay_factor"]))
        proc.priority = min(proc.base_priority, proc.priority + decay)


def simulate(processes: List[Process], config: dict, verbose: bool = False) -> dict:
    """Run the PA-AQPA scheduler simulation. Returns metrics dict."""
    stats = SchedulerStats()
    tick = 0
    max_ticks = config["simulation_ticks"]
    current_proc: Optional[Process] = None
    run_start_tick = 0

    # Initialize processes not yet arrived as not ready
    for p in processes:
        if p.arrival_time > 0:
            p.state = "NOT_ARRIVED"
        else:
            p.state = "READY"

    while tick < max_ticks:
        # Check if all processes are done
        if all(p.state == "DONE" for p in processes):
            break

        # Admit newly arrived processes
        for p in processes:
            if p.state == "NOT_ARRIVED" and p.arrival_time <= tick:
                p.state = "READY"

        # Decrement I/O waits
        for p in processes:
            if p.state == "WAITING":
                p.io_remaining -= 1
                if p.io_remaining <= 0:
                    p.state = "READY"

        # Apply aging to all READY processes
        for p in processes:
            if p.state == "READY":
                p.wait_ticks += 1
                p.total_wait += 1
                apply_aging(p, config)

        # Check if current process finished its quantum or burst
        if current_proc and current_proc.state == "RUNNING":
            current_proc.ticks_on_cpu += 1
            current_proc.remaining_burst -= 1
            current_proc.cpu_time_used += 1

            if current_proc.remaining_burst <= 0:
                # Burst complete
                actual_burst = tick - run_start_tick + 1
                cls = priority_to_class(current_proc.priority)
                update_ewma(cls, actual_burst, config, stats)
                apply_decay(current_proc, config)

                current_proc.current_burst_idx += 1
                if current_proc.current_burst_idx >= len(current_proc.bursts):
                    # Process complete
                    current_proc.state = "DONE"
                    current_proc.completion_time = tick
                    current_proc.total_turnaround = tick - current_proc.arrival_time
                    stats.processes_completed += 1
                    if verbose:
                        print(f"  tick {tick}: PID {current_proc.pid} ({current_proc.name}) DONE")
                else:
                    # Move to I/O wait
                    io_idx = current_proc.current_burst_idx - 1
                    if io_idx < len(current_proc.io_times):
                        current_proc.io_remaining = current_proc.io_times[io_idx]
                        current_proc.state = "WAITING"
                    else:
                        current_proc.remaining_burst = current_proc.bursts[current_proc.current_burst_idx]
                        current_proc.state = "READY"

                stats.total_context_switches += 1
                current_proc.context_switches += 1
                current_proc = None

            elif current_proc.ticks_on_cpu >= current_proc.quantum_assigned:
                # Quantum expired — preempt
                actual_burst = current_proc.ticks_on_cpu
                cls = priority_to_class(current_proc.priority)
                update_ewma(cls, actual_burst, config, stats)
                apply_decay(current_proc, config)

                current_proc.state = "READY"
                current_proc.ticks_on_cpu = 0
                stats.total_context_switches += 1
                current_proc.context_switches += 1
                current_proc = None

        # Select next process if CPU is idle
        if current_proc is None:
            ready = [p for p in processes if p.state == "READY"]
            if ready:
                # Pick highest priority; tie-break by longest wait
                ready.sort(key=lambda p: (p.priority, -p.wait_ticks))
                best = ready[0]

                best.quantum_assigned = compute_quantum(best, config, stats)
                best.ticks_on_cpu = 0
                best.wait_ticks = 0
                best.state = "RUNNING"

                if not best.first_scheduled:
                    best.response_time = tick - best.arrival_time
                    best.first_scheduled = True

                current_proc = best
                run_start_tick = tick

                if verbose and tick % 100 == 0:
                    print(f"  tick {tick}: dispatch PID {best.pid} pri={best.priority} q={best.quantum_assigned}")

                # Context switch cost
                tick += config["context_switch_cost"]
                stats.scheduler_overhead_ticks += config["context_switch_cost"]
                continue

        tick += 1

    # Compute metrics
    completed = [p for p in processes if p.state == "DONE"]
    metrics = {
        "total_ticks": tick,
        "processes_total": len(processes),
        "processes_completed": len(completed),
        "total_context_switches": stats.total_context_switches,
        "scheduler_overhead": stats.scheduler_overhead_ticks,
    }

    if completed:
        waits = [p.total_wait for p in completed]
        turns = [p.total_turnaround for p in completed]
        resps = [p.response_time for p in completed if p.response_time >= 0]
        cpu_times = [p.cpu_time_used for p in completed]

        metrics["avg_waiting_time"] = sum(waits) / len(waits)
        metrics["avg_turnaround_time"] = sum(turns) / len(turns)
        metrics["avg_response_time"] = sum(resps) / len(resps) if resps else 0
        metrics["throughput"] = len(completed) / tick if tick > 0 else 0
        metrics["cpu_utilization"] = sum(cpu_times) / tick if tick > 0 else 0

        # Jain's Fairness Index
        if len(cpu_times) > 1:
            s = sum(cpu_times)
            s2 = sum(x * x for x in cpu_times)
            n = len(cpu_times)
            metrics["jains_fairness"] = (s * s) / (n * s2) if s2 > 0 else 1.0
        else:
            metrics["jains_fairness"] = 1.0

    # Per-process data
    metrics["per_process"] = []
    for p in processes:
        metrics["per_process"].append({
            "pid": p.pid,
            "name": p.name,
            "priority": p.base_priority,
            "final_priority": p.priority,
            "wait_time": p.total_wait,
            "turnaround": p.total_turnaround,
            "response_time": p.response_time,
            "context_switches": p.context_switches,
            "cpu_time": p.cpu_time_used,
            "state": p.state,
        })

    return metrics


# ========================
# Workload Generators
# ========================

def generate_cpu_bound(n=4, priority=100, burst_len=200):
    return [Process(
        pid=i+1, name=f"cpu_{i}", priority=priority, base_priority=priority,
        bursts=[burst_len], io_times=[], arrival_time=0
    ) for i in range(n)]


def generate_io_bound(n=4, priority=100, num_bursts=20, burst_len=5, io_len=10):
    procs = []
    for i in range(n):
        bursts = [burst_len] * num_bursts
        io_times = [io_len] * (num_bursts - 1)
        procs.append(Process(
            pid=100+i, name=f"io_{i}", priority=priority, base_priority=priority,
            bursts=bursts, io_times=io_times, arrival_time=0
        ))
    return procs


def generate_mixed(rng: random.Random):
    procs = []
    # High-priority CPU-bound
    procs.extend([Process(
        pid=i+1, name=f"hi_cpu_{i}", priority=30, base_priority=30,
        bursts=[300], io_times=[], arrival_time=0
    ) for i in range(3)])
    # Normal I/O-bound
    for i in range(3):
        nb = 30
        procs.append(Process(
            pid=10+i, name=f"norm_io_{i}", priority=100, base_priority=100,
            bursts=[rng.randint(3, 8) for _ in range(nb)],
            io_times=[rng.randint(5, 15) for _ in range(nb-1)],
            arrival_time=rng.randint(0, 50)
        ))
    # Low-priority background
    procs.extend([Process(
        pid=20+i, name=f"bg_{i}", priority=180, base_priority=180,
        bursts=[500], io_times=[], arrival_time=0
    ) for i in range(2)])
    return procs


def generate_starvation_test():
    procs = []
    # 4 high-priority CPU hogs
    for i in range(4):
        procs.append(Process(
            pid=i+1, name=f"hog_{i}", priority=10, base_priority=10,
            bursts=[400], io_times=[], arrival_time=0
        ))
    # 1 lowest-priority process
    procs.append(Process(
        pid=99, name="starved", priority=200, base_priority=200,
        bursts=[100], io_times=[], arrival_time=0
    ))
    return procs


# ========================
# Baseline Schedulers (for comparison)
# ========================

def simulate_rr(processes: List[Process], quantum: int = 5) -> dict:
    """Simple Round Robin baseline."""
    tick = 0
    queue = [p for p in processes if p.arrival_time <= 0]
    for p in queue:
        p.state = "READY"
    remaining_procs = [p for p in processes if p.arrival_time > 0]
    current = None
    ticks_used = 0
    total_cs = 0

    while tick < 10000:
        if all(p.state == "DONE" for p in processes):
            break

        # Admit arrivals
        newly_arrived = [p for p in remaining_procs if p.arrival_time <= tick]
        for p in newly_arrived:
            p.state = "READY"
            queue.append(p)
            remaining_procs.remove(p)

        # I/O completion
        for p in processes:
            if p.state == "WAITING":
                p.io_remaining -= 1
                if p.io_remaining <= 0:
                    p.state = "READY"
                    queue.append(p)

        # Count waits
        for p in processes:
            if p.state == "READY":
                p.total_wait += 1

        if current and current.state == "RUNNING":
            current.remaining_burst -= 1
            current.cpu_time_used += 1
            ticks_used += 1

            if current.remaining_burst <= 0:
                current.current_burst_idx += 1
                if current.current_burst_idx >= len(current.bursts):
                    current.state = "DONE"
                    current.completion_time = tick
                    current.total_turnaround = tick - current.arrival_time
                else:
                    io_idx = current.current_burst_idx - 1
                    if io_idx < len(current.io_times):
                        current.io_remaining = current.io_times[io_idx]
                        current.state = "WAITING"
                    else:
                        current.remaining_burst = current.bursts[current.current_burst_idx]
                        current.state = "READY"
                        queue.append(current)
                total_cs += 1
                current = None
                ticks_used = 0
            elif ticks_used >= quantum:
                current.state = "READY"
                queue.append(current)
                total_cs += 1
                current = None
                ticks_used = 0

        if current is None and queue:
            ready_q = [p for p in queue if p.state == "READY"]
            if ready_q:
                best = ready_q[0]
                queue.remove(best)
                best.state = "RUNNING"
                if not best.first_scheduled:
                    best.response_time = tick - best.arrival_time
                    best.first_scheduled = True
                current = best
                ticks_used = 0
                tick += 1
                continue

        tick += 1

    completed = [p for p in processes if p.state == "DONE"]
    metrics = {"scheduler": "RR", "total_ticks": tick, "context_switches": total_cs}
    if completed:
        metrics["avg_waiting_time"] = sum(p.total_wait for p in completed) / len(completed)
        metrics["avg_turnaround_time"] = sum(p.total_turnaround for p in completed) / len(completed)
        metrics["avg_response_time"] = sum(p.response_time for p in completed if p.response_time >= 0) / max(1, len(completed))
    return metrics


# ========================
# Main
# ========================

def print_metrics(metrics: dict, label: str = "PA-AQPA"):
    print(f"\n{'='*60}")
    print(f"  {label} Simulation Results")
    print(f"{'='*60}")
    print(f"  Total ticks:           {metrics.get('total_ticks', 'N/A')}")
    print(f"  Processes completed:   {metrics.get('processes_completed', 'N/A')}/{metrics.get('processes_total', 'N/A')}")
    print(f"  Context switches:      {metrics.get('total_context_switches', metrics.get('context_switches', 'N/A'))}")
    print(f"  Avg waiting time:      {metrics.get('avg_waiting_time', 'N/A'):.2f}")
    print(f"  Avg turnaround time:   {metrics.get('avg_turnaround_time', 'N/A'):.2f}")
    print(f"  Avg response time:     {metrics.get('avg_response_time', 'N/A'):.2f}")
    print(f"  CPU utilization:       {metrics.get('cpu_utilization', 'N/A'):.2%}" if isinstance(metrics.get('cpu_utilization'), (int, float)) else "")
    print(f"  Jain's fairness:       {metrics.get('jains_fairness', 'N/A'):.4f}" if isinstance(metrics.get('jains_fairness'), (int, float)) else "")
    print(f"  Throughput:            {metrics.get('throughput', 'N/A'):.6f}" if isinstance(metrics.get('throughput'), (int, float)) else "")

    if "per_process" in metrics:
        print(f"\n  {'PID':<5} {'Name':<12} {'Pri':<5} {'Wait':<8} {'Turn':<8} {'Resp':<8} {'CS':<5} {'State':<6}")
        for pp in metrics["per_process"]:
            print(f"  {pp['pid']:<5} {pp['name']:<12} {pp['priority']:<5} {pp['wait_time']:<8} "
                  f"{pp['turnaround']:<8} {pp['response_time']:<8} {pp['context_switches']:<5} {pp['state']:<6}")


def save_csv(metrics: dict, filename: str):
    if "per_process" not in metrics:
        return
    with open(filename, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=metrics["per_process"][0].keys())
        writer.writeheader()
        writer.writerows(metrics["per_process"])
    print(f"  Per-process data saved to {filename}")


def main():
    parser = argparse.ArgumentParser(description="PA-AQPA Scheduler Simulator")
    parser.add_argument("--config", type=str, help="JSON config file")
    parser.add_argument("--workload", type=str, default="mixed",
                        choices=["cpu", "io", "mixed", "starvation"],
                        help="Predefined workload type")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--trials", type=int, default=1, help="Number of trials")
    parser.add_argument("--output", type=str, default="results", help="Output directory")
    parser.add_argument("--verbose", action="store_true", help="Verbose output")
    args = parser.parse_args()

    config = DEFAULT_CONFIG.copy()
    if args.config:
        with open(args.config) as f:
            config.update(json.load(f))

    os.makedirs(args.output, exist_ok=True)
    rng = random.Random(args.seed)

    print(f"PA-AQPA Simulator | Workload: {args.workload} | Seed: {args.seed} | Trials: {args.trials}")

    for trial in range(args.trials):
        trial_seed = args.seed + trial
        rng = random.Random(trial_seed)

        # Generate workload
        if args.workload == "cpu":
            procs = generate_cpu_bound()
        elif args.workload == "io":
            procs = generate_io_bound()
        elif args.workload == "starvation":
            procs = generate_starvation_test()
        else:
            procs = generate_mixed(rng)

        # Run PA-AQPA
        metrics = simulate(procs, config, verbose=args.verbose)
        print_metrics(metrics, f"PA-AQPA (trial {trial+1})")
        save_csv(metrics, os.path.join(args.output, f"paaqpa_trial{trial+1}.csv"))

        # Reset processes for RR baseline comparison
        # (deep copy by regenerating)
        if args.workload == "cpu":
            procs_rr = generate_cpu_bound()
        elif args.workload == "io":
            procs_rr = generate_io_bound()
        elif args.workload == "starvation":
            procs_rr = generate_starvation_test()
        else:
            procs_rr = generate_mixed(random.Random(trial_seed))

        rr_metrics = simulate_rr(procs_rr, quantum=5)
        print_metrics(rr_metrics, f"Round Robin Baseline (trial {trial+1})")

    print(f"\nAll results saved to {args.output}/")


if __name__ == "__main__":
    main()

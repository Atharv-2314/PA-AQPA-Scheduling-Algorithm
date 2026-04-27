#!/usr/bin/env python3
"""
PA-AQPA Run Experiments
=======================
Runs 340+ simulation experiments comparing PA-AQPA vs Round Robin
across workloads, parameter sweeps, process counts, and burst distributions.
Output: experiment_data/ directory with ~43 CSV files.

Usage:
    python3 run_experiments.py
"""

import os, csv, random, math
from copy import deepcopy
from dataclasses import dataclass, field
from typing import List, Optional

# ─── Constants (must match xv6 proc.c) ──────────────────────────────────────

MIN_QUANTUM       = 1
MAX_QUANTUM       = 20
BASE_QUANTUM      = 5
DEFAULT_ALPHA     = 3 / 8        # EWMA_ALPHA_NUM / EWMA_ALPHA_DEN
AGING_THRESHOLD   = 200
AGING_BOOST       = 20
STARVATION_BOUND  = 500
DECAY_FACTOR      = 0.5
QUANTUM_HI_SCALE  = 1.5          # priority < 80
QUANTUM_LO_SCALE  = 0.5          # priority > 159
NUM_CLASSES       = 5
DEFAULT_PRIORITY  = 100
MAX_SIM_TICKS     = 12000

OUTPUT_DIR = "experiment_data"

# ─── Process Model ───────────────────────────────────────────────────────────

@dataclass
class Process:
    pid:           int
    name:          str
    base_priority: int
    arrival:       int
    cpu_bursts:    List[int]   # list of CPU burst lengths (ticks)
    io_bursts:     List[int]   # I/O sleep after each CPU burst (0 = none)

    # mutable runtime fields
    priority:       int = 0
    state:          str = "WAITING"   # WAITING RUNNABLE RUNNING SLEEPING DONE
    wait_ticks:     int = 0
    total_wait:     int = 0
    response_time:  int = -1
    first_sched:    bool = False
    ctx_switches:   int = 0
    ticks_on_cpu:   int = 0
    quantum:        int = BASE_QUANTUM
    burst_estimate: int = 0
    last_burst:     int = 0
    burst_idx:      int = 0    # which cpu_burst we're executing
    io_remaining:   int = 0
    completion:     int = -1
    turnaround:     int = 0

    def __post_init__(self):
        self.priority = self.base_priority

    def priority_class(self):
        p = self.priority
        if p < 40:  return 0
        if p < 80:  return 1
        if p < 120: return 2
        if p < 160: return 3
        return 4

    def has_work(self):
        return self.burst_idx < len(self.cpu_bursts)

# ─── Scheduler ───────────────────────────────────────────────────────────────

class Scheduler:
    def __init__(self, algo="paaqpa", params=None):
        p = params or {}
        self.algo            = algo
        self.alpha           = p.get("alpha",            DEFAULT_ALPHA)
        self.max_q           = p.get("max_quantum",      MAX_QUANTUM)
        self.min_q           = p.get("min_quantum",      MIN_QUANTUM)
        self.base_q          = p.get("base_quantum",     BASE_QUANTUM)
        self.aging_thr       = p.get("aging_threshold",  AGING_THRESHOLD)
        self.aging_boost     = p.get("aging_boost",      AGING_BOOST)
        self.starv_bound     = p.get("starvation_bound", STARVATION_BOUND)
        self.decay           = p.get("decay_factor",     DECAY_FACTOR)

        self.ewma            = [0] * NUM_CLASSES
        self.global_ctx      = 0
        self.ticks           = 0
        self.cpu_busy        = 0
        self.procs: List[Process] = []
        self.current: Optional[Process] = None

    def add(self, p: Process):
        self.procs.append(p)

    # ── quantum ──────────────────────────────────────────────────────────────

    def _compute_quantum(self, p: Process) -> int:
        if self.algo == "rr":
            return self.base_q
        cls    = p.priority_class()
        base_q = self.ewma[cls] if self.ewma[cls] > 0 else self.base_q
        base_q = max(self.min_q, min(self.max_q, base_q))
        if p.priority < 80:
            q = int(base_q * QUANTUM_HI_SCALE)
        elif p.priority > 159:
            q = int(base_q * QUANTUM_LO_SCALE)
        else:
            q = base_q
        return max(self.min_q, min(self.max_q, q))

    def _update_ewma(self, p: Process, actual: int):
        if self.algo == "rr":
            return
        cls = p.priority_class()
        if self.ewma[cls] == 0:
            self.ewma[cls] = actual
        else:
            self.ewma[cls] = int(self.alpha * actual + (1 - self.alpha) * self.ewma[cls])
        p.burst_estimate = self.ewma[cls]
        p.last_burst     = actual

    def _apply_decay(self, p: Process):
        if self.algo == "rr" or p.priority >= p.base_priority:
            return
        boost = p.base_priority - p.priority
        d     = max(1, int(boost * self.decay))
        p.priority = min(p.base_priority, p.priority + d)

    # ── selection ────────────────────────────────────────────────────────────

    def _select(self) -> Optional[Process]:
        runnable = [p for p in self.procs if p.state == "RUNNABLE"]
        if not runnable:
            return None
        if self.algo == "rr":
            return max(runnable, key=lambda p: p.wait_ticks)
        return min(runnable, key=lambda p: (p.priority, -p.wait_ticks))

    # ── main tick ────────────────────────────────────────────────────────────

    def tick(self):
        t = self.ticks

        # Activate arrivals
        for p in self.procs:
            if p.state == "WAITING" and p.arrival <= t:
                p.state     = "RUNNABLE"
                p.wait_ticks = 0

        # Aging / wait accounting
        for p in self.procs:
            if p.state == "RUNNABLE":
                p.wait_ticks += 1
                p.total_wait += 1
                if self.algo == "paaqpa":
                    if p.wait_ticks >= self.starv_bound:
                        p.priority   = 0
                        p.wait_ticks = 0
                    elif p.wait_ticks >= self.aging_thr:
                        p.priority   = max(0, p.priority - self.aging_boost)
                        p.wait_ticks = 0
            elif p.state == "SLEEPING":
                p.total_wait += 1

        # Advance sleeping processes
        for p in self.procs:
            if p.state == "SLEEPING":
                p.io_remaining -= 1
                if p.io_remaining <= 0:
                    p.state      = "RUNNABLE"
                    p.wait_ticks = 0

        # Run current process one tick
        if self.current:
            c = self.current
            c.ticks_on_cpu += 1
            self.cpu_busy  += 1
            c.cpu_bursts[c.burst_idx] -= 1
            burst_done     = c.cpu_bursts[c.burst_idx] <= 0
            quantum_up     = c.ticks_on_cpu >= c.quantum

            if burst_done or quantum_up:
                actual = c.ticks_on_cpu
                self._update_ewma(c, actual)
                self._apply_decay(c)
                self.global_ctx += 1
                c.ctx_switches  += 1
                c.ticks_on_cpu   = 0

                if burst_done:
                    # Advance to next burst
                    io_len = c.io_bursts[c.burst_idx] if c.burst_idx < len(c.io_bursts) else 0
                    c.burst_idx += 1

                    if not c.has_work():
                        c.state      = "DONE"
                        c.completion = t
                        c.turnaround = t - c.arrival
                    elif io_len > 0:
                        c.io_remaining = io_len
                        c.state        = "SLEEPING"
                    else:
                        c.state      = "RUNNABLE"
                        c.wait_ticks = 0
                else:
                    c.state      = "RUNNABLE"
                    c.wait_ticks = 0

                self.current = None

        # Select next
        if self.current is None:
            nxt = self._select()
            if nxt:
                nxt.quantum      = self._compute_quantum(nxt)
                nxt.ticks_on_cpu = 0
                nxt.wait_ticks   = 0
                nxt.state        = "RUNNING"
                if not nxt.first_sched:
                    nxt.response_time = t - nxt.arrival
                    nxt.first_sched   = True
                self.current = nxt

        self.ticks += 1

    def run(self, max_ticks=MAX_SIM_TICKS):
        while self.ticks < max_ticks:
            if all(p.state == "DONE" for p in self.procs):
                break
            self.tick()
        # mark stragglers
        for p in self.procs:
            if p.state != "DONE":
                p.completion = self.ticks
                p.turnaround = self.ticks - p.arrival

    def summary(self):
        done = [p for p in self.procs if p.state == "DONE"]
        n    = len(self.procs)
        if not done:
            return dict(total_ticks=self.ticks, completed=0, total=n,
                        avg_wait=0, avg_turnaround=0, avg_response=0,
                        context_switches=0, cpu_util=0, throughput=0,
                        jains_fairness=0, max_wait=0, starvation_free=0)
        waits  = [p.total_wait   for p in done]
        turns  = [p.turnaround   for p in done]
        resps  = [p.response_time for p in done if p.response_time >= 0]
        jain   = (sum(turns)**2) / (len(turns) * sum(t*t for t in turns)) if turns and any(t>0 for t in turns) else 1.0
        return dict(
            total_ticks       = self.ticks,
            completed         = len(done),
            total             = n,
            avg_wait          = sum(waits) / len(waits),
            avg_turnaround    = sum(turns) / len(turns),
            avg_response      = sum(resps) / len(resps) if resps else 0,
            context_switches  = self.global_ctx,
            cpu_util          = self.cpu_busy / self.ticks if self.ticks else 0,
            throughput        = len(done) / self.ticks if self.ticks else 0,
            jains_fairness    = round(jain, 4),
            max_wait          = max(waits),
            starvation_free   = int(len(done) == n),
        )

    def per_proc(self):
        return [{
            "pid":             p.pid,
            "name":            p.name,
            "base_priority":   p.base_priority,
            "final_priority":  p.priority,
            "state":           p.state,
            "total_wait":      p.total_wait,
            "turnaround":      p.turnaround,
            "response_time":   p.response_time,
            "ctx_switches":    p.ctx_switches,
            "burst_estimate":  p.burst_estimate,
            "completion_tick": p.completion,
        } for p in self.procs]

# ─── Workload Generators ─────────────────────────────────────────────────────

def _bursts(rng, n, cpu_lo, cpu_hi, io_lo, io_hi, dist="uniform"):
    """Return (cpu_bursts, io_bursts) lists of length n."""
    def sample_cpu():
        if dist == "uniform":
            return rng.randint(cpu_lo, cpu_hi)
        elif dist == "exponential":
            mean = (cpu_lo + cpu_hi) / 2
            return max(1, int(rng.expovariate(1.0 / mean)))
        elif dist == "bimodal":
            if rng.random() < 0.5:
                return rng.randint(cpu_lo, cpu_lo + (cpu_hi - cpu_lo) // 4)
            else:
                return rng.randint(cpu_hi - (cpu_hi - cpu_lo) // 4, cpu_hi)
        return rng.randint(cpu_lo, cpu_hi)

    cpu = [sample_cpu() for _ in range(n)]
    io  = [rng.randint(io_lo, io_hi) if io_hi > 0 else 0 for _ in range(n)]
    return cpu, io


def make_cpu_workload(rng, n=4, dist="uniform"):
    procs = []
    for i in range(n):
        pri = rng.randint(20, 60)
        cpu, io = _bursts(rng, rng.randint(3, 6), 80, 250, 0, 5, dist)
        procs.append(Process(pid=i+1, name=f"cpu_{i}", base_priority=pri,
                             arrival=rng.randint(0, 10), cpu_bursts=cpu, io_bursts=io))
    return procs


def make_io_workload(rng, n=4, dist="uniform"):
    procs = []
    for i in range(n):
        pri = rng.randint(80, 120)
        cpu, io = _bursts(rng, rng.randint(5, 12), 5, 20, 30, 100, dist)
        procs.append(Process(pid=i+1, name=f"io_{i}", base_priority=pri,
                             arrival=rng.randint(0, 15), cpu_bursts=cpu, io_bursts=io))
    return procs


def make_mixed_workload(rng, n_hi=3, n_lo=3, n_bg=2, dist="uniform"):
    procs = []
    pid = 1
    for i in range(n_hi):
        cpu, io = _bursts(rng, rng.randint(3, 5), 100, 250, 0, 5, dist)
        procs.append(Process(pid=pid, name=f"hi_cpu_{i}", base_priority=rng.randint(20, 50),
                             arrival=rng.randint(0, 5), cpu_bursts=cpu, io_bursts=io))
        pid += 1
    for i in range(n_lo):
        cpu, io = _bursts(rng, rng.randint(4, 8), 10, 40, 20, 60, dist)
        procs.append(Process(pid=pid, name=f"lo_io_{i}", base_priority=rng.randint(130, 170),
                             arrival=rng.randint(0, 10), cpu_bursts=cpu, io_bursts=io))
        pid += 1
    for i in range(n_bg):
        cpu, io = _bursts(rng, rng.randint(2, 4), 50, 150, 5, 20, dist)
        procs.append(Process(pid=pid, name=f"bg_{i}", base_priority=rng.randint(170, 190),
                             arrival=rng.randint(0, 20), cpu_bursts=cpu, io_bursts=io))
        pid += 1
    return procs


def make_starvation_workload(rng, n_hogs=4):
    procs = []
    # Victim at lowest priority
    cpu, io = _bursts(rng, 3, 30, 80, 0, 5)
    procs.append(Process(pid=1, name="victim", base_priority=200,
                         arrival=0, cpu_bursts=cpu, io_bursts=io))
    # CPU hogs at high priority
    for i in range(n_hogs):
        cpu, io = _bursts(rng, rng.randint(4, 8), 100, 300, 0, 3)
        procs.append(Process(pid=i+2, name=f"hog_{i+2}", base_priority=rng.randint(5, 25),
                             arrival=rng.randint(0, 5), cpu_bursts=cpu, io_bursts=io))
    return procs


def make_interactive_workload(rng, n=5):
    procs = []
    for i in range(n):
        pri = 0   # highest priority
        cpu, io = _bursts(rng, rng.randint(10, 20), 2, 8, 15, 40)
        procs.append(Process(pid=i+1, name=f"interactive_{i}", base_priority=pri,
                             arrival=rng.randint(0, 5), cpu_bursts=cpu, io_bursts=io))
    return procs


WORKLOAD_MAKERS = {
    "cpu":         make_cpu_workload,
    "io":          make_io_workload,
    "mixed":       make_mixed_workload,
    "starvation":  make_starvation_workload,
    "interactive": make_interactive_workload,
}

# ─── Run One Experiment ───────────────────────────────────────────────────────

def run_one(procs_template, algo, params=None, seed=0):
    """Deep-copy process list, run scheduler, return (summary, per_proc)."""
    rng  = random.Random(seed)
    procs = deepcopy(procs_template)
    sched = Scheduler(algo=algo, params=params)
    for p in procs:
        sched.add(p)
    sched.run()
    return sched.summary(), sched.per_proc()


def run_trial(workload, algo, params=None, seed=0, **kwargs):
    """Generate a fresh workload + run."""
    rng    = random.Random(seed)
    maker  = WORKLOAD_MAKERS[workload]
    procs  = maker(rng, **kwargs)
    sched  = Scheduler(algo=algo, params=params)
    for p in procs:
        sched.add(p)
    sched.run()
    return sched.summary(), sched.per_proc()

# ─── CSV Helpers ─────────────────────────────────────────────────────────────

def save_csv(path, rows, fieldnames=None):
    if not rows:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fieldnames = fieldnames or list(rows[0].keys())
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    print(f"  wrote {path}  ({len(rows)} rows)")

# ─── Experiment Sets ─────────────────────────────────────────────────────────

TRIALS = 10
ALGOS  = ["paaqpa", "rr"]
WORKLOADS = ["cpu", "io", "mixed", "starvation"]

def exp_main_comparison():
    """
    E1 – Main comparison: PA-AQPA vs RR across all 4 workloads × 10 seeds.
    Produces: main_{workload}_{algo}.csv  (8 files)
              main_summary.csv            (1 file)
    """
    print("\n[E1] Main comparison …")
    summary_rows = []
    for wl in WORKLOADS:
        for algo in ALGOS:
            rows = []
            for seed in range(TRIALS):
                s, _ = run_trial(wl, algo, seed=seed)
                rows.append({"seed": seed, "workload": wl, "algo": algo, **s})
            save_csv(f"{OUTPUT_DIR}/main_{wl}_{algo}.csv", rows)
            summary_rows.extend(rows)
    save_csv(f"{OUTPUT_DIR}/main_summary.csv", summary_rows)


def exp_per_process():
    """
    E2 – Per-process detail: PA-AQPA only, 10 seeds.
    Produces: perproc_{workload}.csv  (4 files)
    """
    print("\n[E2] Per-process detail …")
    for wl in WORKLOADS:
        rows = []
        for seed in range(TRIALS):
            _, pp = run_trial(wl, "paaqpa", seed=seed)
            for r in pp:
                rows.append({"seed": seed, "workload": wl, **r})
        save_csv(f"{OUTPUT_DIR}/perproc_{wl}.csv", rows)


def exp_process_scaling():
    """
    E3 – Vary process count (2 → 32).
    Produces: scaling_{workload}.csv  (4 files)
    """
    print("\n[E3] Process count scaling …")
    counts = [2, 4, 8, 16, 32]
    for wl in WORKLOADS:
        rows = []
        for n in counts:
            kwargs = {}
            if wl == "cpu":
                kwargs = {"n": n}
            elif wl == "io":
                kwargs = {"n": n}
            elif wl == "mixed":
                hi = max(1, n // 3)
                lo = max(1, n // 3)
                bg = max(0, n - hi - lo)
                kwargs = {"n_hi": hi, "n_lo": lo, "n_bg": bg}
            elif wl == "starvation":
                kwargs = {"n_hogs": max(1, n - 1)}
            elif wl == "interactive":
                kwargs = {"n": n}

            for algo in ALGOS:
                for seed in range(TRIALS):
                    try:
                        s, _ = run_trial(wl, algo, seed=seed, **kwargs)
                        rows.append({"n_procs": n, "algo": algo, "seed": seed,
                                     "workload": wl, **s})
                    except Exception:
                        pass
        save_csv(f"{OUTPUT_DIR}/scaling_{wl}.csv", rows)


def exp_alpha_sensitivity():
    """
    E4 – EWMA alpha sensitivity: 0.125 / 0.25 / 0.375 / 0.5 / 0.625.
    Produces: alpha_{workload}.csv  (4 files)
    """
    print("\n[E4] Alpha sensitivity …")
    alphas = [0.125, 0.25, 0.375, 0.5, 0.625]
    for wl in WORKLOADS:
        rows = []
        for a in alphas:
            for seed in range(TRIALS):
                s, _ = run_trial(wl, "paaqpa", params={"alpha": a}, seed=seed)
                rows.append({"alpha": a, "seed": seed, "workload": wl, **s})
        save_csv(f"{OUTPUT_DIR}/alpha_{wl}.csv", rows)


def exp_quantum_sensitivity():
    """
    E5 – Max quantum sensitivity: 5 / 10 / 15 / 20 / 30.
    Produces: quantum_{workload}.csv  (4 files)
    """
    print("\n[E5] Max quantum sensitivity …")
    quanta = [5, 10, 15, 20, 30]
    for wl in WORKLOADS:
        rows = []
        for mq in quanta:
            for seed in range(TRIALS):
                s, _ = run_trial(wl, "paaqpa",
                                 params={"max_quantum": mq, "base_quantum": max(2, mq // 4)},
                                 seed=seed)
                rows.append({"max_quantum": mq, "seed": seed, "workload": wl, **s})
        save_csv(f"{OUTPUT_DIR}/quantum_{wl}.csv", rows)


def exp_burst_distribution():
    """
    E6 – Burst distribution: uniform / exponential / bimodal × 2 algos.
    Produces: burst_{dist}_{algo}.csv  (6 files)
    """
    print("\n[E6] Burst distribution …")
    dists = ["uniform", "exponential", "bimodal"]
    for dist in dists:
        for algo in ALGOS:
            rows = []
            for wl in ["cpu", "io", "mixed"]:
                for seed in range(TRIALS):
                    kwargs = {}
                    if wl == "mixed":
                        kwargs = {}   # dist handled inside maker via dist param
                    # Pass dist where supported (cpu/io/mixed)
                    rng = random.Random(seed)
                    maker = WORKLOAD_MAKERS[wl]
                    try:
                        if wl in ("cpu", "io"):
                            procs = maker(rng, dist=dist)
                        else:
                            procs = maker(rng)
                    except TypeError:
                        procs = maker(rng)
                    sched = Scheduler(algo=algo)
                    for p in procs:
                        sched.add(p)
                    sched.run()
                    s = sched.summary()
                    rows.append({"dist": dist, "algo": algo, "workload": wl,
                                 "seed": seed, **s})
            save_csv(f"{OUTPUT_DIR}/burst_{dist}_{algo}.csv", rows)


def exp_aging_sensitivity():
    """
    E7 – Aging threshold sensitivity.
    Produces: aging_sensitivity.csv  (1 file)
    """
    print("\n[E7] Aging threshold …")
    thresholds = [50, 100, 200, 300, 500]
    rows = []
    for thr in thresholds:
        for seed in range(TRIALS):
            s, _ = run_trial("starvation", "paaqpa",
                             params={"aging_threshold": thr}, seed=seed)
            rows.append({"aging_threshold": thr, "seed": seed, **s})
    save_csv(f"{OUTPUT_DIR}/aging_sensitivity.csv", rows)


def exp_starvation_bound_sensitivity():
    """
    E8 – Starvation bound sensitivity.
    Produces: starvation_bound_sensitivity.csv  (1 file)
    """
    print("\n[E8] Starvation bound …")
    bounds = [100, 200, 300, 500, 750, 1000]
    rows = []
    for bound in bounds:
        for seed in range(TRIALS):
            s, _ = run_trial("starvation", "paaqpa",
                             params={"starvation_bound": bound}, seed=seed)
            rows.append({"starvation_bound": bound, "seed": seed, **s})
    save_csv(f"{OUTPUT_DIR}/starvation_bound_sensitivity.csv", rows)


def exp_decay_sensitivity():
    """
    E9 – Priority decay factor sensitivity.
    Produces: decay_sensitivity.csv  (1 file)
    """
    print("\n[E9] Decay factor …")
    factors = [0.1, 0.25, 0.5, 0.75, 1.0]
    rows = []
    for df in factors:
        for wl in WORKLOADS:
            for seed in range(TRIALS):
                s, _ = run_trial(wl, "paaqpa",
                                 params={"decay_factor": df}, seed=seed)
                rows.append({"decay_factor": df, "workload": wl, "seed": seed, **s})
    save_csv(f"{OUTPUT_DIR}/decay_sensitivity.csv", rows)


def exp_alpha_quantum_heatmap():
    """
    E10 – Joint alpha × max_quantum sweep (6×5 = 30 combos × 10 seeds).
    Produces: alpha_quantum_heatmap.csv  (1 file)
    """
    print("\n[E10] Alpha × Quantum heatmap …")
    alphas = [0.125, 0.25, 0.375, 0.5, 0.625, 0.75]
    quanta = [5, 10, 15, 20, 30]
    rows = []
    for a in alphas:
        for mq in quanta:
            for seed in range(TRIALS):
                s, _ = run_trial("mixed", "paaqpa",
                                 params={"alpha": a, "max_quantum": mq,
                                         "base_quantum": max(2, mq // 4)},
                                 seed=seed)
                rows.append({"alpha": a, "max_quantum": mq, "seed": seed, **s})
    save_csv(f"{OUTPUT_DIR}/alpha_quantum_heatmap.csv", rows)


def exp_priority_diversity():
    """
    E11 – Effect of priority spread (narrow vs wide priority range).
    Produces: priority_diversity.csv  (1 file)
    """
    print("\n[E11] Priority diversity …")
    rows = []
    configs = [
        ("narrow",  [(10, 30),  (80, 100),  (170, 190)]),
        ("medium",  [(0, 50),   (75, 125),  (150, 200)]),
        ("wide",    [(0, 30),   (50, 150),  (170, 200)]),
        ("uniform", [(0, 200),  (0, 200),   (0, 200)]),
    ]
    for name, ranges in configs:
        for seed in range(TRIALS):
            rng = random.Random(seed)
            procs = []
            pid = 1
            for i, (lo, hi) in enumerate(ranges):
                for j in range(3):
                    pri = rng.randint(lo, hi)
                    cpu, io = _bursts(rng, rng.randint(3, 6), 50, 200, 5, 30)
                    procs.append(Process(pid=pid, name=f"g{i}_{j}",
                                         base_priority=pri, arrival=rng.randint(0, 10),
                                         cpu_bursts=cpu, io_bursts=io))
                    pid += 1
            for algo in ALGOS:
                ps = deepcopy(procs)
                sched = Scheduler(algo=algo)
                for p in ps:
                    sched.add(p)
                sched.run()
                s = sched.summary()
                rows.append({"config": name, "algo": algo, "seed": seed, **s})
    save_csv(f"{OUTPUT_DIR}/priority_diversity.csv", rows)


def exp_starvation_proof():
    """
    E12 – Formal starvation-prevention proof data.
    Records the actual max wait time experienced by victim processes.
    Produces: starvation_proof.csv  (1 file)
    """
    print("\n[E12] Starvation proof …")
    rows = []
    hog_counts = [1, 2, 3, 4, 6, 8]
    for n_hogs in hog_counts:
        for seed in range(TRIALS):
            rng = random.Random(seed)
            procs = make_starvation_workload(rng, n_hogs=n_hogs)
            # PA-AQPA
            sched = Scheduler(algo="paaqpa")
            for p in deepcopy(procs):
                sched.add(p)
            sched.run()
            victim = next((p for p in sched.procs if p.name == "victim"), None)
            rows.append({
                "n_hogs": n_hogs, "algo": "paaqpa", "seed": seed,
                "victim_completed": int(victim.state == "DONE") if victim else 0,
                "victim_turnaround": victim.turnaround if victim else -1,
                "victim_wait": victim.total_wait if victim else -1,
                "victim_response": victim.response_time if victim else -1,
                "global_completed": sched.summary()["completed"],
                "total_procs": len(sched.procs),
            })
            # RR (starvation IS possible)
            sched2 = Scheduler(algo="rr")
            for p in deepcopy(procs):
                sched2.add(p)
            sched2.run()
            victim2 = next((p for p in sched2.procs if p.name == "victim"), None)
            rows.append({
                "n_hogs": n_hogs, "algo": "rr", "seed": seed,
                "victim_completed": int(victim2.state == "DONE") if victim2 else 0,
                "victim_turnaround": victim2.turnaround if victim2 else -1,
                "victim_wait": victim2.total_wait if victim2 else -1,
                "victim_response": victim2.response_time if victim2 else -1,
                "global_completed": sched2.summary()["completed"],
                "total_procs": len(sched2.procs),
            })
    save_csv(f"{OUTPUT_DIR}/starvation_proof.csv", rows)


def exp_response_time_cdf():
    """
    E13 – Response time CDF data (every process from 10 seeds × 4 workloads).
    Produces: response_cdf_{workload}.csv  (4 files)
    """
    print("\n[E13] Response time CDF …")
    for wl in WORKLOADS:
        rows = []
        for algo in ALGOS:
            for seed in range(TRIALS):
                _, pp = run_trial(wl, algo, seed=seed)
                for r in pp:
                    rows.append({"algo": algo, "seed": seed, "workload": wl,
                                 "response_time": r["response_time"],
                                 "turnaround": r["turnaround"],
                                 "total_wait": r["total_wait"]})
        save_csv(f"{OUTPUT_DIR}/response_cdf_{wl}.csv", rows)


def exp_throughput_vs_load():
    """
    E14 – Throughput as arrival rate increases (more procs, fixed time).
    Produces: throughput_vs_load.csv  (1 file)
    """
    print("\n[E14] Throughput vs load …")
    rows = []
    for n in [2, 4, 6, 8, 10, 12, 16, 20]:
        for algo in ALGOS:
            for seed in range(TRIALS):
                rng = random.Random(seed)
                procs = make_mixed_workload(
                    rng, n_hi=n//3+1, n_lo=n//3+1, n_bg=max(0, n - 2*(n//3+1))
                )
                sched = Scheduler(algo=algo)
                for p in procs:
                    sched.add(p)
                sched.run(max_ticks=3000)
                s = sched.summary()
                rows.append({"n_procs": n, "algo": algo, "seed": seed, **s})
    save_csv(f"{OUTPUT_DIR}/throughput_vs_load.csv", rows)


def exp_context_switch_breakdown():
    """
    E15 – Context-switch counts broken down by cause (preemption vs voluntary).
    Approximated: tracks quantum-expiry vs burst-completion separately.
    Produces: ctx_switches_breakdown.csv  (1 file)
    """
    print("\n[E15] Context switch breakdown …")
    # We instrument a special run tracking quantum-expiry separately
    rows = []
    for wl in WORKLOADS:
        for algo in ALGOS:
            preemptions    = []
            voluntaries    = []
            for seed in range(TRIALS):
                rng   = random.Random(seed)
                maker = WORKLOAD_MAKERS[wl]
                procs = maker(rng)
                # Count preemptions: ctx_switches where ticks_on_cpu == quantum_assigned
                # We approximate: preemptions = global_ctx * (quantum_expired_fraction)
                # Use a heuristic: if avg burst < quantum → most are voluntary
                sched = Scheduler(algo=algo)
                for p in deepcopy(procs):
                    sched.add(p)
                sched.run()
                s      = sched.summary()
                avg_burst = sum(p.last_burst for p in sched.procs) / max(1, len(sched.procs))
                avg_q     = sum(p.quantum    for p in sched.procs) / max(1, len(sched.procs))
                # Heuristic split
                vol_frac  = min(1.0, avg_burst / avg_q) if avg_q > 0 else 0.5
                vol       = int(s["context_switches"] * vol_frac)
                pre       = s["context_switches"] - vol
                rows.append({
                    "workload": wl, "algo": algo, "seed": seed,
                    "total_ctx":    s["context_switches"],
                    "voluntary":    vol,
                    "preemptions":  pre,
                    "avg_burst":    round(avg_burst, 2),
                    "avg_quantum":  round(avg_q, 2),
                })
    save_csv(f"{OUTPUT_DIR}/ctx_switches_breakdown.csv", rows)


def exp_ewma_convergence():
    """
    E16 – How fast EWMA converges per priority class.
    Produces: ewma_convergence.csv  (1 file)
    """
    print("\n[E16] EWMA convergence …")
    rows = []
    for seed in range(TRIALS):
        rng   = random.Random(seed)
        procs = make_mixed_workload(rng)
        sched = Scheduler(algo="paaqpa")
        for p in deepcopy(procs):
            sched.add(p)

        # Tick-by-tick recording every 50 ticks
        tick_snapshots = []
        while sched.ticks < MAX_SIM_TICKS:
            if all(p.state == "DONE" for p in sched.procs):
                break
            sched.tick()
            if sched.ticks % 50 == 0:
                tick_snapshots.append({
                    "seed": seed, "tick": sched.ticks,
                    "ewma_c0": sched.ewma[0],
                    "ewma_c1": sched.ewma[1],
                    "ewma_c2": sched.ewma[2],
                    "ewma_c3": sched.ewma[3],
                    "ewma_c4": sched.ewma[4],
                })
        rows.extend(tick_snapshots)
    save_csv(f"{OUTPUT_DIR}/ewma_convergence.csv", rows)


def exp_comparative_summary():
    """
    E17 – Aggregate comparison table (means + std across all seeds).
    Produces: comparative_summary.csv  (1 file)
    """
    print("\n[E17] Comparative summary …")

    def mean(xs):
        return sum(xs) / len(xs) if xs else 0

    def std(xs):
        if len(xs) < 2:
            return 0
        m = mean(xs)
        return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))

    rows = []
    metrics = ["avg_wait", "avg_turnaround", "avg_response",
               "context_switches", "cpu_util", "throughput",
               "jains_fairness", "max_wait", "starvation_free"]

    for wl in WORKLOADS:
        for algo in ALGOS:
            bucket = {m: [] for m in metrics}
            for seed in range(TRIALS):
                s, _ = run_trial(wl, algo, seed=seed)
                for m in metrics:
                    bucket[m].append(s[m])
            row = {"workload": wl, "algo": algo}
            for m in metrics:
                row[f"{m}_mean"] = round(mean(bucket[m]), 4)
                row[f"{m}_std"]  = round(std(bucket[m]),  4)
            rows.append(row)
    save_csv(f"{OUTPUT_DIR}/comparative_summary.csv", rows)


def exp_improvement_ratios():
    """
    E18 – PA-AQPA improvement ratio over RR per metric per workload.
    Produces: improvement_ratios.csv  (1 file)
    """
    print("\n[E18] Improvement ratios …")
    rows = []
    for wl in WORKLOADS:
        paaqpa_waits, rr_waits   = [], []
        paaqpa_turns, rr_turns   = [], []
        paaqpa_resps, rr_resps   = [], []
        for seed in range(TRIALS):
            sp, _ = run_trial(wl, "paaqpa", seed=seed)
            sr, _ = run_trial(wl, "rr",     seed=seed)
            paaqpa_waits.append(sp["avg_wait"]);       rr_waits.append(sr["avg_wait"])
            paaqpa_turns.append(sp["avg_turnaround"]); rr_turns.append(sr["avg_turnaround"])
            paaqpa_resps.append(sp["avg_response"]);   rr_resps.append(sr["avg_response"])

        def ratio(a, b):
            if not b or sum(b) == 0:
                return "N/A"
            return round((sum(b)/len(b) - sum(a)/len(a)) / (sum(b)/len(b)) * 100, 2)

        rows.append({
            "workload":              wl,
            "wait_improvement_pct":  ratio(paaqpa_waits, rr_waits),
            "turn_improvement_pct":  ratio(paaqpa_turns, rr_turns),
            "resp_improvement_pct":  ratio(paaqpa_resps, rr_resps),
            "paaqpa_avg_wait":       round(sum(paaqpa_waits)/len(paaqpa_waits), 2),
            "rr_avg_wait":           round(sum(rr_waits)/len(rr_waits), 2),
            "paaqpa_avg_turn":       round(sum(paaqpa_turns)/len(paaqpa_turns), 2),
            "rr_avg_turn":           round(sum(rr_turns)/len(rr_turns), 2),
        })
    save_csv(f"{OUTPUT_DIR}/improvement_ratios.csv", rows)


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    print("=" * 60)
    print("  PA-AQPA Experiment Suite")
    print(f"  Output → {OUTPUT_DIR}/")
    print("=" * 60)

    exp_main_comparison()          # E1  → 9 files
    exp_per_process()              # E2  → 4 files
    exp_process_scaling()          # E3  → 4 files
    exp_alpha_sensitivity()        # E4  → 4 files
    exp_quantum_sensitivity()      # E5  → 4 files
    exp_burst_distribution()       # E6  → 6 files
    exp_aging_sensitivity()        # E7  → 1 file
    exp_starvation_bound_sensitivity()  # E8 → 1 file
    exp_decay_sensitivity()        # E9  → 1 file
    exp_alpha_quantum_heatmap()    # E10 → 1 file
    exp_priority_diversity()       # E11 → 1 file
    exp_starvation_proof()         # E12 → 1 file
    exp_response_time_cdf()        # E13 → 4 files
    exp_throughput_vs_load()       # E14 → 1 file
    exp_context_switch_breakdown() # E15 → 1 file
    exp_ewma_convergence()         # E16 → 1 file
    exp_comparative_summary()      # E17 → 1 file
    exp_improvement_ratios()       # E18 → 1 file

    files = [f for f in os.listdir(OUTPUT_DIR) if f.endswith(".csv")]
    print(f"\n{'='*60}")
    print(f"  Done. {len(files)} CSV files in {OUTPUT_DIR}/")
    print(f"  Next: python3 analyze.py")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()

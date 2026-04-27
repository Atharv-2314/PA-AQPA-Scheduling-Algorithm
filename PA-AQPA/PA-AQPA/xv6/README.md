# PA-AQPA: Priority-Aware Adaptive Quantum with Predictive Aging

## A Hybrid CPU Scheduler for xv6, Linux, and Windows Comparison

**Authors:** Aarnav Arya (2410110008), Adwit Gautam (2410110026), Akshaj Singh (2410110038), Atharv Kumar (2410110086)

---

## 1. Project Summary

Modern operating systems must balance fairness, responsiveness, and throughput across workloads that shift unpredictably between CPU-bound batch jobs and latency-sensitive interactive tasks. Classical schedulers force a hard trade-off: Round Robin (RR) provides fairness but wastes CPU on context switches when the quantum is poorly sized, while Priority Scheduling delivers responsiveness but starves low-priority processes.

**PA-AQPA** (Priority-Aware Adaptive Quantum with Predictive Aging) is a novel hybrid scheduler that closes three gaps left by prior dynamic-quantum and hybrid approaches:

1. **Predictive Quantum Sizing**: Instead of computing the time quantum from the *current* mean burst time (which reacts too late to workload shifts), PA-AQPA uses an Exponentially Weighted Moving Average (EWMA) burst predictor per priority class. This lets the quantum anticipate workload changes rather than chase them, reducing both wasted ticks and unnecessary context switches.

2. **Priority-Scaled Quantum Multiplier**: Higher-priority processes receive a quantum that is a tunable multiple of the base adaptive quantum, while lower-priority processes get a fraction. This ensures that high-priority CPU-bound tasks can make progress without being artificially preempted, while low-priority interactive tasks get short quanta for fast response times.

3. **Bounded Aging with Exponential Decay**: Rather than simple linear aging (which can cause "priority ping-pong" where boosted processes immediately get demoted), PA-AQPA uses aging with a guaranteed starvation bound of `T_max` ticks, combined with exponential priority decay after a process receives CPU. This provides a provable starvation-freedom guarantee while preventing priority oscillation.

### What makes PA-AQPA novel vs. prior work?

| Prior Work | Limitation | PA-AQPA Solution |
|---|---|---|
| Noon et al. (2011) — Dynamic quantum from mean burst | Reactive: quantum lags workload changes | EWMA predictor adapts proactively |
| Bandara & Rajapaksha (2022) — Improved RR | No priority awareness; all processes get same quantum | Priority-scaled multiplier |
| Standard aging (e.g., Linux O(1)) | Linear aging causes priority oscillation | Bounded aging + exponential decay |
| MLFQ (Solaris, FreeBSD) | Complex multi-queue management; hard to tune | Single ready queue per priority with adaptive quantum |

---

## 2. Repository Structure

```
PA-AQPA/
├── README.md                    # This file
├── xv6/                         # xv6 implementation
│   ├── pa_aqpa.patch            # Unified diff against xv6-public (MIT PDOS)
│   ├── build_and_run.sh         # One-command build + QEMU launch
│   ├── test_programs/           # CPU-bound, I/O-bound, mixed, interactive tests
│   │   ├── cpu_bound.c
│   │   ├── io_bound.c
│   │   ├── mixed_workload.c
│   │   ├── interactive.c
│   │   └── starvation_test.c
│   └── README.md                # xv6-specific build instructions
├── linux/                       # Linux kernel module / scheduler class
│   ├── sched_paaqpa.c           # Loadable kernel module skeleton
│   ├── Makefile
│   ├── build_instructions.md
│   └── test_harness.sh
├── simulator_windows/           # Cross-platform user-space simulator
│   ├── simulator.py             # Python simulator (runs on Windows/Linux/Mac)
│   ├── workloads.py             # Workload generator
│   ├── config.json              # Default parameters
│   └── README.md
├── experiments/                  # Experiment automation
│   ├── run_all_experiments.sh
│   ├── collect_xv6_metrics.sh
│   ├── collect_linux_metrics.sh
│   ├── workload_generator.py
│   └── config.yaml
├── analysis/                     # Data analysis
│   ├── analyze.py               # Main analysis script (plots + stats)
│   ├── parse_xv6_logs.py
│   ├── parse_linux_logs.py
│   └── sample_data/
├── report/                       # Final report
│   ├── report_outline.md
│   └── figures/
├── slides/                       # Presentation
│   └── presentation_outline.md
└── references.md                 # All citations and GitHub link mapping
```

---

## 3. Quick Start (xv6)

```bash
# Prerequisites: gcc, make, qemu-system-i386 (or qemu-system-x86_64)
# On Ubuntu/Debian:
sudo apt-get install build-essential qemu-system-x86 git

# Clone xv6
git clone https://github.com/mit-pdos/xv6-public.git
cd xv6-public

# Apply PA-AQPA patch
patch -p1 < ../xv6/pa_aqpa.patch

# Build and run
make clean && make
make qemu-nox

# Inside xv6 shell, run test programs:
$ cpu_bound &
$ io_bound &
$ mixed_workload &
$ schedstat          # Print scheduler statistics
```

---

## 4. Grading Rubric Mapping

| Criterion | Weight | Artifacts Demonstrating It |
|---|---|---|
| **Novelty** | 20% | EWMA burst predictor, priority-scaled quantum, bounded aging with decay — see `report/` §3 Design, `README.md` §1 novelty table |
| **Correctness** | 25% | xv6 patch compiles and runs under QEMU; test programs exercise all code paths; starvation test proves bounded wait; simulator cross-validates against kernel implementation |
| **Completeness** | 20% | Three implementations (xv6 kernel, Linux module skeleton, cross-platform simulator); full workload suite; instrumentation hooks |
| **Evaluation Rigor** | 20% | 7 metrics measured; N≥10 trials per config; 95% CIs; t-tests for pairwise comparisons; ANOVA across schedulers; fairness index over time |
| **Documentation** | 10% | Design document with pseudocode, complexity analysis, proofs; report outline; reproducibility checklist; this README |
| **Reproducibility** | 5% | One-command build scripts; deterministic seeds; raw data + analysis scripts; VM setup instructions |

---

## 5. Reproducibility Checklist

- [ ] Clone xv6-public and apply patch — builds without errors
- [ ] `make qemu-nox` boots xv6 with PA-AQPA scheduler
- [ ] All test programs compile and run inside xv6
- [ ] `schedstat` command prints per-process metrics
- [ ] Simulator produces consistent results across platforms (deterministic seed)
- [ ] `run_all_experiments.sh` completes and generates CSV files
- [ ] `analyze.py` reads CSVs and produces all required plots
- [ ] Linux module compiles against specified kernel headers (or skip with documented fallback)

---

## 6. References & GitHub Link Mapping

See `references.md` for full citations. GitHub links used:

| GitHub / Web Link | How Used |
|---|---|
| [CS1550 Lab 3 — Priority Scheduling for xv6](https://people.cs.pitt.edu/~xil160/CS1550_Fall2019/data/Lab3.pdf) | Reference for xv6 priority scheduler structure; guided `proc.h` and `proc.c` modifications |
| [subsixx/Round-Robin-and-Priority-Scheduling-xv6](https://github.com/subsixx/Round-Robin-and-Priority-Scheduling-xv6) | Baseline RR+Priority implementation reference; test methodology inspiration |
| [dkreme514/Xv6-Priority-Based-Scheduler](https://github.com/dkreme514/Xv6-Priority-Based-Scheduler) | Reference for priority-based scheduler in xv6; Makefile and syscall patterns |
| [Harshal Shree — xv6 ps, nice, priority scheduling](https://medium.com/@harshalshree03/xv6-implementing-ps-nice-system-calls-and-priority-scheduling-b12fa10494e4) | Implementation guide for `setpriority`/`getpriority` syscalls; `ps` command pattern |
| [YouTube — xv6 scheduler tutorial](https://www.youtube.com/watch?v=e-7tDx26zTk) | Conceptual reference for understanding xv6 scheduler internals |

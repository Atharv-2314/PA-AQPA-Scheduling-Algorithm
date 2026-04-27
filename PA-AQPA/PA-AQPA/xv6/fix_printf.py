#!/usr/bin/env python3
"""
Fixes two bugs:
1. sys_schedstat in proc.c uses %-Ns width format specifiers that xv6's
   cprintf does not support — replace with a plain fixed header + basic %d/%s rows.
2. User programs use %ld which xv6 cprintf doesn't support — replace with %d.
Run from ~/xv6-public/
"""
import re, sys, os

# ── 1. Fix proc.c ──────────────────────────────────────────────────────────

OLD_STAT = '''\
  cprintf("%-5s %-8s %-4s %-6s %-6s %-6s %-6s %-5s %-5s %-5s %s\\n",
          "PID", "STATE", "PRI", "BASE", "WAIT", "RESP", "BURST", "EWMA", "QNTM", "CTSW", "NAME");

  acquire(&ptable.lock);
  for(p = ptable.proc; p < &ptable.proc[NPROC]; p++){
    if(p->state == UNUSED)
      continue;
    char *state = p->state == RUNNABLE ? "RNBL" :
                  p->state == RUNNING  ? "RUN"  :
                  p->state == SLEEPING ? "SLP"  :
                  p->state == ZOMBIE   ? "ZMBI" : "EMBR";
    cprintf("%-5d %-8s %-4d %-6d %-6d %-6d %-6d %-5d %-5d %-5d %s\\n",
            p->pid, state, p->priority, p->base_priority,
            p->total_wait, p->response_time, p->last_burst,
            p->burst_estimate, p->quantum_assigned, p->context_switches, p->name);
  }'''

NEW_STAT = '''\
  cprintf("PID   STATE    PRI  BASE  WAIT  RESP  BURST EWMA  QNTM  CTSW  NAME\\n");

  acquire(&ptable.lock);
  for(p = ptable.proc; p < &ptable.proc[NPROC]; p++){
    if(p->state == UNUSED)
      continue;
    char *state = p->state == RUNNABLE ? "RNBL" :
                  p->state == RUNNING  ? "RUN " :
                  p->state == SLEEPING ? "SLP " :
                  p->state == ZOMBIE   ? "ZMBI" : "EMBR";
    cprintf("%d\t%s\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%s\\n",
            p->pid, state, p->priority, p->base_priority,
            p->total_wait, p->response_time, p->last_burst,
            p->burst_estimate, p->quantum_assigned, p->context_switches, p->name);
  }'''

path = 'proc.c'
with open(path) as f:
    src = f.read()

if OLD_STAT in src:
    src = src.replace(OLD_STAT, NEW_STAT, 1)
    with open(path, 'w') as f:
        f.write(src)
    print("proc.c: fixed sys_schedstat cprintf format strings")
else:
    print("proc.c: WARNING — could not find old cprintf block (already fixed?)")

# ── 2. Fix %ld → %d in user programs ──────────────────────────────────────

user_progs = ['cpu_bound.c', 'io_bound.c', 'mixed_workload.c',
              'interactive.c', 'starvation_test.c', 'schedstat_cmd.c']

for fname in user_progs:
    if not os.path.exists(fname):
        print(f"{fname}: not found, skipping")
        continue
    with open(fname) as f:
        src = f.read()
    new_src = src.replace('%ld', '%d')
    if new_src != src:
        with open(fname, 'w') as f:
            f.write(new_src)
        print(f"{fname}: replaced %ld with %d")
    else:
        print(f"{fname}: no %ld found")

print("\nDone. Run: make clean && make")

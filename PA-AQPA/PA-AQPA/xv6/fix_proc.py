#!/usr/bin/env python3
"""
Replaces the old round-robin scheduler() in xv6 proc.c with the
PA-AQPA priority scheduler, and inserts compute_quantum() after it.
Run from the xv6-public directory: python3 fix_proc.py
"""

import sys

OLD_SCHEDULER = '''\
void
scheduler(void)
{
  struct proc *p;
  struct cpu *c = mycpu();
  c->proc = 0;
  
  for(;;){
    // Enable interrupts on this processor.
    sti();

    // Loop over process table looking for process to run.
    acquire(&ptable.lock);
    for(p = ptable.proc; p < &ptable.proc[NPROC]; p++){
      if(p->state != RUNNABLE)
        continue;

      // Switch to chosen process.  It is the process's job
      // to release ptable.lock and then reacquire it
      // before jumping back to us.
      c->proc = p;
      switchuvm(p);
      p->state = RUNNING;

      swtch(&(c->scheduler), p->context);
      switchkvm();

      // Process is done running for now.
      // It should have changed its p->state before coming back.
      c->proc = 0;
    }
    release(&ptable.lock);

  }
}'''

NEW_SCHEDULER = '''\
void
scheduler(void)
{
  struct proc *p;
  struct cpu *c = mycpu();
  struct proc *best;
  int start_tick;
  c->proc = 0;

  for(;;){
    // Enable interrupts on this processor.
    sti();

    acquire(&ptable.lock);

    // Phase 1: age all RUNNABLE processes
    for(p = ptable.proc; p < &ptable.proc[NPROC]; p++){
      if(p->state == RUNNABLE){
        p->wait_ticks++;
        p->total_wait++;
        if(p->wait_ticks >= STARVATION_BOUND){
          p->priority = 0;
          p->wait_ticks = 0;
        } else if(p->wait_ticks >= AGING_THRESHOLD){
          p->priority = p->priority - AGING_BOOST;
          if(p->priority < 0) p->priority = 0;
          p->wait_ticks = 0;
        }
      }
    }

    // Phase 2: pick highest-priority RUNNABLE process
    // (lowest number = highest priority; break ties by longest wait)
    best = 0;
    for(p = ptable.proc; p < &ptable.proc[NPROC]; p++){
      if(p->state != RUNNABLE)
        continue;
      if(best == 0 || p->priority < best->priority ||
         (p->priority == best->priority && p->wait_ticks > best->wait_ticks))
        best = p;
    }

    if(best != 0){
      // Phase 3: dispatch
      best->quantum_assigned = compute_quantum(best);
      best->ticks_on_cpu = 0;
      best->wait_ticks = 0;

      if(!best->first_scheduled){
        best->response_time = ticks - best->creation_tick;
        best->first_scheduled = 1;
      }

      start_tick = ticks;
      c->proc = best;
      switchuvm(best);
      best->state = RUNNING;
      swtch(&(c->scheduler), best->context);
      switchkvm();

      // Phase 4: update stats after process yields/exits
      {
        int actual_burst = ticks - start_tick;
        if(actual_burst < 1) actual_burst = 1;

        int cls = priority_to_class(best->priority);
        if(ewma_burst[cls] == 0)
          ewma_burst[cls] = actual_burst;
        else
          ewma_burst[cls] = (EWMA_ALPHA_NUM * actual_burst +
                             (EWMA_ALPHA_DEN - EWMA_ALPHA_NUM) * ewma_burst[cls])
                            / EWMA_ALPHA_DEN;
        best->burst_estimate = ewma_burst[cls];
        best->last_burst = actual_burst;

        // Decay priority back toward base
        if(best->priority < best->base_priority){
          int boost = best->base_priority - best->priority;
          int decay = boost * DECAY_FACTOR_NUM / DECAY_FACTOR_DEN;
          if(decay < 1) decay = 1;
          best->priority += decay;
          if(best->priority > best->base_priority)
            best->priority = best->base_priority;
        }

        global_context_switches++;
        best->context_switches++;
      }

      c->proc = 0;
    }

    release(&ptable.lock);
  }
}

static int
compute_quantum(struct proc *p)
{
  int cls = priority_to_class(p->priority);
  int base_q = (ewma_burst[cls] > 0) ? ewma_burst[cls] : BASE_QUANTUM;

  if(base_q < MIN_QUANTUM) base_q = MIN_QUANTUM;
  if(base_q > MAX_QUANTUM) base_q = MAX_QUANTUM;

  int quantum;
  if(p->priority < 80)
    quantum = base_q * QUANTUM_SCALE_HI_N / QUANTUM_SCALE_HI_D;
  else if(p->priority > 159)
    quantum = base_q * QUANTUM_SCALE_LO_N / QUANTUM_SCALE_LO_D;
  else
    quantum = base_q;

  if(quantum < MIN_QUANTUM) quantum = MIN_QUANTUM;
  if(quantum > MAX_QUANTUM) quantum = MAX_QUANTUM;
  return quantum;
}'''

path = 'proc.c'
try:
    with open(path, 'r') as f:
        src = f.read()
except FileNotFoundError:
    print(f"ERROR: {path} not found. Run this script from ~/xv6-public/")
    sys.exit(1)

if OLD_SCHEDULER not in src:
    print("ERROR: Could not find the old scheduler() — has it already been replaced?")
    print("Searching for 'scheduler(void)' in file...")
    for i, line in enumerate(src.splitlines(), 1):
        if 'scheduler(void)' in line:
            print(f"  Found at line {i}: {line}")
    sys.exit(1)

new_src = src.replace(OLD_SCHEDULER, NEW_SCHEDULER, 1)

with open(path, 'w') as f:
    f.write(new_src)

print("Done. scheduler() replaced and compute_quantum() inserted.")
print("Now run: make clean && make")

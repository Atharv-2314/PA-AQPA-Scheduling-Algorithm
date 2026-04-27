#include "types.h"
#include "defs.h"
#include "param.h"
#include "memlayout.h"
#include "mmu.h"
#include "x86.h"
#include "proc.h"
#include "spinlock.h"

// ========================
// PA-AQPA Scheduler Globals
// ========================
#define MIN_QUANTUM          1
#define MAX_QUANTUM          20
#define BASE_QUANTUM         5
#define EWMA_ALPHA_NUM       3
#define EWMA_ALPHA_DEN       8
#define AGING_THRESHOLD      200
#define AGING_BOOST          20
#define DECAY_FACTOR_NUM     1
#define DECAY_FACTOR_DEN     2
#define QUANTUM_SCALE_HI_N   3
#define QUANTUM_SCALE_HI_D   2
#define QUANTUM_SCALE_LO_N   1
#define QUANTUM_SCALE_LO_D   2

static int ewma_burst[NUM_PRIORITY_CLASSES];
static int global_context_switches = 0;

static int
priority_to_class(int priority)
{
  if(priority < 40)  return 0;
  if(priority < 80)  return 1;
  if(priority < 120) return 2;
  if(priority < 160) return 3;
  return 4;
}

static int
compute_quantum(struct proc *p)
{
  int cls = priority_to_class(p->priority);
  int base_q, quantum;
  if(ewma_burst[cls] > 0)
    base_q = ewma_burst[cls];
  else
    base_q = BASE_QUANTUM;
  if(base_q < MIN_QUANTUM) base_q = MIN_QUANTUM;
  if(base_q > MAX_QUANTUM) base_q = MAX_QUANTUM;
  if(p->priority < 80)
    quantum = base_q * QUANTUM_SCALE_HI_N / QUANTUM_SCALE_HI_D;
  else if(p->priority > 159)
    quantum = base_q * QUANTUM_SCALE_LO_N / QUANTUM_SCALE_LO_D;
  else
    quantum = base_q;
  if(quantum < MIN_QUANTUM) quantum = MIN_QUANTUM;
  if(quantum > MAX_QUANTUM) quantum = MAX_QUANTUM;
  return quantum;
}

struct {
  struct spinlock lock;
  struct proc proc[NPROC];
} ptable;

static struct proc *initproc;

int nextpid = 1;
extern void forkret(void);
extern void trapret(void);

static void wakeup1(void *chan);

void
pinit(void)
{
  initlock(&ptable.lock, "ptable");
}

// Must be called with interrupts disabled
int
cpuid() {
  return mycpu()-cpus;
}

// Must be called with interrupts disabled to avoid the caller being
// rescheduled between reading lapicid and running through the loop.
struct cpu*
mycpu(void)
{
  int apicid, i;
  
  if(readeflags()&FL_IF)
    panic("mycpu called with interrupts enabled\n");
  
  apicid = lapicid();
  // APIC IDs are not guaranteed to be contiguous. Maybe we should have
  // a reverse map, or reserve a register to store &cpus[i].
  for (i = 0; i < ncpu; ++i) {
    if (cpus[i].apicid == apicid)
      return &cpus[i];
  }
  panic("unknown apicid\n");
}

// Disable interrupts so that we are not rescheduled
// while reading proc from the cpu structure
struct proc*
myproc(void) {
  struct cpu *c;
  struct proc *p;
  pushcli();
  c = mycpu();
  p = c->proc;
  popcli();
  return p;
}

//PAGEBREAK: 32
// Look in the process table for an UNUSED proc.
// If found, change state to EMBRYO and initialize
// state required to run in the kernel.
// Otherwise return 0.
static struct proc*
allocproc(void)
{
  struct proc *p;
  char *sp;

  acquire(&ptable.lock);

  for(p = ptable.proc; p < &ptable.proc[NPROC]; p++)
    if(p->state == UNUSED)
      goto found;

  release(&ptable.lock);
  return 0;

found:
  p->state = EMBRYO;
  p->pid = nextpid++;

  release(&ptable.lock);

  // Allocate kernel stack.
  if((p->kstack = kalloc()) == 0){
    p->state = UNUSED;
    return 0;
  }
  sp = p->kstack + KSTACKSIZE;

  // Leave room for trap frame.
  sp -= sizeof *p->tf;
  p->tf = (struct trapframe*)sp;

  // Set up new context to start executing at forkret,
  // which returns to trapret.
  sp -= 4;
  *(uint*)sp = (uint)trapret;

  sp -= sizeof *p->context;
  p->context = (struct context*)sp;
  memset(p->context, 0, sizeof *p->context);
  p->context->eip = (uint)forkret;

  // PA-AQPA initialization
  p->priority = DEFAULT_PRIORITY;
  p->base_priority = DEFAULT_PRIORITY;
  p->burst_estimate = 0;
  p->last_burst = 0;
  p->wait_ticks = 0;
  p->total_wait = 0;
  p->total_turnaround = 0;
  p->response_time = -1;
  p->first_scheduled = 0;
  p->context_switches = 0;
  p->creation_tick = ticks;
  p->quantum_assigned = BASE_QUANTUM;
  p->ticks_on_cpu = 0;

  return p;
}

//PAGEBREAK: 32
// Set up first user process.
void
userinit(void)
{
  struct proc *p;
  extern char _binary_initcode_start[], _binary_initcode_size[];

  p = allocproc();
  
  initproc = p;
  if((p->pgdir = setupkvm()) == 0)
    panic("userinit: out of memory?");
  inituvm(p->pgdir, _binary_initcode_start, (int)_binary_initcode_size);
  p->sz = PGSIZE;
  memset(p->tf, 0, sizeof(*p->tf));
  p->tf->cs = (SEG_UCODE << 3) | DPL_USER;
  p->tf->ds = (SEG_UDATA << 3) | DPL_USER;
  p->tf->es = p->tf->ds;
  p->tf->ss = p->tf->ds;
  p->tf->eflags = FL_IF;
  p->tf->esp = PGSIZE;
  p->tf->eip = 0;  // beginning of initcode.S

  safestrcpy(p->name, "initcode", sizeof(p->name));
  p->cwd = namei("/");

  // this assignment to p->state lets other cores
  // run this process. the acquire forces the above
  // writes to be visible, and the lock is also needed
  // because the assignment might not be atomic.
  acquire(&ptable.lock);

  p->state = RUNNABLE;

  release(&ptable.lock);
}

// Grow current process's memory by n bytes.
// Return 0 on success, -1 on failure.
int
growproc(int n)
{
  uint sz;
  struct proc *curproc = myproc();

  sz = curproc->sz;
  if(n > 0){
    if((sz = allocuvm(curproc->pgdir, sz, sz + n)) == 0)
      return -1;
  } else if(n < 0){
    if((sz = deallocuvm(curproc->pgdir, sz, sz + n)) == 0)
      return -1;
  }
  curproc->sz = sz;
  switchuvm(curproc);
  return 0;
}

// Create a new process copying p as the parent.
// Sets up stack to return as if from system call.
// Caller must set state of returned proc to RUNNABLE.
int
fork(void)
{
  int i, pid;
  struct proc *np;
  struct proc *curproc = myproc();

  // Allocate process.
  if((np = allocproc()) == 0){
    return -1;
  }

  // Copy process state from proc.
  if((np->pgdir = copyuvm(curproc->pgdir, curproc->sz)) == 0){
    kfree(np->kstack);
    np->kstack = 0;
    np->state = UNUSED;
    return -1;
  }
  np->sz = curproc->sz;
  np->parent = curproc;
  *np->tf = *curproc->tf;

  // PA-AQPA: inherit priority from parent
  np->priority = curproc->priority;
  np->base_priority = curproc->base_priority;

  // Clear %eax so that fork returns 0 in the child.
  np->tf->eax = 0;

  for(i = 0; i < NOFILE; i++)
    if(curproc->ofile[i])
      np->ofile[i] = filedup(curproc->ofile[i]);
  np->cwd = idup(curproc->cwd);

  safestrcpy(np->name, curproc->name, sizeof(curproc->name));

  pid = np->pid;

  acquire(&ptable.lock);

  np->state = RUNNABLE;

  release(&ptable.lock);

  return pid;
}

// Exit the current process.  Does not return.
// An exited process remains in the zombie state
// until its parent calls wait() to find out it exited.
void
exit(void)
{
  struct proc *curproc = myproc();
  struct proc *p;
  int fd;

  if(curproc == initproc)
    panic("init exiting");

  // Close all open files.
  for(fd = 0; fd < NOFILE; fd++){
    if(curproc->ofile[fd]){
      fileclose(curproc->ofile[fd]);
      curproc->ofile[fd] = 0;
    }
  }

  begin_op();
  iput(curproc->cwd);
  end_op();
  curproc->cwd = 0;

  acquire(&ptable.lock);

  // Parent might be sleeping in wait().
  wakeup1(curproc->parent);

  // Pass abandoned children to init.
  for(p = ptable.proc; p < &ptable.proc[NPROC]; p++){
    if(p->parent == curproc){
      p->parent = initproc;
      if(p->state == ZOMBIE)
        wakeup1(initproc);
    }
  }

  // Jump into the scheduler, never to return.
  curproc->state = ZOMBIE;
  sched();
  panic("zombie exit");
}

// Wait for a child process to exit and return its pid.
// Return -1 if this process has no children.
int
wait(void)
{
  struct proc *p;
  int havekids, pid;
  struct proc *curproc = myproc();
  
  acquire(&ptable.lock);
  for(;;){
    // Scan through table looking for exited children.
    havekids = 0;
    for(p = ptable.proc; p < &ptable.proc[NPROC]; p++){
      if(p->parent != curproc)
        continue;
      havekids = 1;
      if(p->state == ZOMBIE){
        // Found one.
        pid = p->pid;
        kfree(p->kstack);
        p->kstack = 0;
        freevm(p->pgdir);
        p->pid = 0;
        p->parent = 0;
        p->name[0] = 0;
        p->killed = 0;
        p->state = UNUSED;
        release(&ptable.lock);
        return pid;
      }
    }

    // No point waiting if we don't have any children.
    if(!havekids || curproc->killed){
      release(&ptable.lock);
      return -1;
    }

    // Wait for children to exit.  (See wakeup1 call in proc_exit.)
    sleep(curproc, &ptable.lock);  //DOC: wait-sleep
  }
}

//PAGEBREAK: 42
// PA-AQPA Scheduler.
// Replaces the default xv6 round-robin scheduler.
void
scheduler(void)
{
  struct proc *p;
  struct proc *best;
  struct cpu *c = mycpu();
  int start_tick;
  c->proc = 0;
  
  for(;;){
    sti();

    acquire(&ptable.lock);

    // Phase 1: Aging — increment wait and apply boosts
    for(p = ptable.proc; p < &ptable.proc[NPROC]; p++){
      if(p->state == RUNNABLE){
        p->wait_ticks++;
        p->total_wait++;
        if(p->wait_ticks >= STARVATION_BOUND){
          p->priority = 0;
          p->wait_ticks = 0;
        } else if(p->wait_ticks >= AGING_THRESHOLD){
          p->priority -= AGING_BOOST;
          if(p->priority < 0) p->priority = 0;
          p->wait_ticks = 0;
        }
      }
    }

    // Phase 2: Select highest-priority RUNNABLE process
    best = 0;
    for(p = ptable.proc; p < &ptable.proc[NPROC]; p++){
      if(p->state != RUNNABLE)
        continue;
      if(best == 0 || p->priority < best->priority ||
         (p->priority == best->priority && p->wait_ticks > best->wait_ticks)){
        best = p;
      }
    }

    if(best != 0){
      // Phase 3: Compute quantum and dispatch
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

      // Phase 4: Update EWMA and apply decay
      {
        int actual_burst = ticks - start_tick;
        int cls;
        if(actual_burst < 1) actual_burst = 1;
        cls = priority_to_class(best->priority);
        if(ewma_burst[cls] == 0)
          ewma_burst[cls] = actual_burst;
        else
          ewma_burst[cls] = (EWMA_ALPHA_NUM * actual_burst +
                             (EWMA_ALPHA_DEN - EWMA_ALPHA_NUM) * ewma_burst[cls])
                            / EWMA_ALPHA_DEN;
        best->burst_estimate = ewma_burst[cls];
        best->last_burst = actual_burst;

        // Decay: drift priority back toward base
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

// Enter scheduler.  Must hold only ptable.lock
// and have changed proc->state. Saves and restores
// intena because intena is a property of this
// kernel thread, not this CPU. It should
// be proc->intena and proc->ncli, but that would
// break in the few places where a lock is held but
// there's no process.
void
sched(void)
{
  int intena;
  struct proc *p = myproc();

  if(!holding(&ptable.lock))
    panic("sched ptable.lock");
  if(mycpu()->ncli != 1)
    panic("sched locks");
  if(p->state == RUNNING)
    panic("sched running");
  if(readeflags()&FL_IF)
    panic("sched interruptible");
  intena = mycpu()->intena;
  swtch(&p->context, mycpu()->scheduler);
  mycpu()->intena = intena;
}

// Give up the CPU for one scheduling round.
void
yield(void)
{
  acquire(&ptable.lock);  //DOC: yieldlock
  myproc()->state = RUNNABLE;
  sched();
  release(&ptable.lock);
}

// A fork child's very first scheduling by scheduler()
// will swtch here.  "Return" to user space.
void
forkret(void)
{
  static int first = 1;
  // Still holding ptable.lock from scheduler.
  release(&ptable.lock);

  if (first) {
    // Some initialization functions must be run in the context
    // of a regular process (e.g., they call sleep), and thus cannot
    // be run from main().
    first = 0;
    iinit(ROOTDEV);
    initlog(ROOTDEV);
  }

  // Return to "caller", actually trapret (see allocproc).
}

// Atomically release lock and sleep on chan.
// Reacquires lock when awakened.
void
sleep(void *chan, struct spinlock *lk)
{
  struct proc *p = myproc();
  
  if(p == 0)
    panic("sleep");

  if(lk == 0)
    panic("sleep without lk");

  // Must acquire ptable.lock in order to
  // change p->state and then call sched.
  // Once we hold ptable.lock, we can be
  // guaranteed that we won't miss any wakeup
  // (wakeup runs with ptable.lock locked),
  // so it's okay to release lk.
  if(lk != &ptable.lock){  //DOC: sleeplock0
    acquire(&ptable.lock);  //DOC: sleeplock1
    release(lk);
  }
  // Go to sleep.
  p->chan = chan;
  p->state = SLEEPING;

  sched();

  // Tidy up.
  p->chan = 0;

  // Reacquire original lock.
  if(lk != &ptable.lock){  //DOC: sleeplock2
    release(&ptable.lock);
    acquire(lk);
  }
}

//PAGEBREAK!
// Wake up all processes sleeping on chan.
// The ptable lock must be held.
static void
wakeup1(void *chan)
{
  struct proc *p;

  for(p = ptable.proc; p < &ptable.proc[NPROC]; p++)
    if(p->state == SLEEPING && p->chan == chan)
      p->state = RUNNABLE;
}

// Wake up all processes sleeping on chan.
void
wakeup(void *chan)
{
  acquire(&ptable.lock);
  wakeup1(chan);
  release(&ptable.lock);
}

// Kill the process with the given pid.
// Process won't exit until it returns
// to user space (see trap in trap.c).
int
kill(int pid)
{
  struct proc *p;

  acquire(&ptable.lock);
  for(p = ptable.proc; p < &ptable.proc[NPROC]; p++){
    if(p->pid == pid){
      p->killed = 1;
      // Wake process from sleep if necessary.
      if(p->state == SLEEPING)
        p->state = RUNNABLE;
      release(&ptable.lock);
      return 0;
    }
  }
  release(&ptable.lock);
  return -1;
}

//PAGEBREAK: 36
// Print a process listing to console.  For debugging.
// Runs when user types ^P on console.
// No lock to avoid wedging a stuck machine further.
void
procdump(void)
{
  static char *states[] = {
  [UNUSED]    "unused",
  [EMBRYO]    "embryo",
  [SLEEPING]  "sleep ",
  [RUNNABLE]  "runble",
  [RUNNING]   "run   ",
  [ZOMBIE]    "zombie"
  };
  int i;
  struct proc *p;
  char *state;
  uint pc[10];

  for(p = ptable.proc; p < &ptable.proc[NPROC]; p++){
    if(p->state == UNUSED)
      continue;
    if(p->state >= 0 && p->state < NELEM(states) && states[p->state])
      state = states[p->state];
    else
      state = "???";
    cprintf("%d %s %s", p->pid, state, p->name);
    if(p->state == SLEEPING){
      getcallerpcs((uint*)p->context->ebp+2, pc);
      for(i=0; i<10 && pc[i] != 0; i++)
        cprintf(" %p", pc[i]);
    }
    cprintf("\n");
  }
}

// ========================
// PA-AQPA System Calls
// ========================

int
sys_setpriority(void)
{
  int newpri;
  int oldpri;
  struct proc *curproc = myproc();
  if(argint(0, &newpri) < 0)
    return -1;
  if(newpri < 0 || newpri > 200)
    return -1;
  acquire(&ptable.lock);
  oldpri = curproc->priority;
  curproc->priority = newpri;
  curproc->base_priority = newpri;
  release(&ptable.lock);
  if(newpri > oldpri)
    yield();
  return oldpri;
}

int
sys_getpriority(void)
{
  return myproc()->priority;
}

int
sys_schedstat(void)
{
  struct proc *p;
  int i;
  char *states[] = { "UNUSED", "EMBRYO", "SLEEP", "RNBL", "RUN", "ZMBI" };

  cprintf("\n--- PA-AQPA Scheduler Statistics ---\n");
  cprintf("Global context switches: %d\n", global_context_switches);
  cprintf("EWMA bursts: ");
  for(i = 0; i < NUM_PRIORITY_CLASSES; i++)
    cprintf("[%d]=%d ", i, ewma_burst[i]);
  cprintf("\n\n");

  cprintf("%-5s %-6s %-4s %-4s %-6s %-5s %-5s %-4s %-4s %s\n",
          "PID", "STATE", "PRI", "BASE", "WAIT", "RESP", "BURST", "Q", "CS", "NAME");

  acquire(&ptable.lock);
  for(p = ptable.proc; p < &ptable.proc[NPROC]; p++){
    if(p->state == UNUSED)
      continue;
    cprintf("%-5d %-6s %-4d %-4d %-6d %-5d %-5d %-4d %-4d %s\n",
            p->pid, states[p->state], p->priority, p->base_priority,
            p->total_wait, p->response_time, p->last_burst,
            p->quantum_assigned, p->context_switches, p->name);
  }
  release(&ptable.lock);
  return 0;
}

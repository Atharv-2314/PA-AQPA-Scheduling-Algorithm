// sysproc.c additions for PA-AQPA
// Merge these functions into your existing sysproc.c

#include "types.h"
#include "x86.h"
#include "defs.h"
#include "date.h"
#include "param.h"
#include "memlayout.h"
#include "mmu.h"
#include "proc.h"

// sys_setpriority(int pid, int priority)
// Sets the scheduling priority of process with given pid
// priority must be in [0, PA_AQPA_PRIORITY_LEVELS-1]
int
sys_setpriority(void)
{
    int pid, priority;

    if(argint(0, &pid) < 0 || argint(1, &priority) < 0)
        return -1;

    if(priority < 0 || priority >= PA_AQPA_PRIORITY_LEVELS)
        return -1;

    return setpriority(pid, priority);
}

// sys_getpriority(int pid)
// Returns the current scheduling priority of process with given pid
int
sys_getpriority(void)
{
    int pid;

    if(argint(0, &pid) < 0)
        return -1;

    return getpriority(pid);
}

// sys_schedstat(void)
// Prints per-process scheduler statistics to console
int
sys_schedstat(void)
{
    return schedstat();
}

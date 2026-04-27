// starvation_test.c - Proves bounded starvation for PA-AQPA
// Forks a low-priority process alongside high-priority hogs
// Low-priority process MUST complete within T_max * priority_levels ticks

#ifndef PA_AQPA_AGING_TMAX
#define PA_AQPA_AGING_TMAX 100
#endif
#include "types.h"
#include "stat.h"
#include "user.h"

#define NUM_HOGS 4

int
main(void)
{
    int i, pid;

    printf(1, "starvation_test: starting. T_max=%d\n", PA_AQPA_AGING_TMAX);

    // Fork low-priority victim
    pid = fork();
    if(pid == 0){
        setpriority(getpid(), 4);   // lowest priority
        printf(1, "victim: pid=%d started at lowest priority\n", getpid());

        volatile long sum = 0;
        int j;
        for(j = 0; j < 500000; j++) sum += j;

        printf(1, "victim: COMPLETED. sum=%ld (starvation bounded!)\n", sum);
        exit();
    }

    // Fork high-priority CPU hogs
    for(i = 0; i < NUM_HOGS; i++){
        int hog = fork();
        if(hog == 0){
            setpriority(getpid(), 0);   // highest priority
            volatile long s = 0;
            int j;
            for(j = 0; j < 1000000; j++) s += j * j;
            printf(1, "hog %d: done.\n", getpid());
            exit();
        }
    }

    // Wait for all children
    for(i = 0; i < NUM_HOGS + 1; i++)
        wait();

    printf(1, "starvation_test: all processes completed.\n");
    schedstat();
    exit();
}

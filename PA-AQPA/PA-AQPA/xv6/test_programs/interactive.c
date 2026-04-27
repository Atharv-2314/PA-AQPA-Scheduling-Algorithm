// interactive.c - Simulates interactive process for PA-AQPA
// High priority, short bursts, frequent yields

#include "types.h"
#include "stat.h"
#include "user.h"

int
main(int argc, char *argv[])
{
    int priority = (argc > 1) ? atoi(argv[1]) : 0;  // highest priority
    int rounds   = (argc > 2) ? atoi(argv[2]) : 15;

    setpriority(getpid(), priority);
    printf(1, "interactive: pid=%d priority=%d\n", getpid(), priority);

    int i;
    for(i = 0; i < rounds; i++){
        sleep(1);   // short think time
        printf(1, "interactive: response %d\n", i);
    }

    printf(1, "interactive: done.\n");
    schedstat();
    exit();
}

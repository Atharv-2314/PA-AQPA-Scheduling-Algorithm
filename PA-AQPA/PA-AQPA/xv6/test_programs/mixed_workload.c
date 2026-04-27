// mixed_workload.c - Mixed CPU + I/O workload for PA-AQPA
// Alternates between compute bursts and sleeps

#include "types.h"
#include "stat.h"
#include "user.h"

int
main(int argc, char *argv[])
{
    int priority = (argc > 1) ? atoi(argv[1]) : 2;
    int rounds   = (argc > 2) ? atoi(argv[2]) : 10;

    setpriority(getpid(), priority);
    printf(1, "mixed: pid=%d priority=%d rounds=%d\n",
               getpid(), priority, rounds);

    int i, j;
    for(i = 0; i < rounds; i++){
        // CPU burst
        volatile long sum = 0;
        for(j = 0; j < 200000; j++)
            sum += j;

        // I/O burst
        sleep(3);
        printf(1, "mixed: round %d done. sum=%ld\n", i, sum);
    }

    printf(1, "mixed: done.\n");
    schedstat();
    exit();
}

// cpu_bound.c - CPU-bound workload test for PA-AQPA
// Runs a tight compute loop; exercises high-priority quantum sizing

#include "types.h"
#include "stat.h"
#include "user.h"

int
main(int argc, char *argv[])
{
    int priority = (argc > 1) ? atoi(argv[1]) : 2;
    int iters    = (argc > 2) ? atoi(argv[2]) : 1000000;

    setpriority(getpid(), priority);
    printf(1, "cpu_bound: pid=%d priority=%d iters=%d\n",
               getpid(), priority, iters);

    volatile long sum = 0;
    int i;
    for(i = 0; i < iters; i++)
        sum += i * i;

    printf(1, "cpu_bound: done. sum=%ld\n", sum);
    schedstat();
    exit();
}

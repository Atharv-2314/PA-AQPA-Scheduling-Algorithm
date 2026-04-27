// io_bound.c - I/O-bound workload test for PA-AQPA
// Sleeps frequently; exercises short-quantum low-priority behavior

#include "types.h"
#include "stat.h"
#include "user.h"

int
main(int argc, char *argv[])
{
    int priority = (argc > 1) ? atoi(argv[1]) : 3;
    int rounds   = (argc > 2) ? atoi(argv[2]) : 20;

    setpriority(getpid(), priority);
    printf(1, "io_bound: pid=%d priority=%d rounds=%d\n",
               getpid(), priority, rounds);

    int i;
    for(i = 0; i < rounds; i++){
        sleep(5);   // simulate I/O wait
        printf(1, "io_bound: woke up round %d\n", i);
    }

    printf(1, "io_bound: done.\n");
    schedstat();
    exit();
}

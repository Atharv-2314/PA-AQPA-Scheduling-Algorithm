#include "types.h"
#include "stat.h"
#include "user.h"

int main(void)
{
    printf(1, "schedstat working (no syscall yet)\n");

    if(schedstat() < 0){
        printf(1, "schedstat syscall failed\n");
    }

    exit();
}

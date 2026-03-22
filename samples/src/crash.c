#include "lib.h"

// Ring 3 null-pointer write: MyOS's page fault handler halts the machine.
void main() {
    print("about to null-deref\n");
    *(volatile int *)0 = 1;
    exit(0);
}

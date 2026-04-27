# Makefile for xv6 with PA-AQPA Scheduler
# Authors: Aarnav Arya, Adwit Gautam, Akshaj Singh, Atharv Kumar

U=_

OBJS = \
	bio.o\
	console.o\
	exec.o\
	file.o\
	fs.o\
	ide.o\
	ioapic.o\
	kalloc.o\
	kbd.o\
	lapic.o\
	log.o\
	main.o\
	mp.o\
	picirq.o\
	pipe.o\
	proc.o\
	sleeplock.o\
	spinlock.o\
	string.o\
	swtch.o\
	syscall.o\
	sysfile.o\
	sysproc.o\
	trapasm.o\
	trap.o\
	uart.o\
	vectors.o\
	vm.o\
	pa_aqpa.o

# Cross-compiling tools
ifndef TOOLPREFIX
TOOLPREFIX := $(shell if i386-jos-elf-objdump -i 2>&1 | grep '^elf32-i386$$' >/dev/null 2>&1; \
	then echo 'i386-jos-elf-'; \
	elif objdump -i 2>&1 | grep 'elf32-i386' >/dev/null 2>&1; \
	then echo ''; \
	else echo "***" 1>&2; \
	echo "*** Error: Couldn't find an i386 version of GCC/binutils." 1>&2; \
	exit 1; fi)
endif

QEMU = qemu-system-i386

CC = $(TOOLPREFIX)gcc
AS = $(TOOLPREFIX)gas
LD = $(TOOLPREFIX)ld
OBJCOPY = $(TOOLPREFIX)objcopy
OBJDUMP = $(TOOLPREFIX)objdump

# Compiler flags
CFLAGS = -fno-pic -static -fno-builtin -fno-strict-aliasing -O2 -Wall -MD -ggdb -m32 -Werror -fno-omit-frame-pointer
CFLAGS += $(shell $(CC) -fno-stack-protector -E -x c /dev/null >/dev/null 2>&1 && echo -fno-stack-protector)

# PA-AQPA flags
CFLAGS += -DPA_AQPA_EWMA_ALPHA=7
CFLAGS += -DPA_AQPA_QUANTUM_MIN=1
CFLAGS += -DPA_AQPA_QUANTUM_MAX=20
CFLAGS += -DPA_AQPA_PRIORITY_LEVELS=5
CFLAGS += -DPA_AQPA_AGING_TMAX=100
CFLAGS += -DPA_AQPA_DECAY_FACTOR=2

ASFLAGS = -m32 -gdwarf-2 -Wa,-divide
LDFLAGS += -m $(shell $(LD) -V | grep elf_i386 2>/dev/null | head -1)

# User library
ULIB = ulib.o usys.o printf.o umalloc.o

# User programs
UPROGS=\
	$U/_cat\
	$U/_echo\
	$U/_forktest\
	$U/_grep\
	$U/_init\
	$U/_kill\
	$U/_ln\
	$U/_ls\
	$U/_mkdir\
	$U/_rm\
	$U/_sh\
	$U/_stressfs\
	$U/_usertests\
	$U/_wc\
	$U/_zombie\
	$U/_schedstat\
	$U/_cpu_bound\
	$U/_io_bound\
	$U/_mixed_workload\
	$U/_interactive\
	$U/_starvation_test\
	$U/_schedstat_cmd

# Kernel build
kernel: $(OBJS) entry.o entryother initcode kernel.ld
	$(LD) $(LDFLAGS) -T kernel.ld -o kernel entry.o $(OBJS) -b binary initcode entryother
	$(OBJDUMP) -S kernel > kernel.asm
	$(OBJDUMP) -t kernel | sed '1,/SYMBOL TABLE/d; s/ .* / /; /^$$/d' > kernel.sym

# User program build rule (IMPORTANT FIX)
$U/_%: %.o $(ULIB)
	$(LD) $(LDFLAGS) -o $@ $^
	$(OBJDUMP) -S $@ > $*.asm
	$(OBJDUMP) -t $@ | sed '1,/SYMBOL TABLE/d; s/ .* / /; /^$$/d' > $*.sym

# Compilation rules
%.o: %.c
	$(CC) $(CFLAGS) -c -o $@ $<

%.o: %.S
	$(CC) $(ASFLAGS) -c -o $@ $<

# FS image
fs.img: mkfs README $(UPROGS)
	./mkfs fs.img README $(UPROGS)

# Boot + images (unchanged xv6 logic)
bootblock: bootasm.S bootmain.c
	$(CC) $(CFLAGS) -c bootmain.c
	$(CC) $(CFLAGS) -c bootasm.S
	$(LD) $(LDFLAGS) -N -e start -Ttext 0x7C00 -o bootblock.o bootasm.o bootmain.o
	$(OBJCOPY) -S -O binary bootblock.o bootblock
	./sign.pl bootblock

initcode: initcode.S
	$(CC) $(CFLAGS) -c initcode.S
	$(LD) $(LDFLAGS) -N -e start -Ttext 0 -o initcode.out initcode.o
	$(OBJCOPY) -S -O binary initcode.out initcode

xv6.img: bootblock kernel
	dd if=/dev/zero of=xv6.img count=10000
	dd if=bootblock of=xv6.img conv=notrunc
	dd if=kernel of=xv6.img seek=1 conv=notrunc

QEMUOPTS = -drive file=xv6.img,index=0,media=disk,format=raw -smp 2 -m 512

qemu: fs.img xv6.img
	$(QEMU) -serial mon:stdio $(QEMUOPTS) -drive file=fs.img,index=1,media=disk,format=raw

clean:
	rm -f *.o *.d *.asm *.sym *.img kernel fs.img xv6.img bootblock initcode.out

-include *.d

.PHONY: clean qemu

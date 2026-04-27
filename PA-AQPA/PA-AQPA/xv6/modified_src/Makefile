# Makefile for xv6 with PA-AQPA Scheduler
# PA-AQPA: Priority-Aware Adaptive Quantum with Predictive Aging
# Authors: Aarnav Arya, Adwit Gautam, Akshaj Singh, Atharv Kumar

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

# PA-AQPA scheduler object
OBJS += pa_aqpa.o

# Cross-compiling tools
ifndef TOOLPREFIX
TOOLPREFIX := $(shell if i386-jos-elf-objdump -i 2>&1 | grep '^elf32-i386$$' >/dev/null 2>&1; \
	then echo 'i386-jos-elf-'; \
	elif objdump -i 2>&1 | grep 'elf32-i386' >/dev/null 2>&1; \
	then echo ''; \
	else echo "***" 1>&2; \
	echo "*** Error: Couldn't find an i386 version of GCC/binutils." 1>&2; \
	echo "*** Is the right version of gcc installed?" 1>&2; \
	echo "***" 1>&2; exit 1; fi)
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

# PA-AQPA specific flags (tune these parameters)
CFLAGS += -DPA_AQPA_EWMA_ALPHA=7        # EWMA alpha (out of 10): higher = more reactive
CFLAGS += -DPA_AQPA_QUANTUM_MIN=1       # Minimum quantum in ticks
CFLAGS += -DPA_AQPA_QUANTUM_MAX=20      # Maximum quantum in ticks
CFLAGS += -DPA_AQPA_PRIORITY_LEVELS=5  # Number of priority levels (0=highest, 4=lowest)
CFLAGS += -DPA_AQPA_AGING_TMAX=100     # Starvation bound in ticks
CFLAGS += -DPA_AQPA_DECAY_FACTOR=2     # Exponential decay divisor after CPU grant

ASFLAGS = -m32 -gdwarf-2 -Wa,-divide
LDFLAGS += -m $(shell $(LD) -V | grep elf_i386 2>/dev/null | head -1)

# Bootloader
bootblock: bootasm.S bootmain.c
	$(CC) $(CFLAGS) -fno-pic -O -nostdinc -I. -c bootmain.c
	$(CC) $(CFLAGS) -fno-pic -nostdinc -I. -c bootasm.S
	$(LD) $(LDFLAGS) -N -e start -Ttext 0x7C00 -o bootblock.o bootasm.o bootmain.o
	$(OBJDUMP) -S bootblock.o > bootblock.asm
	$(OBJCOPY) -S -O binary -j .text bootblock.o bootblock
	./sign.pl bootblock

entryother: entryother.S
	$(CC) $(CFLAGS) -fno-pic -nostdinc -I. -c entryother.S
	$(LD) $(LDFLAGS) -N -e start -Ttext 0x7000 -o bootblockother.o entryother.o
	$(OBJCOPY) -S -O binary -j .text bootblockother.o entryother
	$(OBJDUMP) -S bootblockother.o > entryother.asm

initcode: initcode.S
	$(CC) $(CFLAGS) -nostdinc -I. -c initcode.S
	$(LD) $(LDFLAGS) -N -e start -Ttext 0 -o initcode.out initcode.o
	$(OBJCOPY) -S -O binary initcode.out initcode
	$(OBJDUMP) -S initcode.o > initcode.asm

kernel: $(OBJS) entry.o entryother initcode kernel.ld
	$(LD) $(LDFLAGS) -T kernel.ld -o kernel entry.o $(OBJS) -b binary initcode entryother
	$(OBJDUMP) -S kernel > kernel.asm
	$(OBJDUMP) -t kernel | sed '1,/SYMBOL TABLE/d; s/ .* / /; /^$$/d' > kernel.sym

xv6.img: bootblock kernel
	dd if=/dev/zero of=xv6.img count=10000
	dd if=bootblock of=xv6.img conv=notrunc
	dd if=kernel of=xv6.img seek=1 conv=notrunc

xv6memfs.img: bootblock kernelmemfs
	dd if=/dev/zero of=xv6memfs.img count=10000
	dd if=bootblock of=xv6memfs.img conv=notrunc
	dd if=kernelmemfs of=xv6memfs.img seek=1 conv=notrunc

# QEMU settings
QEMUOPTS = -drive file=xv6.img,index=0,media=disk,format=raw -smp 2 -m 512 $(QEMUEXTRA)

# Standard QEMU launch (with display)
qemu: fs.img xv6.img
	$(QEMU) -serial mon:stdio $(QEMUOPTS) -drive file=fs.img,index=1,media=disk,format=raw

# No-display QEMU launch (for automated testing / SSH sessions)
qemu-nox: fs.img xv6.img
	$(QEMU) -nographic $(QEMUOPTS) -drive file=fs.img,index=1,media=disk,format=raw

# GDB debug session (no display)
qemu-nox-gdb: fs.img xv6.img .gdbinit
	$(QEMU) -nographic $(QEMUOPTS) -drive file=fs.img,index=1,media=disk,format=raw -S -gdb tcp::26000

# GDB debug session (with display)
qemu-gdb: fs.img xv6.img .gdbinit
	@echo "*** Now run 'gdb'." 1>&2
	$(QEMU) -serial mon:stdio $(QEMUOPTS) -drive file=fs.img,index=1,media=disk,format=raw -S -gdb tcp::26000

# Filesystem image
UPROGS=\
	_cat\
	_echo\
	_forktest\
	_grep\
	_init\
	_kill\
	_ln\
	_ls\
	_mkdir\
	_rm\
	_sh\
	_stressfs\
	_usertests\
	_wc\
	_zombie\
	_schedstat\
	_cpu_bound\
	_io_bound\
	_mixed_workload\
	_interactive\
	_starvation_test\

fs.img: mkfs README $(UPROGS)
	./mkfs fs.img README $(UPROGS)

# Build rules
_%: %.o $(ULIB)
	$(LD) $(LDFLAGS) -N -e main -Ttext 0 -o $@ $^
	$(OBJDUMP) -S $@ > $*.asm
	$(OBJDUMP) -t $@ | sed '1,/SYMBOL TABLE/d; s/ .* / /; /^$$/d' > $*.sym

%.o: %.c
	$(CC) $(CFLAGS) -c -o $@ $

%.o: %.S
	$(CC) $(ASFLAGS) -c -o $@ $

# User library
ULIB = ulib.o usys.o printf.o umalloc.o

clean:
	rm -f *.tex *.dvi *.idx *.aux *.log *.ind *.ilg \
	*.o *.d *.asm *.sym vectors.S bootblock entryother \
	initcode initcode.out kernel xv6.img fs.img kernelmemfs \
	xv6memfs.img .gdbinit \
	$(UPROGS)

# Dependency tracking
-include *.d

.PHONY: clean qemu qemu-nox qemu-gdb qemu-nox-gdb

#!/bin/sh
# Build the sample job binaries against MyOS's user library. Reads from the
# MyOS repo, writes only into samples/.
set -eu
cd "$(dirname "$0")"
REPO="${MYOS_REPO:-/Users/jihoonchoi/Desktop/my-os}"
CC="${CC:-x86_64-elf-gcc}"
FLAGS="-ffreestanding -nostdlib -m32 -g -Wl,-m,elf_i386 -mno-sse -mno-sse2 -mno-mmx"

for name in crash fail spin; do
    $CC $FLAGS -T "$REPO/programs/linker.ld" -I "$REPO/programs" \
        "src/$name.c" "$REPO/programs/lib.c" -o "$name.elf"
done
cp "$REPO/programs/hello.elf" hello.elf
cp "$REPO/programs/producer_consumer.elf" producer_consumer.elf

# ELF with its magic broken: passes the size check, fails MyOS's loader.
python3 -c "
d = bytearray(open('hello.elf', 'rb').read()); d[1] = 0
open('corrupt.elf', 'wb').write(d)"

# One byte over the 48 x 512 SimpleFS limit.
python3 -c "
d = bytearray(open('hello.elf', 'rb').read())
open('too_big.elf', 'wb').write(d + bytes(48 * 512 + 1 - len(d)))"

ls -l *.elf

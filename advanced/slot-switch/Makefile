CC_AARCH64 ?= aarch64-linux-gnu-gcc
CFLAGS ?= -std=gnu11 -O2 -Wall -Wextra -Werror -fstack-protector-strong -D_FORTIFY_SOURCE=2

.PHONY: all test
all: bin/slot-helper

bin/slot-helper: slot_helper.c hash.c hash.h profile_format.h
	mkdir -p bin
	$(CC_AARCH64) $(CFLAGS) -static slot_helper.c hash.c -o $@

test:
	python3 test_slot_helper.py

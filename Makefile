# minibox Makefile (Phase 0: placeholder build)
CC      := gcc
CFLAGS  := -std=c11 -Wall -Wextra -Werror -g
BUILD   := build

.PHONY: all clean check-env rootfs

all:
	@echo "minibox: nothing to build yet (placeholder)."

check-env:
	@./scripts/check_env.sh

rootfs:
	@./scripts/get_rootfs.sh

clean:
	rm -rf $(BUILD)

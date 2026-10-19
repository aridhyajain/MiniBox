# minibox Makefile
CC      := gcc
CFLAGS  := -std=c11 -Wall -Wextra -Werror -g -Iinclude -MMD -MP
BUILD   := build
TARGET  := minibox
SRCS    := src/main.c src/container.c src/rootfs.c src/net.c src/cgroup.c
OBJS    := $(SRCS:src/%.c=$(BUILD)/%.o)

.PHONY: all clean check-env rootfs test test-phase3

all: $(TARGET)

$(TARGET): $(OBJS)
	$(CC) $(CFLAGS) -o $@ $(OBJS)

$(BUILD)/%.o: src/%.c | $(BUILD)
	$(CC) $(CFLAGS) -c -o $@ $<

$(BUILD):
	mkdir -p $(BUILD)

check-env:
	@./scripts/check_env.sh

rootfs:
	@./scripts/get_rootfs.sh

# Tests need root (namespaces, mounts); make never runs sudo itself.
test: $(TARGET)
	@echo "Tests need root. Run:"
	@echo "  sudo tests/phase1_test.sh"
	@echo "  sudo tests/phase2_test.sh"
	@echo "  sudo tests/phase3_test.sh"

test-phase3: $(TARGET)
	@echo "Phase 3 tests need root. Run:"
	@echo "  sudo tests/phase3_test.sh"

clean:
	rm -rf $(BUILD) $(TARGET)

-include $(OBJS:.o=.d)
